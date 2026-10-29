"""Detached post-processing execution and durable result publication."""

from __future__ import annotations

from dataclasses import replace
import logging
import threading
from types import SimpleNamespace

from transbridge.application.io.publish import ImmediateCommitGuard

from ._postprocess_tool_runtime import execute_postprocess_task, resolve_postprocess_request
from .base import ExecutionContext, ToolResult
from .diagnostics import technical_error
from .input_prerequisites import SourceInputRequired, source_input_failure
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata
from .translation_task_results import AssistantTaskResults

logger = logging.getLogger(__name__)


def launch_postprocess_task(args, ctx, *, report_directory, report_callback, manager=None):
    """Capture live inputs on their owner thread; prepare durable storage in the worker."""
    dispatch = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)

    def capture():
        collection = dispatch.collection
        if not collection:
            raise ValueError("当前没有加载翻译集合")
        request = resolve_postprocess_request(args, dispatch, collection)
        # Every currently resolved proofreading phase consumes original text.
        # Resolve the actual selection once; configuration errors retain their existing precedence.
        missing = source_input_failure(dispatch, request.entries) if request.stages else None
        if missing is not None:
            raise SourceInputRequired(missing)
        task = AssistantTaskResults.capture(
            dispatch,
            collection,
            request.entries,
            request.effective_config,
            request.terminology_binding,
            mode="postprocess",
            request_options=request.metadata,
        )
        request = replace(
            request,
            entries=task.task.postprocess_entries,
            effective_config=task.config,
            terminology_binding=task.terminology,
        )
        return task, request

    task = None
    task_id = None
    try:
        task, request = dispatch.safe_mutate_wait(capture)
        manager = manager or TaskManager()
        stop = threading.Event()
        task_id = manager.register(
            stop_event=stop,
            metadata=task_metadata(
                dispatch,
                {**request.metadata, "result_id": task.result_id, "input_ref": task.result_id},
            ),
        )
        handle = manager.get_handle(task_id)
        manager.start_thread(
            task_id,
            lambda: execute_task(
                task,
                request,
                lambda: report_directory(task.report_context),
                report_callback,
                manager,
                task_id,
                handle,
            ),
        )
    except SourceInputRequired as exc:
        return exc.result
    except Exception as exc:
        if task is not None:
            task.pending_state, task.pending_error = "failed", technical_error(exc)
            task.persistence_error = technical_error(exc)
        if task_id is not None:
            manager.notify_finished(task_id, False, technical_error(exc), {"result_id": task.result_id})
        return ToolResult.fail(
            technical_error(exc),
            data={"result_id": task.result_id} if task is not None else None,
            error_code="TASK_PREPARATION_FAILED",
        )
    return ToolResult.ok(
        f"后处理已启动 (profile={request.profile}, strategy={request.strategy}, entries={len(request.entries)})",
        data={
            "task_id": task_id,
            "run_id": manager.get_status(task_id).get("run_id", ""),
            "result_id": task.result_id,
            "entry_count": len(request.entries),
            **request.metadata,
            "phases": list(request.stages),
        },
    )


def execute_task(task, request, directory, report_callback, manager, task_id, handle):
    """Save validated report candidates before any merge into the active version."""
    error, warning, merge = "", "", {}
    completion = {}
    cancelled = False
    try:
        task.prepare()
        task.store.update_metadata(task.result_id, state="running")
        directory = directory() if callable(directory) else directory
        try:
            result = execute_postprocess_task(
                request,
                ctx=SimpleNamespace(esp_path=task.esp_path, request_context=task.context),
                collection=task.task.collection,
                task_id=task.result_id,
                stop_event=handle.stop_event,
                pause_event=handle.pause_event,
                report_directory=directory,
                commit_guard=ImmediateCommitGuard(task.result_id),
            )
            cancelled = result.cancelled or handle.stop_event.is_set()
            task.ledger.ingest_postprocess(result.report_snapshot, cancelled=cancelled, expected_phases=request.stages)
            report_callback(result.report_data, task.ctx)
            completion = result.completion_data
            if result.report_data["outcome"] == "failed":
                error = "后处理未完整完成，请检查质量报告中的诊断。"
        except Exception as exc:
            logger.exception("后处理执行失败")
            error = technical_error(exc)
        cancelled = cancelled or handle.stop_event.is_set()
        succeeded = sum(row.status == "succeeded" for row in task.ledger.entries.values())
        failed = len(task.ledger.entries) - succeeded
        state = (
            "cancelled" if cancelled else "partial" if succeeded and failed else "completed" if succeeded else "failed"
        )
        task.persist(state=state, error=error)
        if succeeded and not cancelled:
            try:
                merge, warning = task.merge(execution=handle.execution)
            except Exception as exc:
                error = f"成果已保存，合入未完成：{technical_error(exc)}"
                task.store.update_metadata(task.result_id, merge_error=error)
        data = {
            **completion,
            "result_id": task.result_id,
            "state": state,
            "success_count": succeeded,
            "failed_count": failed,
            "merge": merge,
            "warning": warning,
            "error": error,
        }
        manager.update_progress(task_id, data)
        if cancelled:
            manager.set_status(task_id, "cancelled")
            manager.notify_finished(task_id, False, "任务已停止，已验证成果已保存，可查询并重新合入。", data)
        else:
            manager.notify_finished(task_id, state != "failed", error or "后处理成果已保存。", data)
    except Exception as exc:
        logger.exception("后处理成果准备或保存失败，停止自动合入")
        if task.pending_state == "preparing":
            task.pending_state, task.pending_error = "failed", technical_error(exc)
            task.persistence_error = technical_error(exc)
        data = {"result_id": task.result_id, "error": technical_error(exc)}
        manager.update_progress(task_id, data)
        manager.notify_finished(task_id, False, f"成果保存失败，未自动合入：{technical_error(exc)}", data)
    finally:
        task.store.release()
