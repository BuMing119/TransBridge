# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""P1 ParaTranz 平台工具 (paratranz namespace)。Story 11 + Story 15（项目查询与切换）。"""

from __future__ import annotations

from contextvars import ContextVar
from copy import copy
from functools import wraps
import logging

from transbridge.application.ports.paratranz import ParaTranzProject
from transbridge.application.projects import ParaTranzTargetStatus
from transbridge.application.projects.target_context import resolved_paratranz_target
from transbridge.application.tasks import TaskCancelled
from transbridge.paratranz.management_queries import HISTORY_LAST_PAGE

from ._project_upload import project_files
from .base import ToolResult
from .diagnostics import technical_error

logger = logging.getLogger(__name__)

_ACTIVE_CLIENTS: ContextVar[tuple[object, ...]] = ContextVar("paratranz_tool_clients", default=())
_EXECUTABLE_TARGET_STATES = frozenset({
    ParaTranzTargetStatus.UNVERIFIED,
    ParaTranzTargetStatus.AVAILABLE,
})


def _close_paratranz_clients(func):
    """Close every service created during one synchronous Assistant tool call."""

    @wraps(func)
    def wrapped(*args, **kwargs):
        token = _ACTIVE_CLIENTS.set(())
        try:
            return func(*args, **kwargs)
        finally:
            clients = _ACTIVE_CLIENTS.get()
            _ACTIVE_CLIENTS.reset(token)
            for client in reversed(clients):
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - cleanup must not replace the tool result
                    logger.exception("关闭 ParaTranz Assistant 客户端失败")

    return wrapped


def _new_paratranz_client(ctx):
    from transbridge.paratranz.service import ParaTranzService

    config = ctx.config
    snapshot = config.copy_for_execution() if hasattr(config, "copy_for_execution") else copy(config)
    client = ParaTranzService.from_config(snapshot)
    _ACTIVE_CLIENTS.set((*_ACTIVE_CLIENTS.get(), client))
    return client


def _get_paratranz_client(ctx, args):
    """获取 typed ParaTranz port 并解析 project_id。

    Returns:
        (client, pid, error): ParaTranzPort、解析后的 project_id 和稳定错误信息。
    """
    if "project_id" in args:
        project_id = args["project_id"]
        if isinstance(project_id, bool):
            return None, None, "project_id 必须是正整数；未使用工程默认绑定。"
        if isinstance(project_id, int):
            explicit_project_id = project_id
        elif isinstance(project_id, str) and project_id.strip().isdecimal():
            explicit_project_id = int(project_id.strip())
        else:
            return None, None, "project_id 必须是正整数；未使用工程默认绑定。"
        if explicit_project_id <= 0:
            return None, None, "project_id 必须是正整数；未使用工程默认绑定。"
    else:
        explicit_project_id = None
    target = resolved_paratranz_target(ctx, explicit_project_id=explicit_project_id)
    if target.status not in _EXECUTABLE_TARGET_STATES:
        error = (
            None if target.status is ParaTranzTargetStatus.UNBOUND else target.reason or "ParaTranz 同步目标不可用。"
        )
        return None, None, error
    return _new_paratranz_client(ctx), target.project_id, None


def _cancellation(ctx):
    return getattr(ctx, "cancellation_token", None)


def _project_mapping(project):
    if isinstance(project, ParaTranzProject):
        return {
            "id": project.project_id,
            "name": project.name,
            "visibility": project.visibility,
            "member_count": project.member_count,
        }
    return project


@_close_paratranz_clients
def _tool_list_projects(args: dict, ctx) -> ToolResult:
    """列出参与的或全部 ParaTranz 项目。"""
    uid = args.get("uid", "my")
    if uid not in ("my", ""):
        return ToolResult.fail("uid 只支持 my 或空字符串。", error_code="PROJECT_SCOPE_INVALID")
    try:
        client = _new_paratranz_client(ctx)
        projects = client.list_projects(uid=uid or None, cancellation=_cancellation(ctx))
        summary = [
            {"id": project.project_id, "name": project.name, "visibility": project.visibility} for project in projects
        ]
        return ToolResult.ok(f"找到 {len(summary)} 个项目", data={"projects": summary})
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取项目列表失败")
        return ToolResult.fail(f"获取项目列表失败: {technical_error(exc)}")


