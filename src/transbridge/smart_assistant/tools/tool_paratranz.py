"""P1 ParaTranz 平台工具 (paratranz namespace)。Story 11 + Story 15（项目查询与切换）。"""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import logging

from transbridge.application.io.identity import EntryRevision, SourceNamespace
from transbridge.application.ports.paratranz import ParaTranzEntry, ParaTranzProject
from transbridge.application.projects import (
    ParaTranzProjectBinding,
    ParaTranzTargetResolver,
    ParaTranzTargetStatus,
)
from transbridge.application.sync import (
    ConflictPolicy,
    CreateSyncPlanRequest,
    LocalEntrySnapshot,
    ParaTranzSyncPlanningUseCase,
    SyncOperation,
)
from transbridge.application.tasks import TaskCancelled
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter

from .base import ToolResult, require_collection

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

    client = ParaTranzService.from_config(ctx.config)
    _ACTIVE_CLIENTS.set((*_ACTIVE_CLIENTS.get(), client))
    return client


def _get_paratranz_client(ctx, project_id=None):
    """获取 typed ParaTranz port 并解析 project_id。

    Returns:
        (client, pid, error): ParaTranzPort、解析后的 project_id 和稳定错误信息。
    """
    explicit_provided = project_id not in (None, "")
    if explicit_provided:
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
    resolve = getattr(ctx, "resolve_paratranz_target", None)
    if callable(resolve):
        target = resolve(
            explicit_project_id=explicit_project_id,
            explicit_verified=explicit_project_id is not None,
        )
    else:
        target = ParaTranzTargetResolver().resolve(
            binding=getattr(ctx, "paratranz_binding", None),
            binding_revision=getattr(ctx, "project_revision", None),
            explicit_project_id=explicit_project_id,
            endpoint=_config_endpoint(ctx),
            account_user_id=_account_user_id(ctx),
            explicit_verified=explicit_project_id is not None,
        )
    if target.status not in _EXECUTABLE_TARGET_STATES:
        error = (
            None if target.status is ParaTranzTargetStatus.UNBOUND else target.reason or "ParaTranz 同步目标不可用。"
        )
        return None, None, error
    return _new_paratranz_client(ctx), target.project_id, None


def _cancellation(ctx):
    return getattr(ctx, "cancellation_token", None)


def _config_endpoint(ctx) -> str:
    value = getattr(getattr(ctx, "config", None), "base_url", None)
    return value if isinstance(value, str) and value.startswith(("http://", "https://")) else "https://paratranz.cn"


