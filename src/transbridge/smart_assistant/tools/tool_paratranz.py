"""P1 ParaTranz 平台工具 (paratranz namespace)。Story 11。"""
from __future__ import annotations

from .base import ToolResult


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
    pid = args.get("project_id")
    if not pid:
        current = ctx.current_project
        pid = current.get("id") if current else None
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        info = client.get_project(pid)
        return ToolResult.ok(data={"id": info.get("id"), "name": info.get("name"),
                                   "visibility": info.get("visibility"), "member_count": len(info.get("members", []))})
    except Exception as exc:
        return ToolResult.fail(f"获取项目信息失败: {exc}")


def _tool_compare_with_remote(args: dict, ctx) -> ToolResult:
    """对比本地与远程差异。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前无翻译集合")
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
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


def _tool_upload_entries(args: dict, ctx) -> ToolResult:
    """上传条目到 ParaTranz。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前无可上传集合")
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
        force = args.get("force_overwrite", False)
        entry_ids = args.get("entry_ids")
        entries = [collection.get(eid) for eid in entry_ids] if entry_ids else list(collection)
        uploaded = 0
        for e in entries:
            try:
                client.upsert_entry(pid, {"key": e.key, "original": e.original, "translation": e.translation or "",
                                          "context": e.context or "", "stage": e.stage}, force_overwrite=force)
                uploaded += 1
            except Exception:
                pass
        return ToolResult.ok(f"已上传 {uploaded}/{len(entries)} 条", data={"uploaded": uploaded, "total": len(entries)})
    except Exception as exc:
        return ToolResult.fail(f"上传失败: {exc}")


def _tool_download_entries(args: dict, ctx) -> ToolResult:
    """下载条目（单阶段 O7: 下载完成后自动附加对比摘要）。"""
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        from src.transbridge.converter.translation_entry import TranslationEntry
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
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
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
        terms = client.get_terms(pid)
        return ToolResult.ok(f"已获取 {len(terms) if terms else 0} 个术语", data={"term_count": len(terms) if terms else 0})
    except Exception as exc:
        return ToolResult.fail(f"术语同步失败: {exc}")


def _tool_export_artifact(args: dict, ctx) -> ToolResult:
    """导出 ParaTranz 工件。"""
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
        result = client.export_artifact(pid)
        return ToolResult.ok("工件导出请求已提交", data=result if isinstance(result, dict) else {"result": str(result)})
    except Exception as exc:
        return ToolResult.fail(f"导出失败: {exc}")


def _tool_get_upload_history(args: dict, ctx) -> ToolResult:
    """获取上传历史。"""
    try:
        from src.transbridge.paratranz.api_client import ParatranzClient
        client = ParatranzClient(ctx.config)
        pid = args.get("project_id") or (ctx.current_project.get("id") if ctx.current_project else None)
        if not pid:
            return ToolResult.fail("请指定 project_id")
        history = client.get_upload_history(pid, limit=args.get("limit", 20))
        return ToolResult.ok(data={"history": history if history else []})
    except Exception as exc:
        return ToolResult.fail(f"获取历史失败: {exc}")


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
    ]

    for name, display_name, description, execute, permission in tools:
        is_long = name in ("upload_entries", "download_entries", "export_artifact")
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters={}, execute=execute, permission=permission,
            is_long_running=is_long,
            require_confirmation=(name in ("upload_entries", "download_entries")),  # C2: 上传也需确认
        ), namespace="paratranz")


_register_paratranz_tools()