@_close_paratranz_clients
def _tool_get_project_info(args: dict, ctx) -> ToolResult:
    """获取项目详细信息。"""
    try:
        client, pid, target_error = _get_paratranz_client(ctx, args)
        if target_error:
            return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
        if not pid:
            return ToolResult.fail("请指定 project_id")
        project = client.get_project(pid, cancellation=_cancellation(ctx))
        return ToolResult.ok(data=_project_mapping(project))
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取项目信息失败")
        return ToolResult.fail(f"获取项目信息失败: {technical_error(exc)}")


def _tool_compare_with_remote(args: dict, ctx) -> ToolResult:
    return project_files(args, ctx, compare=True)


def _tool_upload_entries(args: dict, ctx) -> ToolResult:
    return project_files(args, ctx, compare=False)


@_close_paratranz_clients
def _tool_download_entries(args: dict, ctx) -> ToolResult:
    """Download plugin files and save matching translations through the shared use case."""
    from ._project_download import download_entries

    return download_entries(args, ctx)


@_close_paratranz_clients
def _tool_export_artifact(args: dict, ctx) -> ToolResult:
    """导出 ParaTranz 工件。"""
    from transbridge.application.assistant_requests.models import RequestError

    from ._export_artifact import export_artifact

    try:
        client, pid, target_error = _get_paratranz_client(ctx, args)
        if target_error:
            return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
        if not pid:
            return ToolResult.fail("请指定 project_id")
        return export_artifact(args, ctx, client, pid)
    except RequestError as exc:
        return ToolResult.fail(str(exc), error_code=exc.code, error_category="permission")
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("导出工件失败")
        return ToolResult.fail(f"导出失败: {technical_error(exc)}")


@_close_paratranz_clients
def _tool_get_upload_history(args: dict, ctx) -> ToolResult:
    """读取桌面文件上传历史的指定页。"""
    if "limit" in args:
        return ToolResult.fail("请按页查询上传历史。", error_code="ARGUMENT_SCHEMA_INVALID")
    client, pid, target_error = _get_paratranz_client(ctx, args)
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        history = client.list_upload_history(pid, page=args.get("page", 1), cancellation=_cancellation(ctx))
        projected = [dict(item.raw) for item in history]
        return ToolResult.ok(data={"history": projected})
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取上传历史失败")
        return ToolResult.fail(f"获取历史失败: {technical_error(exc)}")


# ── Story 15: 项目查询与切换 ───────────────────────────────────


def _tool_get_paratranz_project(args: dict, ctx) -> ToolResult:
    """Read the current local binding, including its workbench availability status."""
    from .types import ExecutionContext

    def snapshot():
        if isinstance(ctx, ExecutionContext) and ctx._target_version_identity != getattr(
            ctx.app_context, "active_version_identity", None
        ):
            return ToolResult.fail("本地工程或版本已切换，请重新查询。")
        target = resolved_paratranz_target(ctx)
        if target.project_id is None:
            return ToolResult.ok("当前本地工程未绑定 ParaTranz 项目", data={"selected_project": None})
        return ToolResult.ok(
            f"当前工程绑定到 {target.project_name} (#{target.project_id})",
            data={
                "id": target.project_id,
                "name": target.project_name,
                "status": target.status.value,
                "reason": target.reason,
                "local_project_id": getattr(ctx, "active_project_id", None),
                "local_project_name": getattr(ctx, "project_name", None),
            },
        )

    return ctx.safe_mutate_wait(snapshot) if isinstance(ctx, ExecutionContext) else snapshot()


