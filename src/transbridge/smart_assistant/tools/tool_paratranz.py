"""P1 ParaTranz 平台工具 (paratranz namespace)。Story 11 + Story 15（项目查询与切换）。"""
from __future__ import annotations

from .base import ToolResult, require_collection


def _get_paratranz_client(ctx, project_id=None):
    """获取 ParatranzClient 并解析 project_id。M30: 消除 7 个函数中重复的 import + 构造 + pid 解析模式。

    Returns:
        (client, pid): client 实例和解析后的 project_id（可能为 None）。
    """
    from src.transbridge.paratranz.api_client import ParatranzClient
    client = ParatranzClient(ctx.config)
    # 优先显式传入的 project_id，其次 ctx.paratranz_project_id（Story 15），最后 ctx.current_project
    pid = project_id or getattr(ctx, 'paratranz_project_id', None) or (ctx.current_project.get("id") if ctx.current_project else None)
    return client, pid


def _tool_list_projects(args: dict, ctx) -> ToolResult:
    """列出 ParaTranz 项目（all/mine）。"""
    view = args.get("view", "mine")
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        projects = client.list_projects(view=view)
        summary = [{"id": p.get("id"), "name": p.get("name"), "visibility": p.get("visibility")}
                   for p in (projects or [])]
        return ToolResult.ok(f"找到 {len(summary)} 个项目", data={"projects": summary})
    except Exception as exc:
        return ToolResult.fail(f"获取项目列表失败: {exc}")


def _tool_get_project_info(args: dict, ctx) -> ToolResult:
    """获取项目详细信息。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        info = client.get_project(pid)
        return ToolResult.ok(data={"id": info.get("id"), "name": info.get("name"),
                                   "visibility": info.get("visibility"), "member_count": len(info.get("members", []))})
    except Exception as exc:
        return ToolResult.fail(f"获取项目信息失败: {exc}")


@require_collection
def _tool_compare_with_remote(args: dict, ctx, collection) -> ToolResult:
    """对比本地与远程差异。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        remote_entries = client.get_entries(pid, limit=500)
        remote_map = {e.get("key"): e for e in remote_entries if e.get("key")}
        diff = {"only_local": 0, "only_remote": 0, "different": 0, "same": 0, "details": []}
        local_keys = set()
        for e in collection:
            local_keys.add(e.key)
            remote = remote_map.get(e.key)
            if not remote:
                diff["only_local"] += 1
                if len(diff["details"]) < 20:
                    diff["details"].append({"key": e.key, "status": "only_local"})
            elif remote.get("translation") != e.translation:
                diff["different"] += 1
                if len(diff["details"]) < 20:
                    diff["details"].append({"key": e.key, "status": "different"})
            else:
                diff["same"] += 1
        diff["only_remote"] = len([k for k in remote_map if k not in local_keys])
        return ToolResult.ok(f"对比完成: 仅本地{diff['only_local']} 仅远程{diff['only_remote']} 不同{diff['different']}", data=diff)
    except Exception as exc:
        return ToolResult.fail(f"对比失败: {exc}")


@require_collection
def _tool_upload_entries(args: dict, ctx, collection) -> ToolResult:
    """上传条目到 ParaTranz。

    NOTE(M10): 当前全量同步上传采用逐条上传方式，无批处理机制。
    对于大批量条目（>100条），逐个 API 调用可能导致耗时较长且易触发限流。
    已知限制，后续可优化为批量上传接口。
    """
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        force = args.get("force_overwrite", False)
        entry_ids = args.get("entry_ids")
        entries = [collection.get(eid) for eid in entry_ids] if entry_ids else list(collection)
        uploaded = 0
        failed_items = []
        for e in entries:
            try:
                client.upsert_entry(pid, {"key": e.key, "original": e.original, "translation": e.translation or "",
                                          "context": e.context or "", "stage": e.stage}, force_overwrite=force)
                uploaded += 1
            except Exception as exc:
                failed_items.append({
                    "key": e.key if hasattr(e, 'key') else str(e),
                    "error": str(exc),
                })
        return ToolResult.ok(
            f"已上传 {uploaded}/{len(entries)} 条" + (f"，失败 {len(failed_items)} 条" if failed_items else ""),
            data={"uploaded": uploaded, "total": len(entries), "failed_items": failed_items},
        )
    except Exception as exc:
        return ToolResult.fail(f"上传失败: {exc}")


def _tool_download_entries(args: dict, ctx) -> ToolResult:
    """下载条目（单阶段 O7: 下载完成后自动附加对比摘要）。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        from src.transbridge.converter.translation_entry import TranslationEntry
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        remote_entries = client.get_entries(pid, limit=2000)
        downloaded = TranslationEntryCollection()
        for re in remote_entries:
            e = TranslationEntry(id=re.get("key", ""), key=re.get("key", ""),
                                 original=re.get("original", ""), translation=re.get("translation", ""),
                                 context=re.get("context", ""), stage=re.get("stage", 0))
            downloaded.add(e, overwrite=True)
        # O7: 自动附加对比摘要
        collection = ctx.collection
        diff_summary = None
        if collection:
            local_keys = {e.key for e in collection}
            remote_keys = {e.key for e in downloaded}
            diff_summary = {
                "new_from_remote": len(remote_keys - local_keys),
                "updated": len([k for k in (local_keys & remote_keys)
                               if collection.get_by_key(k) and downloaded.get_by_key(k)
                               and collection.get_by_key(k).translation != downloaded.get_by_key(k).translation]),
            }
        return ToolResult.ok(
            f"已下载 {len(downloaded)} 条" + (f"（新增{diff_summary['new_from_remote']} 更新{diff_summary['updated']}）" if diff_summary else ""),
            data={"downloaded_count": len(downloaded), "diff_summary": diff_summary},
        )
    except Exception as exc:
        return ToolResult.fail(f"下载失败: {exc}")


def _tool_sync_terms(args: dict, ctx) -> ToolResult:
    """同步术语库。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        terms = client.get_terms(pid)
        return ToolResult.ok(f"已获取 {len(terms) if terms else 0} 个术语", data={"term_count": len(terms) if terms else 0})
    except Exception as exc:
        return ToolResult.fail(f"术语同步失败: {exc}")


