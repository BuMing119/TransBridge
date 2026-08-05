"""P1 ParaTranz 平台工具 (paratranz namespace)。Story 11 + Story 15（项目查询与切换）。"""
from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

from .base import ToolResult, require_collection


def _get_paratranz_client(ctx, project_id=None):
    """获取 ParatranzProjectAPI 并解析 project_id。M30: 消除 7 个函数中重复的 import + 构造 + pid 解析模式。

    Returns:
        (client, pid): ParatranzProjectAPI 实例和解析后的 project_id（可能为 None）。
    """
    from src.transbridge.paratranz import ParatranzProjectAPI
    client = ParatranzProjectAPI(ctx.config)
    # 优先显式传入的 project_id，其次 ctx.paratranz_project_id（Story 15），最后 ctx.current_project
    pid = project_id or getattr(ctx, 'paratranz_project_id', None) or (ctx.current_project.get("id") if ctx.current_project else None)
    return client, pid


def _tool_list_projects(args: dict, ctx) -> ToolResult:
    """列出 ParaTranz 项目。uid="my" 查看我的项目，不传则查看全部。"""
    uid = args.get("uid", "my")
    try:
        client, _ = _get_paratranz_client(ctx)
        projects = client.list_projects(page=1, page_size=200, uid=uid)
        # 兼容 API 返回 list 或 {"projects": [...]} 两种格式
        if isinstance(projects, dict):
            project_list = projects.get("projects", [])
        elif isinstance(projects, list):
            project_list = projects
        else:
            project_list = []
        summary = [{"id": p.get("id"), "name": p.get("name"), "visibility": p.get("visibility")}
                   for p in project_list]
        return ToolResult.ok(f"找到 {len(summary)} 个项目", data={"projects": summary})
    except Exception as exc:
        logger.error("获取项目列表失败")
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
        logger.error("获取项目信息失败")
        return ToolResult.fail(f"获取项目信息失败: {exc}")