def _tool_switch_paratranz_project(args: dict, ctx) -> ToolResult:
    """Bind one of the signed-in account's projects through the workbench command."""
    from transbridge.paratranz.project_binding import BindingSelection
    from transbridge.paratranz.project_catalog import ParaTranzProjectCatalog

    from .binding_undo_capture import capture_binding_command
    from .types import ExecutionContext

    project_id = args.get("project_id")
    if type(project_id) is not int or project_id <= 0:
        return ToolResult.fail("project_id 必须是正整数。", error_category="input")
    try:
        app = ctx.app_context if isinstance(ctx, ExecutionContext) else ctx
        selection = BindingSelection.capture(app)
        if isinstance(ctx, ExecutionContext) and (
            ctx._target_version_identity != app.active_version_identity
            or ctx._target_project_revision != selection.local_revision
        ):
            return ToolResult.fail("本地工程已变化，请重新确认要绑定的工程。", error_category="input")
        cancellation = _cancellation(ctx)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        project = selection.member_project(ParaTranzProjectCatalog(), project_id, cancellation=cancellation)
        if project is None:
            return ToolResult.fail("当前 ParaTranz 账号不是所选项目的成员。", error_category="input")

        def commit():
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            return selection.bind(app, project, capture_commit=lambda update: capture_binding_command(ctx, update))

        result = ctx.safe_mutate_wait(commit) if isinstance(ctx, ExecutionContext) else commit()
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "本地工程绑定失败"
            return ToolResult.fail(message)
        return ToolResult.ok(
            f"已绑定当前工程到项目: {project.name} (id={project_id})",
            data={"id": project_id, "name": project.name, "visibility": project.visibility},
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("切换ParaTranz项目失败")
        return ToolResult.fail(f"切换项目失败: {technical_error(exc)}")


# ── 参数 Schema ────────────────────────────────────────────────

_FILE_PARAMETERS = {
    "mode": {
        "type": "str",
        "required": True,
        "description": "Desktop upload mode: orig_only, trans_safe, trans_force or both",
    },
    "source_ids": {
        "type": "list",
        "required": False,
        "description": "Registered sources; defaults to the active plugin",
    },
    "categorized": {
        "type": "bool",
        "required": False,
        "description": "Use the desktop category layout; defaults to ordinary plugin files",
    },
    "project_id": {"type": "int", "required": False, "description": "Remote project; defaults to the current binding"},
}
_PARAM_SCHEMAS = {
    "list_projects": {
        "uid": {
            "type": "str",
            "required": False,
            "enum": ["my", ""],
            "description": 'Pass "my" to list my projects (default), or "" to list all projects',
        },
    },
    "get_project_info": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
    },
    "download_entries": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
        "source_ids": {
            "type": "list",
            "required": False,
            "description": "Registered source IDs to download; defaults to the currently active plugin",
        },
    },
    "export_artifact": {
        "action": {
            "type": "str",
            "required": False,
            "enum": ["trigger", "download"],
            "default": "trigger",
            "description": "trigger creates an export (default); download saves the existing ZIP.",
        },
        "output_path": {
            "type": "str",
            "required": False,
            "description": "Suggested desktop ZIP name. A granted output path is required for headless downloads.",
        },
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
    },
    "get_upload_history": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
        "page": {
            "type": "int",
            "required": False,
            "min": 1,
            "max": HISTORY_LAST_PAGE,
            "default": 1,
            "description": "History page (1–9999, default 1); 50 revisions per page.",
        },
    },
    "switch_paratranz_project": {
        "project_id": {"type": "int", "required": True, "description": "Target project ID"},
    },
}

# ── 注册 ──────────────────────────────────────────────────────


