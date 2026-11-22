# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Execute detached assistant translation work and settle durable results."""

from __future__ import annotations

import logging
import threading

from transbridge.ai_translator.project_terminology_runtime import capture_terminology_owner
from transbridge.application.projects.target_context import bound_paratranz_project

from .base import ToolResult
from .diagnostics import technical_error
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata
from .terminology_run import freeze_terminology_binding
from .translation_task_results import AssistantTaskResults

logger = logging.getLogger(__name__)


def launch_translation_task(
    ctx, collection, targets, config, *, mode, profile=None, intensity="medium", strategy="proofread", manager=None
):
    """Freeze inputs and create the manifest before the runtime starts any model work."""
    task = None
    task_id = None
    try:
        from .types import ExecutionContext

        dispatch = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)

        def capture():
            remote = bound_paratranz_project(ctx)
            connection = ctx.config.copy_for_execution() if remote else None
            terminology_owner = capture_terminology_owner(ctx)
            task = AssistantTaskResults.capture(
                ctx,
                collection,
                targets,
                config,
                None,
                mode=mode,
                request_options={
                    "intensity": intensity,
                    "strategy": strategy,
                    **({"profile": profile} if profile else {}),
                },
            )
            return task, connection, remote["id"] if remote else None, terminology_owner

        task, remote_config, remote_project, terminology_owner = dispatch.safe_mutate_wait(capture)
        manager = manager or TaskManager()
        stop = threading.Event()
        task_id = manager.register(
            stop_event=stop,
            metadata=task_metadata(
                ctx,
                {
                    "mode": mode,
                    "type": "polish" if mode == "polish" else "translation",
                    "result_id": task.result_id,
                    "input_ref": task.result_id,
                    "input_fingerprint": task.record["config_digest"],
                },
            ),
        )
        handle = manager.get_handle(task_id)
        manager.start_thread(
            task_id,
            lambda: execute_task(task, manager, task_id, handle, remote_config, remote_project, terminology_owner),
        )
    except Exception as exc:
        if task is not None:
            try:
                task.persist(state="failed", error=technical_error(exc))
            except (OSError, ValueError):
                logger.exception("记录助手任务准备失败状态失败")
            finally:
                task.store.release()
        if task_id is not None:
            manager.notify_finished(task_id, False, technical_error(exc), {"result_id": task.result_id})
        return ToolResult.fail(
            technical_error(exc),
            data={"result_id": task.result_id} if task is not None else None,
            error_code="TASK_PREPARATION_FAILED",
        )
    return ToolResult.ok(
        f"{'润色' if mode == 'polish' else '翻译'}任务已启动 (mode={mode})",
        data={
            "task_id": task_id,
            "run_id": manager.get_status(task_id).get("run_id", ""),
            "result_id": task.result_id,
            "mode": mode,
            "entry_count": len(task.task.entries),
            "strategy": strategy,
            "intensity": intensity,
        },
    )


def execute_task(task, manager, task_id, handle, remote_config, remote_project, terminology_owner=None):
    outcomes = ()
    error, merge, warning = "", {}, ""
    stop = handle.stop_event
    try:
        if terminology_owner is not None:
            task.terminology = freeze_terminology_binding(terminology_owner)
            if task.terminology.snapshot_ref is not None:
                task.record["reference_snapshot"] = task.terminology.snapshot_ref.to_dict()
        task.prepare()
        task.store.update_metadata(task.result_id, state="running")
        outcomes = task.app.translation_task_executor(
            source=task.task,
            config=task.config,
            mode=task.record["mode"],
            owner=task.context,
            result_id=task.result_id,
            directory=task.store.run_directory(task.result_id),
            terminology=task.terminology,
            remote_config=remote_config,
            remote_project=remote_project,
            stop_event=stop,
            pause_event=handle.pause_event,
            progress_callback=lambda source, stage, current, total, message: manager.update_progress(
                task_id, {"source": source, "stage": stage, "current": current, "total": total, "message": message}
            ),
        )
        error = "；".join(outcome.error for outcome in outcomes if outcome.error)
    except Exception as exc:
        logger.exception("助手 AI 任务执行失败，保留已验证成果")
        error = technical_error(exc)
    try:
        cancelled = stop.is_set()
        task.ledger.ingest(outcomes, set(task.ledger.entries), cancelled=cancelled)
        succeeded = sum(row.status == "succeeded" for row in task.ledger.entries.values())
        failed = sum(row.status == "failed" for row in task.ledger.entries.values())
        state = "cancelled" if cancelled else "failed" if not succeeded else "partial" if failed else "completed"
        # This write is the publication boundary. A failed durable write prevents merge.
        task.persist(state=state, error=error)
        if succeeded and not cancelled:
            try:
                merge, warning = task.merge(execution=handle.execution)
            except Exception as exc:
                error = f"成果已保存，合入未完成：{technical_error(exc)}"
                task.store.update_metadata(task.result_id, merge_error=error)
        if stop.is_set() and not cancelled:
            cancelled, state = True, "cancelled"
            task.persist(state=state, error=error)
        data = {
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
        elif state == "failed":
            manager.notify_finished(task_id, False, error or "任务未生成通过验证的成果。", data)
        else:
            manager.notify_finished(task_id, True, error or "任务成果已保存。", data)
    except Exception as exc:
        logger.exception("助手 AI 任务成果保存失败，停止自动合入")
        manager.update_progress(task_id, {"result_id": task.result_id, "error": technical_error(exc)})
        manager.notify_finished(
            task_id,
            False,
            f"成果保存失败，未自动合入：{technical_error(exc)}",
            {"result_id": task.result_id, "error": technical_error(exc)},
        )
    finally:
        task.store.release()
