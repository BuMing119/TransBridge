"""Assistant write-back through immutable hydration and guarded publication."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.plugin_write import plugin_artifact_paths
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.application.tasks import TaskCancelled

from .base import ToolResult, require_collection
from .task_manager import TaskManager
from .task_runtime_bridge import task_metadata


@require_collection
def _tool_write_back(args: dict, ctx, collection) -> ToolResult:
    target = str(args.get("target", "")).lower()
    formats = {
        "esp": FormatId.PLUGIN_SSE,
        "strings": FormatId.PLUGIN_SSE,
        "eet": FormatId.XML_EET,
        "xt": FormatId.XML_XT,
    }
    if target not in formats:
        return ToolResult.fail("无效的 target；可选 esp/eet/xt/strings")
    slot = getattr(ctx, "active_slot", None)
    snapshot = getattr(slot, "source_snapshot", None)
    if snapshot is None or snapshot.format_id is not formats[target]:
        return ToolResult.fail(
            "当前集合缺少匹配的来源快照，请先解析对应源文件。", error_code="SOURCE_SNAPSHOT_REQUIRED"
        )
    try:
        source = Path(snapshot.source.uri)
        path = args.get("path")
        if target == "strings":
            directory = args.get("output_dir")
            if not directory:
                return ToolResult.fail("请提供 output_dir；本地化插件与 Strings 将作为同一发布事务输出。")
            path = str(Path(directory) / source.name)
        path = str(path or source)
        request_context = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
        roots = tuple(getattr(request_context, "authorized_roots", ()) or getattr(ctx, "authorized_roots", ()) or ())
        if not roots:
            return ToolResult.fail("写回缺少授权目录。", error_code="PATH_GRANT_REQUIRED")
        metadata = dict(getattr(request_context, "metadata", ()) or ())
        base = Path(metadata.get("working_directory") or roots[0])
        destination = Path(path)
        path = str((destination if destination.is_absolute() else base / destination).resolve(strict=False))
        policy = PathAuthorizationPolicy([PathGrant(Path(root), allow_create=True) for root in roots])
        for output in plugin_artifact_paths(snapshot, path):
            output_path = Path(output)
            # Companions are generated under exactly this one child directory.
            # Authorize its creation without creating it before preflight. An
            # existing symlink/junction must still pass normal canonical checks.
            if output_path.parent == Path(path).parent / "Strings" and not os.path.lexists(output_path.parent):
                output_path = output_path.parent
            decision = policy.authorize(output_path, working_directory=base, for_creation=True)
            if not decision.allowed:
                return ToolResult.fail(decision.reason, error_code=decision.code)
        if request_context is None:
            request_context = RequestContext("smart-assistant-writer")
        draft = HydratedWriteDraft(
            snapshot,
            snapshot.format_id,
            tuple(entry.snapshot() for entry in collection),
            path,
            collection.collection_revision.value,
            request_context,
            conflict_policy=ConflictPolicy.EXPLICIT_OVERWRITE if args.get("overwrite", False) else ConflictPolicy.FAIL,
            backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
        )
        checked = HydratedWritePreflightService().preflight(draft)
        if not checked.ready:
            blocked = tuple(check for check in checked.checks if not check.passed and not check.warning)
            return ToolResult.fail("；".join(check.message for check in blocked), error_code=blocked[0].code)
        workload = HydratedWriteWorkload(checked)
        manager = TaskManager()
        task_id = manager.register(
            metadata=task_metadata(ctx, {"type": "write", "display_name": "写回 " + source.name})
        )
        execution = manager.get_handle(task_id).execution

        def run():
            try:
                result = workload(
                    SimpleNamespace(
                        ref=execution.ref,
                        cancellation=manager.runtime.cancellation_token(execution.ref, execution.owner),
                        publish_commit_guard=lambda: execution,
                    )
                )
                if result.outcome is OperationOutcome.CANCELLED:
                    raise TaskCancelled("写回已取消，正式目标未改变。")
                if result.outcome is not OperationOutcome.COMPLETED:
                    message = "；".join(item.message for item in result.diagnostics) or "写回未完整完成"
                    manager.notify_finished(task_id, False, message, result.value)
                    return
                manager.notify_finished(
                    task_id,
                    True,
                    "写回完成",
                    {
                        "path": path,
                        "written_count": len(draft.entries),
                        "artifacts": list(result.artifact_refs),
                    },
                )
            except TaskCancelled:
                raise
            except Exception as exc:
                manager.notify_finished(task_id, False, f"写回失败: {exc}")
                raise

        manager.start_thread(task_id, run)
        return ToolResult.ok("安全写回任务已启动", data={"task_id": task_id, "path": path})
    except Exception as exc:
        return ToolResult.fail(f"无法创建写回计划: {exc}")


_PARAM_SCHEMAS = {
    "write_back": {
        "target": {"type": "str", "required": True, "description": "esp/eet/xt/strings; must match the parsed source"},
        "path": {"type": "str", "required": False, "description": "Output file; defaults to the source path"},
        "output_dir": {
            "type": "str",
            "required": False,
            "description": "Output directory for a localized plugin bundle",
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
                    "②Arguments: target esp/eet/xt/strings, optional path, overwrite (default false), "
                    "output_dir for strings. strings publishes the plugin and its localized Strings together. "
                    "Existing outputs require overwrite=true. "
                    "③Returns {task_id,path}; wait for the task terminal result before dependent actions. "
                    "Requires admin confirmation and an authorized output root. Parse the matching source first."
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