def _register_paratranz_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "paratranz",
        [
            {
                "name": "list_projects",
                "display_name": "列出项目",
                "description": (
                    '①List ParaTranz projects. ②Parameters: uid ("my" lists only my projects '
                    'and is the default; "" lists '
                    "all projects). Read-only. ③Returns: {projects:[{id,name,visibility}]}. "
                    "Rule: use get_project_info to "
                    "inspect a single project."
                ),
                "execute": _tool_list_projects,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("list_projects", {}),
            },
            {
                "name": "get_project_info",
                "display_name": "项目信息",
                "description": (
                    "①Get detailed project information. ②Parameters: project_id "
                    "(optional; uses the current project binding "
                    "when omitted). Read-only. ③Returns: {id,name,visibility,member_count}. "
                    "Rule: use this tool for details "
                    "about a specific project or member_count; use get_paratranz_project "
                    "for a quick lookup of the project "
                    "binding."
                ),
                "execute": _tool_get_project_info,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_project_info", {}),
            },
            {
                "name": "compare_with_remote",
                "display_name": "比较上传文件",
                "description": (
                    "Preview upload differences for whole plugin files. Returns source_id, file_key, path, "
                    "existence, source changes, key renames and translation change counts. Read only."
                ),
                "execute": _tool_compare_with_remote,
                "permission": "read",
                "parameters": {
                    **_FILE_PARAMETERS,
                    "file_keys": {
                        "type": "list",
                        "required": False,
                        "description": "Files to compare; defaults to all files of the selected sources",
                    },
                },
            },
            {
                "name": "upload_entries",
                "display_name": "上传工程文件",
                "description": (
                    "Publish explicitly selected complete project files using the desktop file upload workflow. "
                    "Use file_keys from compare_with_remote with the same remote target, layout, sources and mode. "
                    "trans_force overwrites differing nonblank translations. Requires confirmation. "
                    "Returns file counts and details."
                ),
                "execute": _tool_upload_entries,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": {
                    **_FILE_PARAMETERS,
                    "file_keys": {
                        "type": "list",
                        "required": True,
                        "description": "Exact file keys authorized for upload",
                    },
                },
            },
            {
                "name": "download_entries",
                "display_name": "下载条目",
                "description": (
                    "Download all files under the selected plugin directories, match local entries, and save "
                    "translations directly using the same project workflow as the GUI. project_id is optional; "
                    "source_ids selects registered sources and defaults to the active plugin. Returns updated_count, "
                    "unchanged_count, pending_count, source_ids and retryable. Does not create local source entries. "
                    "There is no 2000-entry pagination limit or saved result_id. At 50 or more selected remote files, "
                    "this triggers a fresh project export (requires "
                    "ParaTranz admin permission), downloads its ZIP and matches only the selected files. Otherwise "
                    "files are downloaded individually. Export failure is reported without using a stale ZIP. "
                    "ZIP and extracted files are removed after reading; in-memory download content is cleared "
                    "when this call ends; do not call get_task_result or merge_task_result for this operation. "
                    "Unresolved translations keep their local content. Write permission and confirmation required."
                ),
                "execute": _tool_download_entries,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("download_entries", {}),
            },
            {
                "name": "export_artifact",
                "display_name": "导出工件",
                "description": (
                    "Trigger a new ParaTranz export or download the existing ZIP. "
                    "The default trigger action returns fresh artifact metadata or the pending job. "
                    "Download saves the existing ZIP without triggering an export. The desktop user selects the file. "
                    "Headless downloads require an authorized output_path. "
                    "Both actions require write permission and confirmation."
                ),
                "execute": _tool_export_artifact,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("export_artifact", {}),
            },
            {
                "name": "get_upload_history",
                "display_name": "上传历史",
                "description": (
                    "①Get the upload history for a ParaTranz project. "
                    "②Parameters: project_id (optional), page (default 1). "
                    "Read-only. ③Returns {history} containing remote file revisions with time, "
                    "filename, operation, user and insert/update/remove counts."
                ),
                "execute": _tool_get_upload_history,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_upload_history", {}),
            },
            # Story 15: 项目查询与切换
            {
                "name": "get_paratranz_project",
                "display_name": "PT同步目标",
                "description": (
                    "①Get the ParaTranz synchronization target bound to the current local project. ②No parameters. "
                    "Local read, works offline. ③Returns {id,name,status,reason,local_project_id,local_project_name}, "
                    "or {selected_project:null} "
                    "when no project is bound. Rule: use "
                    "get_project_info for details about a specific project or member_count."
                ),
                "execute": _tool_get_paratranz_project,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_paratranz_project", {}),
            },
            {
                "name": "switch_paratranz_project",
                "display_name": "绑定PT同步目标",
                "description": (
                    "①Explicitly bind a ParaTranz project as the default synchronization target for the current local "
                    "project. ②Parameters: project_id (required int from the signed-in account's projects). "
                    "Write permission. ③Returns: "
                    "{id,name,visibility}. Rules: a local project must already exist; "
                    "browsing the management page does "
                    "not change the binding; local translation data remains unchanged when switching bindings."
                ),
                "execute": _tool_switch_paratranz_project,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("switch_paratranz_project", {}),
            },
        ],
    )


_register_paratranz_tools()