def _account_user_id(ctx) -> int | None:
    user = getattr(ctx, "current_user", None)
    value = user.get("id") if isinstance(user, dict) else getattr(getattr(ctx, "config", None), "user_id", None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _project_mapping(project):
    if isinstance(project, ParaTranzProject):
        return {
            "id": project.project_id,
            "name": project.name,
            "visibility": project.visibility,
            "member_count": project.member_count,
        }
    return project


def _paratranz_ref(entry, project_id: int):
    scope = f"project:{project_id}"
    matches = tuple(
        reference
        for reference in getattr(entry, "external_refs", ())
        if reference.system == "paratranz" and reference.scope == scope
    )
    if len(matches) > 1:
        raise ValueError(f"条目 {entry.key} 存在重复 ParaTranz 远端引用")
    return matches[0] if matches else None


def _local_sync_snapshots(collection, project_id: int, entry_ids=None):
    if entry_ids:
        entries = tuple(collection.get(entry_id) for entry_id in entry_ids)
        missing = tuple(entry_id for entry_id, entry in zip(entry_ids, entries, strict=True) if entry is None)
        if missing:
            raise ValueError(f"找不到本地条目: {', '.join(map(str, missing[:5]))}")
    else:
        entries = tuple(collection)
    namespaces = {entry.identity.namespace for entry in entries}
    if len(namespaces) != 1:
        raise ValueError("同步计划要求本地快照属于单一 source namespace")
    namespace = next(iter(namespaces))
    snapshots = tuple(
        LocalEntrySnapshot(
            entry.identity,
            entry.revision if isinstance(entry.revision, EntryRevision) else EntryRevision(entry.revision),
            entry.original,
            entry.translation or "",
            entry.context or "",
            entry.stage,
            _paratranz_ref(entry, project_id),
        )
        for entry in entries
    )
    return namespace, snapshots


def _sync_planning_use_case(ctx, client):
    injected = getattr(ctx, "paratranz_sync_planning", None)
    if injected is not None:
        return injected
    return ParaTranzSyncPlanningUseCase(ParaTranzRemoteSnapshotAdapter(client))


@_close_paratranz_clients
def _tool_list_projects(args: dict, ctx) -> ToolResult:
    """列出 ParaTranz 项目。uid="my" 查看我的项目，不传则查看全部。"""
    uid = args.get("uid", "my")
    try:
        client = _new_paratranz_client(ctx)
        projects = client.list_projects(uid=uid, cancellation=_cancellation(ctx))
        # 兼容 API 返回 list 或 {"projects": [...]} 两种格式
        if isinstance(projects, dict):
            project_list = projects.get("projects", [])
        elif isinstance(projects, (list, tuple)):
            project_list = projects
        else:
            project_list = []
        summary = [
            {
                "id": mapped.get("id"),
                "name": mapped.get("name"),
                "visibility": mapped.get("visibility"),
            }
            for project in project_list
            if isinstance((mapped := _project_mapping(project)), dict)
        ]
        return ToolResult.ok(f"找到 {len(summary)} 个项目", data={"projects": summary})
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取项目列表失败")
        return ToolResult.fail(f"获取项目列表失败: {exc}")


@_close_paratranz_clients
def _tool_get_project_info(args: dict, ctx) -> ToolResult:
    """获取项目详细信息。"""
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        info = _project_mapping(client.get_project(pid, cancellation=_cancellation(ctx)))
        return ToolResult.ok(
            data={
                "id": info.get("id"),
                "name": info.get("name"),
                "visibility": info.get("visibility"),
                "member_count": info.get("member_count", len(info.get("members", []))),
            }
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取项目信息失败")
        return ToolResult.fail(f"获取项目信息失败: {exc}")


@_close_paratranz_clients
@require_collection
def _tool_compare_with_remote(args: dict, ctx, collection) -> ToolResult:
    """对比本地与远程差异。"""
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        limit = args.get("limit", 500)
        remote_entries = client.list_entries(pid, limit=limit, cancellation=_cancellation(ctx))
        remote_map = {
            (entry.key if isinstance(entry, ParaTranzEntry) else entry.get("key")): entry
            for entry in remote_entries
            if (entry.key if isinstance(entry, ParaTranzEntry) else entry.get("key"))
        }
        diff = {"only_local": 0, "only_remote": 0, "different": 0, "same": 0, "details": []}
        local_keys = set()
        for e in collection:
            local_keys.add(e.key)
            remote = remote_map.get(e.key)
            if not remote:
                diff["only_local"] += 1
                if len(diff["details"]) < 20:
                    diff["details"].append({"key": e.key, "status": "only_local"})
            elif (
                remote.translation if isinstance(remote, ParaTranzEntry) else remote.get("translation")
            ) != e.translation:
                diff["different"] += 1
                if len(diff["details"]) < 20:
                    diff["details"].append({"key": e.key, "status": "different"})
            else:
                diff["same"] += 1
        diff["only_remote"] = len([k for k in remote_map if k not in local_keys])
        return ToolResult.ok(
            f"对比完成: 仅本地{diff['only_local']} 仅远程{diff['only_remote']} 不同{diff['different']}",
            data=diff,
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("对比远程条目失败")
        return ToolResult.fail(f"对比失败: {exc}")


@_close_paratranz_clients
@require_collection
def _tool_plan_sync(args: dict, ctx, collection) -> ToolResult:
    """Build an inspectable, side-effect-free ParaTranz synchronization plan."""

    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        project_id = int(pid)
        namespace, local_entries = _local_sync_snapshots(
            collection,
            project_id,
            args.get("entry_ids"),
        )
        requested_namespace = args.get("source_namespace")
        if requested_namespace is not None and SourceNamespace(requested_namespace) != namespace:
            return ToolResult.fail(
                "source_namespace 与本地条目身份不一致",
                error_category="input",
                error_code="SOURCE_NAMESPACE_MISMATCH",
            )
        use_case = _sync_planning_use_case(ctx, client)
        plan = use_case.create_plan(
            CreateSyncPlanRequest(
                project_id=project_id,
                namespace=namespace,
                local_entries=local_entries,
                operation=SyncOperation(args.get("operation", "upload")),
                conflict_policy=ConflictPolicy(args.get("conflict_policy", "abort")),
                remote_limit=int(args.get("remote_limit", 100_000)),
                cancellation=_cancellation(ctx),
            )
        )
        page_size = int(args.get("page_size", 100))
        offset = int(args.get("offset", 0))
        return ToolResult.ok(
            "同步计划已生成；尚未执行任何远端或本地写入",
            data=plan.to_dict(offset=offset, limit=page_size),
        )
    except TaskCancelled:
        raise
    except (TypeError, ValueError) as exc:
        return ToolResult.fail(
            f"同步计划参数无效: {exc}",
            error_category="input",
            error_code="SYNC_PLAN_INPUT_INVALID",
        )
    except Exception as exc:
        logger.error("生成 ParaTranz 同步计划失败: %s", type(exc).__name__)
        return ToolResult.fail(
            "生成同步计划失败",
            error_category="internal",
            error_code="SYNC_PLAN_FAILED",
        )


@_close_paratranz_clients
@require_collection
def _tool_upload_entries(args: dict, ctx, collection) -> ToolResult:
    """上传条目到 ParaTranz。

    NOTE(M10): 当前全量同步上传采用逐条上传方式，无批处理机制。
    对于大批量条目（>100条），逐个 API 调用可能导致耗时较长且易触发限流。
    已知限制，后续可优化为批量上传接口。
    """
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    from ._entry_upload import upload_entries

    try:
        return upload_entries(args, ctx, collection, client, pid, _cancellation(ctx))
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.exception("上传条目失败")
        return ToolResult.fail(f"上传失败: {exc}")


@_close_paratranz_clients
def _tool_download_entries(args: dict, ctx) -> ToolResult:
    """下载条目（单阶段 O7: 下载完成后自动附加对比摘要）。"""
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        from transbridge.converter.translation_entry import TranslationEntry
        from transbridge.converter.translation_entry_collection import TranslationEntryCollection

        limit = args.get("limit", 2000)
        remote_entries = client.list_entries(pid, limit=limit, cancellation=_cancellation(ctx))
        downloaded = TranslationEntryCollection()
        for remote in remote_entries:
            if isinstance(remote, ParaTranzEntry):
                data = remote.to_remote_payload()
            else:
                data = remote
            e = TranslationEntry(
                id=data.get("key", ""),
                key=data.get("key", ""),
                original=data.get("original", ""),
                translation=data.get("translation", ""),
                context=data.get("context", ""),
                stage=data.get("stage", 0),
            )
            downloaded.add(e, overwrite=True)
        # O7: 自动附加对比摘要
        collection = ctx.collection
        diff_summary = None
        if collection:
            local_keys = {e.key for e in collection}
            remote_keys = {e.key for e in downloaded}
            diff_summary = {
                "new_from_remote": len(remote_keys - local_keys),
                "updated": len([
                    k
                    for k in (local_keys & remote_keys)
                    if collection.get_by_key(k)
                    and downloaded.get_by_key(k)
                    and collection.get_by_key(k).translation != downloaded.get_by_key(k).translation
                ]),
            }
        suffix = f"（新增{diff_summary['new_from_remote']} 更新{diff_summary['updated']}）" if diff_summary else ""
        return ToolResult.ok(
            f"已下载 {len(downloaded)} 条{suffix}",
            data={"downloaded_count": len(downloaded), "diff_summary": diff_summary},
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("下载条目失败")
        return ToolResult.fail(f"下载失败: {exc}")


@_close_paratranz_clients
def _tool_export_artifact(args: dict, ctx) -> ToolResult:
    """导出 ParaTranz 工件。"""
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        # 触发导出
        cancellation = _cancellation(ctx)
        job = client.trigger_export(pid, cancellation=cancellation)
        # m9: 30秒阻塞轮询占用Agent工作线程，后续可优化为异步回调
        # 轮询等待完成（最长 30 秒）
        import time

        deadline = time.time() + 30
        while time.time() < deadline:
            if cancellation is None:
                time.sleep(2)
            elif cancellation.wait(2):
                cancellation.raise_if_cancelled()
            artifacts = client.get_artifacts(pid, cancellation=cancellation)
            if artifacts:
                latest = artifacts[-1] if isinstance(artifacts, (list, tuple)) else artifacts
                result = latest if isinstance(latest, dict) else {"artifact": str(latest)}
                return ToolResult.ok("工件导出完成", data=result)
        return ToolResult.ok("导出已触发，仍在处理中（超时未完成）", data={"job": job, "status": "pending"})
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("导出工件失败")
        return ToolResult.fail(f"导出失败: {exc}")


@_close_paratranz_clients
def _tool_get_upload_history(args: dict, ctx) -> ToolResult:
    """获取上传历史。"""
    client, pid, target_error = _get_paratranz_client(ctx, args.get("project_id"))
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        history = client.list_upload_history(pid, limit=args.get("limit", 20), cancellation=_cancellation(ctx))
        projected = [dict(item.raw) if hasattr(item, "raw") else item for item in history]
        return ToolResult.ok(data={"history": projected})
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取上传历史失败")
        return ToolResult.fail(f"获取历史失败: {exc}")


# ── Story 15: 项目查询与切换 ───────────────────────────────────


@_close_paratranz_clients
def _tool_get_paratranz_project(args: dict, ctx) -> ToolResult:
    """获取当前本地工程绑定的 ParaTranz 项目。"""
    client, pid, target_error = _get_paratranz_client(ctx)
    if target_error:
        return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
    if not pid:
        return ToolResult.ok("当前本地工程未绑定 ParaTranz 项目", data={"selected_project": None})
    try:
        info = _project_mapping(client.get_project(pid, cancellation=_cancellation(ctx)))
        return ToolResult.ok(
            f"当前 ParaTranz 项目: {info.get('name')} (id={pid})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")},
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("获取ParaTranz项目失败")
        return ToolResult.fail(f"获取项目信息失败: {exc}")


@_close_paratranz_clients
def _tool_switch_paratranz_project(args: dict, ctx) -> ToolResult:
    """显式设置当前本地工程的 ParaTranz 默认同步目标。"""
    project_id = args["project_id"]
    try:
        client, _, target_error = _get_paratranz_client(ctx, project_id)
        if target_error:
            return ToolResult.fail(target_error, error_category="input", error_code="PARATRANZ_TARGET_INVALID")
        info = _project_mapping(client.get_project(project_id, cancellation=_cancellation(ctx)))  # 验证有效性
        set_binding = getattr(ctx, "set_paratranz_binding", None)
        if not callable(set_binding) or not getattr(ctx, "active_project_id", None):
            return ToolResult.fail("请先打开本地工程，再设置 ParaTranz 同步目标")
        now = datetime.now().astimezone().isoformat()
        result = set_binding(
            ParaTranzProjectBinding(
                int(project_id),
                str(info.get("name") or f"项目 #{project_id}"),
                _config_endpoint(ctx),
                _account_user_id(ctx),
                now,
                now,
            )
        )
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "本地工程绑定失败"
            return ToolResult.fail(message)
        return ToolResult.ok(
            f"已绑定当前工程到项目: {info.get('name')} (id={project_id})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")},
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        logger.error("切换ParaTranz项目失败")
        return ToolResult.fail(f"切换项目失败: {exc}")


# ── 参数 Schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "list_projects": {
        "uid": {
            "type": "str",
            "required": False,
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
    "compare_with_remote": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
    },
    "plan_sync": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
        "operation": {
            "type": "str",
            "required": False,
            "description": "upload, download, or bidirectional; defaults to upload",
        },
        "conflict_policy": {
            "type": "str",
            "required": False,
            "description": "abort, prefer_local, prefer_remote, or skip; defaults to abort",
        },
        "entry_ids": {
            "type": "list",
            "required": False,
            "description": "Optional list of local EntryKey.local_key values",
        },
        "source_namespace": {
            "type": "str",
            "required": False,
            "description": "Optional explicit source namespace; must match the local identity",
        },
        "remote_limit": {
            "type": "int",
            "required": False,
            "description": "Maximum size of the read-only remote snapshot; defaults to 100000",
        },
        "offset": {"type": "int", "required": False, "description": "Offset for displaying the plan; defaults to 0"},
        "page_size": {
            "type": "int",
            "required": False,
            "description": "Page size for displaying the plan; defaults to 100",
        },
    },
    "upload_entries": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
        "force_overwrite": {
            "type": "bool",
            "required": False,
            "description": "Overwrite only with a stored remote ID for this project; no key lookup; default false",
        },
        "entry_ids": {
            "type": "list",
            "required": False,
            "description": "List of entry IDs to upload; defaults to all entries",
        },
    },
    "download_entries": {
        "project_id": {
            "type": "str",
            "required": False,
            "description": "Project ID (uses the current project binding when omitted)",
        },
    },
    "export_artifact": {
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
        "limit": {
            "type": "int",
            "required": False,
            "description": "Maximum number of records to return; defaults to 20",
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
                "display_name": "对比远程",
                "description": (
                    "①Compare local translations (ctx.collection) with the remote project. ②Parameters: project_id "
                    "(optional). Read-only. ③Returns: "
                    "{only_local,only_remote,different,same,details:[{key,status}]} with at "
                    "most 20 details. status is only_local or different. Rules: "
                    "use get_paratranz_project to confirm the "
                    "project binding; a local collection must be loaded."
                ),
                "execute": _tool_compare_with_remote,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("compare_with_remote", {}),
            },
            {
                "name": "plan_sync",
                "display_name": "规划同步",
                "description": (
                    "Generate a side-effect-free ParaTranz synchronization plan. "
                    "Returns a hash, counts, and paginated "
                    "entries; overwrites or deletions require subsequent confirmation."
                ),
                "execute": _tool_plan_sync,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("plan_sync", {}),
            },
            {
                "name": "upload_entries",
                "display_name": "上传条目",
                "description": (
                    "①Upload local entries to ParaTranz. ②Parameters: project_id (optional), "
                    "entry_ids (optional list of "
                    "keys from get_visible_entries; uploads all entries when omitted), "
                    "force_overwrite (default false; updates require a stored remote ID for this project). "
                    "Without that ID, creates directly and reports server conflicts; never searches by key. Key "
                    "format: {record_type}:{form_id}, for example NPC_:00012345. Write permission, long-running, and "
                    "confirmation required. ③Returns: {uploaded,total,failed_items:[{key,error}]}. "
                    "Rule: uploading 100 or "
                    "more entries may trigger rate limiting."
                ),
                "execute": _tool_upload_entries,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("upload_entries", {}),
            },
            {
                "name": "download_entries",
                "display_name": "下载条目",
                "description": (
                    "①Download translation entries from the remote project. ②Parameters: project_id (optional). Write "
                    "permission, long-running, and confirmation required. ③Returns: "
                    "{downloaded_count,entries:[{key,original,translation,stage,context}],"
                    "diff_summary}. Rules: this does not "
                    "automatically modify the local collection, so entries must be handled "
                    "explicitly; diff_summary is "
                    "null when no local collection is loaded."
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
                    "①Export a translation artifact package (.zip) from ParaTranz. "
                    "②Parameters: project_id (optional). "
                    "Write permission, long-running, and confirmation required. ③Returns artifact data on success, or "
                    '{job,status:"pending"} on timeout. Rule: asynchronous flow: trigger_export starts the job → '
                    "get_artifacts is polled every 2 seconds for up to 30 seconds → "
                    "artifact data is returned on success, or "
                    "pending on timeout."
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
                    "②Parameters: project_id (optional), limit (default 20). "
                    "Read-only. ③Returns: {history:[{id,timestamp,filename,status,entries_count}]}. status is success, "
                    "failed, or processing. Rules: check the last synchronization time before "
                    "uploading, verify success "
                    "afterward, and use the history to troubleshoot synchronization issues."
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
                    "Read-only. ③Returns {id,name,visibility}, or {selected_project:null} "
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
                    "project. ②Parameters: project_id (required int from list_projects). Write permission. ③Returns: "
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
