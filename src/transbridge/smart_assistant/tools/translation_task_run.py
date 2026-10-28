"""Execute detached assistant translation work and settle durable results."""

from __future__ import annotations

from copy import deepcopy
import logging
import threading
from types import SimpleNamespace

from transbridge.application.translation.entry_alias import ai_entry_key

from .base import ToolResult
from .diagnostics import technical_error
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata
from .terminology_run import freeze_terminology_binding
from .translation_task_results import AssistantTaskResults

logger = logging.getLogger(__name__)


def paratranz_term_project_id(ctx):
    from transbridge.application.projects import ParaTranzTargetResolver, ParaTranzTargetStatus

    resolve = getattr(ctx, "resolve_paratranz_target", None)
    if callable(resolve):
        target = resolve()
    else:
        config = getattr(ctx, "config", None)
        endpoint = getattr(config, "base_url", None)
        if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
            endpoint = "https://paratranz.cn"
        user = getattr(ctx, "current_user", None)
        account_id = user.get("id") if isinstance(user, dict) else getattr(config, "user_id", None)
        if not isinstance(account_id, int) or isinstance(account_id, bool) or account_id <= 0:
            account_id = None
        target = ParaTranzTargetResolver().resolve(
            binding=getattr(ctx, "paratranz_binding", None),
            binding_revision=getattr(ctx, "project_revision", None),
            endpoint=endpoint,
            account_user_id=account_id,
        )
    return (
        target.project_id
        if target.status
        in {
            ParaTranzTargetStatus.UNVERIFIED,
            ParaTranzTargetStatus.AVAILABLE,
        }
        else None
    )


def launch_translation_task(
    ctx, collection, targets, config, *, mode, intensity="medium", strategy="proofread", manager=None
):
    """Freeze inputs and create the manifest before the runtime starts any model work."""
    task = None
    task_id = None
    try:
        from .types import ExecutionContext

        dispatch = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)

        def capture():
            terminology = freeze_terminology_binding(ctx)
            task = AssistantTaskResults.capture(
                ctx,
                collection,
                targets,
                config,
                terminology,
                mode=mode,
                request_options={"intensity": intensity, "strategy": strategy},
            )
            remote = SimpleNamespace(
                config=deepcopy(getattr(ctx, "config", None)),
                current_user=deepcopy(getattr(ctx, "current_user", None)),
                paratranz_binding=deepcopy(getattr(ctx, "paratranz_binding", None)),
                project_revision=getattr(ctx, "project_revision", None),
            )
            return task, remote.config, paratranz_term_project_id(remote)

        task, remote_config, remote_project = dispatch.safe_mutate_wait(capture)
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
            lambda: execute_task(
                task,
                manager,
                task_id,
                handle,
                remote_config,
                remote_project,
                intensity=intensity,
                strategy=strategy,
            ),
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


def _capture_references(task, remote_config, remote_project):
    from transbridge.ai_translator.project_reference_snapshot import capture_project_reference
    from transbridge.ai_translator.task_reference_snapshot import TaskReferenceStore
    from transbridge.ai_translator.term_database import TermDatabaseManager

    task.references = TaskReferenceStore(
        task.result_id,
        root=task.store.root / "references",
        identity=(*task.identity, task.config.target_lang),
    )
    task.references.capture_published(capture_project_reference(task.terminology))
    client = None
    try:
        if remote_project is not None and remote_config is not None and getattr(remote_config, "token", None):
            from transbridge.paratranz import ParatranzClient

            client = ParatranzClient(remote_config)
        manager = TermDatabaseManager(
            config=task.config,
            esp_path=task.esp_path,
            paratranz_client=client,
            project_id=remote_project,
            reference_store=task.references,
            reference_source=task.task.key,
            **task.terminology.term_database_kwargs(),
        )
        manager.load_all()
    finally:
        if client is not None:
            client.close()


def execute_task(task, manager, task_id, handle, remote_config, remote_project, *, intensity, strategy):
    outcome = SimpleNamespace(task=task.task, translation=None, polish={}, error="")
    client = runtime = None
    error, merge, warning = "", {}, ""
    stop = handle.stop_event
    try:
        task.prepare()
        _capture_references(task, remote_config, remote_project)
        task.store.update_metadata(task.result_id, state="running")
        if remote_project is not None and remote_config is not None and getattr(remote_config, "token", None):
            from transbridge.paratranz import ParatranzClient

            client = ParatranzClient(remote_config)
        if task.task.translate_entries:
            from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig

            translator = AutoTranslator(
                TranslatorConfig(llm_config=task.config, esp_path=task.esp_path, overwrite=False),
                client,
                remote_project,
                run_id_factory=lambda: task.result_id,
                reference_store=task.references,
                reference_source=task.task.key,
                **task.terminology.translator_kwargs(),
            )
            outcome.translation = translator.translate(
                collection=task.task.collection,
                target_entry_ids=[ai_entry_key(entry) for entry in task.task.translate_entries],
                progress_callback=lambda current, total, message, success, failed, _terms: manager.update_progress(
                    task_id,
                    {
                        "current": current,
                        "total": total,
                        "message": message,
                        "success_count": success,
                        "failed_count": failed,
                    },
                ),
                stop_event=stop,
                pause_event=handle.pause_event,
            )
        if task.task.polish_entries and not stop.is_set():
            from transbridge.ai_translator.term_database import TermDatabaseManager

            from ._polish_execution import execute_polish
            from ._polish_llm_runtime import create_polish_llm_runtime

            runtime = create_polish_llm_runtime(
                task.config,
                esp_path=task.esp_path,
                stop_event=stop,
                pause_event=handle.pause_event,
            )
            terms = TermDatabaseManager(
                config=task.config,
                esp_path=task.esp_path,
                paratranz_client=client,
                project_id=remote_project,
                reference_store=task.references,
                reference_source=task.task.key,
                **task.terminology.term_database_kwargs(),
            )
            terms.load_all()
            summary = execute_polish(
                strategy=strategy,
                intensity=intensity,
                llm_config=task.config,
                llm_client=runtime.client,
                term_manager=terms,
                targets=list(task.task.polish_entries),
                collection=task.task.collection,
                stop_event=stop,
                progress_callback=lambda _stage, current, total, message: manager.update_progress(
                    task_id,
                    {"current": current, "total": total, "message": message},
                ),
                log_callback=lambda line: runtime.log_store.write_line("workflow", line),
            )
            outcome.polish = summary.results
    except Exception as exc:
        logger.exception("助手 AI 任务执行失败，保留已验证成果")
        error = outcome.error = technical_error(exc)
    finally:
        for resource in (runtime, client):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    logger.exception("关闭助手 AI 任务资源失败")
                    warning = f"任务资源清理失败：{exc}"
    try:
        cancelled = stop.is_set()
        task.ledger.ingest((outcome,), set(task.ledger.entries), cancelled=cancelled)
        succeeded = sum(row.status == "succeeded" for row in task.ledger.entries.values())
        failed = sum(row.status == "failed" for row in task.ledger.entries.values())
        state = "cancelled" if cancelled else "failed" if not succeeded else "partial" if failed else "completed"
        # This write is the publication boundary. A failed durable write prevents merge.
        task.persist(state=state, error=error)
        if succeeded and not cancelled:
            try:
                merge, merge_warning = task.merge(execution=handle.execution)
                warning = "; ".join(filter(None, (warning, merge_warning)))
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