def _tool_export_artifact(args: dict, ctx) -> ToolResult:
    """导出 ParaTranz 工件。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        result = client.export_artifact(pid)
        return ToolResult.ok("工件导出请求已提交", data=result if isinstance(result, dict) else {"result": str(result)})
    except Exception as exc:
        return ToolResult.fail(f"导出失败: {exc}")


def _tool_get_upload_history(args: dict, ctx) -> ToolResult:
    """获取上传历史。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        history = client.get_upload_history(pid, limit=args.get("limit", 20))
        return ToolResult.ok(data={"history": history if history else []})
    except Exception as exc:
        return ToolResult.fail(f"获取历史失败: {exc}")


# ── Story 15: 项目查询与切换 ───────────────────────────────────

def _tool_get_paratranz_project(args: dict, ctx) -> ToolResult:
    """获取当前选中的 ParaTranz 项目。"""
    # 降级: paratranz_project_id → current_project → None
    pid = getattr(ctx, 'paratranz_project_id', None) or (ctx.current_project.get("id") if ctx.current_project else None)
    if not pid:
        return ToolResult.ok("未选择 ParaTranz 项目", data={"selected_project": None})
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        info = client.get_project(pid)
        return ToolResult.ok(
            f"当前 ParaTranz 项目: {info.get('name')} (id={pid})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        return ToolResult.fail(f"获取项目信息失败: {exc}")


def _tool_switch_paratranz_project(args: dict, ctx) -> ToolResult:
    """切换当前选中的 ParaTranz 项目。"""
    project_id = args["project_id"]
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        info = client.get_project(project_id)  # 验证有效性
        ctx.paratranz_project_id = project_id
        return ToolResult.ok(
            f"已切换到项目: {info.get('name')} (id={project_id})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        return ToolResult.fail(f"切换项目失败: {exc}")


# ── 参数 Schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "list_projects": {
        "view": {"type": "str", "required": False, "description": "视图: all/mine，默认 mine"},
    },
    "get_project_info": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
    },
    "compare_with_remote": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
    },
    "upload_entries": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
        "force_overwrite": {"type": "bool", "required": False, "description": "是否强制覆盖已存在的条目，默认 false"},
        "entry_ids": {"type": "list", "required": False, "description": "要上传的条目ID列表，默认全部"},
    },
    "download_entries": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
    },
    "sync_terms": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
    },
    "export_artifact": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
    },
    "get_upload_history": {
        "project_id": {"type": "str", "required": False, "description": "项目ID（不传则使用当前选中项目）"},
        "limit": {"type": "int", "required": False, "description": "返回条数上限，默认 20"},
    },
    "switch_paratranz_project": {
        "project_id": {"type": "int", "required": True, "description": "目标项目 ID"},
    },
}

# ── 注册 ──────────────────────────────────────────────────────

def _register_paratranz_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    tools = [
        ("list_projects", "列出项目", "列出ParaTranz项目(all/mine)", _tool_list_projects, "read"),
        ("get_project_info", "项目信息", "获取项目详细信息", _tool_get_project_info, "read"),
        ("compare_with_remote", "对比远程", "对比本地与远程差异(前20条详情)", _tool_compare_with_remote, "read"),
        ("upload_entries", "上传条目", "上传条目到ParaTranz(long_running)", _tool_upload_entries, "write"),
        ("download_entries", "下载条目", "下载条目(单阶段,自动附加对比摘要,O7)", _tool_download_entries, "write"),
        ("sync_terms", "同步术语", "同步术语库", _tool_sync_terms, "write"),
        ("export_artifact", "导出工件", "导出ParaTranz工件", _tool_export_artifact, "write"),
        ("get_upload_history", "上传历史", "获取上传历史", _tool_get_upload_history, "read"),
        # Story 15: 项目查询与切换
        ("get_paratranz_project", "PT当前项目", "获取当前选中的 ParaTranz 项目", _tool_get_paratranz_project, "read"),
        ("switch_paratranz_project", "切换PT项目", "切换到指定的 ParaTranz 项目（project_id 必填）", _tool_switch_paratranz_project, "write"),
    ]

    for name, display_name, description, execute, permission in tools:
        is_long = name in ("upload_entries", "download_entries", "export_artifact")
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}), execute=execute, permission=permission,
            is_long_running=is_long,
            require_confirmation=(name in ("upload_entries", "download_entries")),  # C2: 上传也需确认
        ), namespace="paratranz")


_register_paratranz_tools()
