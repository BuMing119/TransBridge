"""Assistant write-back through immutable hydration and guarded publication."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.operation_write import HydratedWriteWorkload
from transbridge.application.io.operation_write_batch import HydratedWriteBatchWorkload
from transbridge.application.tasks import TaskCancelled

from .base import ToolResult
from .diagnostics import technical_error
from .file_undo_capture import capture_file_command
from .input_prerequisites import source_input_failure
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata
from .write_back_preparation import _selected_sources, prepare_write_back


def _tool_write_back(args: dict, ctx) -> ToolResult:
    try:
        prepared = prepare_write_back(args, ctx)
        if not prepared.capture_paths:
            blocked = [
                (key, check)
                for key, checked in zip(prepared.source_keys, prepared.preflights, strict=True)
                for check in checked.checks
                if not check.passed and not check.warning
            ]
            return ToolResult.fail(
                "；".join(f"{key}：{check.message}" for key, check in blocked),
                error_code=blocked[0][1].code,
                failed_items=[
                    {"source_key": key, "code": check.code, "message": check.message} for key, check in blocked
                ]
                if prepared.is_batch
                else None,
            )
        workload = (
            HydratedWriteBatchWorkload(prepared.preflights)
            if prepared.is_batch
            else HydratedWriteWorkload(prepared.preflights[0])
        )
        manager = TaskManager()
        label = (
            f"批量写回 {len(prepared.paths)} 个来源" if prepared.is_batch else "写回 " + Path(prepared.paths[0]).name
        )
        task_id = manager.register(metadata=task_metadata(ctx, {"type": "write", "display_name": label}))
        execution = manager.get_handle(task_id).execution
        cancellation = manager.runtime.cancellation_token(execution.ref, execution.owner)

        def run():
            publication = None

            def publish():
                nonlocal publication
                publication = workload(
                    SimpleNamespace(
                        ref=execution.ref,
                        cancellation=cancellation,
                        publish_commit_guard=lambda: execution,
                    )
                )
                return publication

            try:
                result = capture_file_command(ctx, prepared.capture_paths, publish)
                data = _write_receipt(result, prepared)
                if result.outcome is not OperationOutcome.COMPLETED:
                    if cancellation.is_cancelled or result.outcome is OperationOutcome.CANCELLED:
                        manager.set_status(task_id, "cancelled")
                    message = "；".join(item.message for item in result.diagnostics) or "写回未完整完成"
                    manager.notify_finished(task_id, False, message, data)
                    return
                data["written_count"] = sum(len(item.draft.entries) for item in prepared.preflights)
                manager.notify_finished(task_id, True, "写回完成", data)
            except TaskCancelled:
                raise
            except Exception as exc:
                data = None if publication is None else _write_receipt(publication, prepared)
                if data is not None:
                    data["publication_outcome"] = publication.outcome.value
                    data["outcome"] = "partial" if publication.artifact_refs else "failed"
                    data["post_publish_error"] = technical_error(exc)
                    data.pop("written_count", None)
                manager.notify_finished(task_id, False, f"写回失败: {technical_error(exc)}", data)
                raise

        manager.start_thread(task_id, run)
        started = {"task_id": task_id}
        if prepared.is_batch:
            started.update(paths=list(prepared.paths), source_keys=list(prepared.source_keys))
        else:
            started["path"] = prepared.paths[0]
        return ToolResult.ok("安全写回任务已启动", data=started)
    except RequestError as exc:
        if exc.code == "SOURCE_SNAPSHOT_REQUIRED":
            repair = source_input_failure(
                ctx,
                requirement="template",
                source_keys=[key for key, _slot in _selected_sources(args, ctx)],
                error_code=exc.code,
            )
            if repair is not None:
                return repair
        return ToolResult.fail(str(exc), error_code=exc.code)
    except Exception as exc:
        return ToolResult.fail(f"无法创建写回计划: {technical_error(exc)}")


def _write_receipt(result, prepared):
    data = _publication_data(result, prepared.paths[0])
    if not prepared.is_batch:
        return data
    outcomes = data.get("outcomes", ())
    if not outcomes:
        outcomes = next(
            (
                dict(item.details).get("outcomes", ())
                for item in result.diagnostics
                if item.code == "WRITE_BATCH_RESULTS"
            ),
            (),
        )
    sources = dict(zip(prepared.paths, prepared.source_keys, strict=True))
    data["outcomes"] = [{**item, "source_key": sources[item["object_ref"]]} for item in outcomes]
    data.pop("path", None)
    data.update(paths=list(prepared.paths), source_keys=list(prepared.source_keys))
    return data


def _publication_data(result, path: str) -> dict:
    """Retain publication evidence even when durability or undo capture fails."""
    return {
        **dict(result.value or {}),
        "path": path,
        "outcome": result.outcome.value,
        "artifacts": list(result.artifact_refs),
        "diagnostics": [item.to_dict() for item in result.diagnostics],
        "counts": result.counts.to_dict(),
    }


_PARAM_SCHEMAS = {
    "write_back": {
        "target": {
            "type": "str",
            "required": True,
            "description": "esp/eet/xt/strings, or auto with explicit source_keys; auto preserves each source's format",
        },
        "path": {
            "type": "str",
            "required": False,
            "description": (
                "Output file; defaults to the current optional reference. Required without it or output_dir."
            ),
        },
        "output_dir": {
            "type": "str",
            "required": False,
            "description": (
                "Save selected sources together under their original names; supports all targets. "
                "Required for strings. Omit to use an available reference location. "
                "Single-file path takes precedence except for strings."
            ),
        },
        "source_keys": {
            "type": "list",
            "required": False,
            "description": (
                "Explicit collection keys from list_collections; omit for the active source. "
                "A nonempty unique list selects one or more sources without switching the active collection. "
                "Multiple sources cannot use path."
            ),
        },
        "pack_bsa": {
            "type": "bool",
            "required": False,
            "description": (
                "Default false: write loose STRINGS. True packs only localized STRINGS into BSA alongside the plugin. "
                "Other formats in an auto batch are unchanged. "
                "Does not replace input archives or delete existing loose files."
            ),
        },
        "overwrite": {
            "type": "bool",
            "required": False,
            "description": "Explicitly allow replacing existing outputs with backups",
        },
    },
}


def _register_writer_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "writer",
        [
            {
                "name": "write_back",
                "display_name": "写回译文",
                "description": (
                    "①Publish translations from the parsed source snapshot with validation, backups "
                    "and guarded atomic publication. "
                    "②Arguments: target esp/eet/xt/strings; auto preserves each explicitly selected source's format. "
                    "source_keys selects exact keys returned by list_collections; omit for the active source. "
                    "output_dir saves original filenames together for all formats; path selects one output file. "
                    "strings requires output_dir and publishes the plugin and its localized resources together. "
                    "pack_bsa defaults to false (loose STRINGS); true optionally packs localized resources. "
                    "Existing outputs require overwrite=true. "
                    "③Returns {task_id,path} or {task_id,paths,source_keys}; task results include per-source outcomes, "
                    "actual artifacts and diagnostics. Independent batch members may complete when others fail. "
                    "Wait for the task terminal result before dependent actions. "
                    "Requires admin confirmation and an authorized output root. "
                    "Uses saved source content; missing content must be supplied for the selected source. "
                    "Without a current reference location, provide path or output_dir."
                ),
                "execute": _tool_write_back,
                "permission": "admin",
                "require_confirmation": True,
                "is_long_running": True,
                "parameters": _PARAM_SCHEMAS["write_back"],
            }
        ],
    )


_register_writer_tools()