@require_collection
def _tool_compare_with_remote(args: dict, ctx, collection) -> ToolResult:
    """对比本地与远程差异。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        limit = args.get("limit", 500)
        remote_entries = client.get_entries(pid, limit=limit)
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
        logger.error("对比远程条目失败")
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
                logger.warning("上传条目失败: %s", exc)
                failed_items.append({
                    "key": e.key if hasattr(e, 'key') else str(e),
                    "error": str(exc),
                })
        return ToolResult.ok(
            f"已上传 {uploaded}/{len(entries)} 条" + (f"，失败 {len(failed_items)} 条" if failed_items else ""),
            data={"uploaded": uploaded, "total": len(entries), "failed_items": failed_items},
        )
    except Exception as exc:
        logger.error("上传条目失败")
        return ToolResult.fail(f"上传失败: {exc}")


def _tool_download_entries(args: dict, ctx) -> ToolResult:
    """下载条目（单阶段 O7: 下载完成后自动附加对比摘要）。"""
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        from src.transbridge.converter.translation_entry import TranslationEntry
        from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
        limit = args.get("limit", 2000)
        remote_entries = client.get_entries(pid, limit=limit)
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
        logger.error("下载条目失败")
        return ToolResult.fail(f"下载失败: {exc}")


def _tool_export_artifact(args: dict, ctx) -> ToolResult:
    """导出 ParaTranz 工件。"""
    from src.transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
    client, pid = _get_paratranz_client(ctx, args.get("project_id"))
    if not pid:
        return ToolResult.fail("请指定 project_id")
    try:
        export_api = ParatranzExportAPI(ctx.config)
        # 触发导出
        job = export_api.trigger_export(pid)
        # m9: 30秒阻塞轮询占用Agent工作线程，后续可优化为异步回调
        # 轮询等待完成（最长 30 秒）
        import time
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(2)
            artifacts = export_api.get_artifacts(pid)
            if artifacts:
                latest = artifacts[-1] if isinstance(artifacts, list) else artifacts
                return ToolResult.ok("工件导出完成", data=latest if isinstance(latest, dict) else {"artifact": str(latest)})
        return ToolResult.ok("导出已触发，仍在处理中（超时未完成）",
                           data={"job": job, "status": "pending"})
    except Exception as exc:
        logger.error("导出工件失败")
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
        logger.error("获取上传历史失败")
        return ToolResult.fail(f"获取历史失败: {exc}")


# ── Story 15: 项目查询与切换 ───────────────────────────────────

def _tool_get_paratranz_project(args: dict, ctx) -> ToolResult:
    """获取当前选中的 ParaTranz 项目。"""
    # 降级: paratranz_project_id → current_project → None
    pid = getattr(ctx, 'paratranz_project_id', None) or (ctx.current_project.get("id") if ctx.current_project else None)
    if not pid:
        return ToolResult.ok("未选择 ParaTranz 项目", data={"selected_project": None})
    try:
        client, _ = _get_paratranz_client(ctx, pid)
        info = client.get_project(pid)
        return ToolResult.ok(
            f"当前 ParaTranz 项目: {info.get('name')} (id={pid})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        logger.error("获取ParaTranz项目失败")
        return ToolResult.fail(f"获取项目信息失败: {exc}")


def _tool_switch_paratranz_project(args: dict, ctx) -> ToolResult:
    """切换当前选中的 ParaTranz 项目。"""
    project_id = args["project_id"]
    try:
        client, _ = _get_paratranz_client(ctx, project_id)
        info = client.get_project(project_id)  # 验证有效性
        ctx.paratranz_project_id = project_id
        return ToolResult.ok(
            f"已切换到项目: {info.get('name')} (id={project_id})",
            data={"id": info.get("id"), "name": info.get("name"), "visibility": info.get("visibility")}
        )
    except Exception as exc:
        logger.error("切换ParaTranz项目失败")
        return ToolResult.fail(f"切换项目失败: {exc}")


# ── 参数 Schema ────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "list_projects": {
        "uid": {"type": "str", "required": False, "description": "传 \"my\" 查看我的项目（默认），传 \"\" 查看全部项目"},
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
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("paratranz", [
        {"name": "list_projects", "display_name": "列出项目", "description": "①列出ParaTranz项目。②参数: uid(\"my\"=仅我的项目/默认, \"\"=全部项目)。只读。③返回: {projects:[{id,name,visibility}]}。规则: 查看单项目详情用get_project_info。",
         "execute": _tool_list_projects, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("list_projects", {})},
        {"name": "get_project_info", "display_name": "项目信息", "description": "①获取项目详细信息。②参数: project_id(可选, 不传则用当前选中项目)。只读。③返回: {id,name,visibility,member_count}。规则: vs get_paratranz_project: 用此工具查看特定项目详情或member_count; 用get_paratranz_project做快速当前项目查询(零参数, 无选中不报错)。",
         "execute": _tool_get_project_info, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_project_info", {})},
        {"name": "compare_with_remote", "display_name": "对比远程", "description": "①对比本地翻译(ctx.collection)与远程差异。②参数: project_id(可选)。只读。③返回: {only_local,only_remote,different,same,details:[{key,status}](最多20条)}。status: only_local/different。规则: 用get_paratranz_project确认当前项目; 需已加载本地集合。",
         "execute": _tool_compare_with_remote, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("compare_with_remote", {})},
        {"name": "upload_entries", "display_name": "上传条目", "description": "①上传本地条目到ParaTranz。②参数: project_id(可选), entry_ids(可选, key列表来自get_visible_entries, 不传则上传全部), force_overwrite(默认false)。key格式: {record_type}:{form_id}(如NPC_:00012345)。写权限, 长运行, 需确认。③返回: {uploaded,total,failed_items:[{key,error}]}。规则: 100+条目可能触发限流。",
         "execute": _tool_upload_entries, "permission": "write", "is_long_running": True,
         "require_confirmation": True, "parameters": _PARAM_SCHEMAS.get("upload_entries", {})},
        {"name": "download_entries", "display_name": "下载条目", "description": "①从远程下载翻译条目。②参数: project_id(可选)。写权限, 长运行, 需确认。③返回: {downloaded_count,entries:[{key,original,translation,stage,context}],diff_summary}。规则: 不会自动修改本地集合, 条目数据需手动处理; 未加载本地集合时diff_summary为null。",
         "execute": _tool_download_entries, "permission": "write", "is_long_running": True,
         "require_confirmation": True, "parameters": _PARAM_SCHEMAS.get("download_entries", {})},
        {"name": "export_artifact", "display_name": "导出工件", "description": "①从ParaTranz服务端导出翻译工件包(.zip)。②参数: project_id(可选)。写权限, 长运行, 需确认。③返回: 成功时返回artifact数据; 超时时返回{job,status:\"pending\"}。规则: 异步模型: trigger_export触发→轮询get_artifacts(2s间隔, 30s超时)→成功返回artifact数据或超时返回pending。",
         "execute": _tool_export_artifact, "permission": "write", "is_long_running": True,
         "require_confirmation": True, "parameters": _PARAM_SCHEMAS.get("export_artifact", {})},
        {"name": "get_upload_history", "display_name": "上传历史", "description": "①获取ParaTranz项目上传历史。②参数: project_id(可选), limit(默认20)。只读。③返回: {history:[{id,timestamp,filename,status,entries_count}]}。status: success/failed/processing。规则: 上传前检查上次同步时间, 上传后验证是否成功, 排查同步问题。",
         "execute": _tool_get_upload_history, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_upload_history", {})},
        # Story 15: 项目查询与切换
        {"name": "get_paratranz_project", "display_name": "PT当前项目", "description": "①获取当前选中项目。②无参数, 只读。③返回: {id,name,visibility}或无选中时{selected_project:null}。规则: vs get_project_info: 用此工具做快速检查(零参数, 无选中不报错); 查看特定项目详情或member_count用get_project_info。",
         "execute": _tool_get_paratranz_project, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_paratranz_project", {})},
        {"name": "switch_paratranz_project", "display_name": "切换PT项目", "description": "①切换当前PT项目, 后续操作的默认project_id自动更新。②参数: project_id(必填, int, 来自list_projects)。写权限。③返回: {id,name,visibility}。规则: 前置条件: 通过get_app_state检查paratranz_configured; 切换前先调list_projects获取可选ID; 本地数据(翻译集合/筛选条件)跨切换保持不变。",
         "execute": _tool_switch_paratranz_project, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("switch_paratranz_project", {})},
    ])


_register_paratranz_tools()
