"""P0 状态查询工具 — 集合/项目/统计查询 (default namespace)。

Story 07: get_collection_summary deprecated(O8)，功能合并到 get_statistics。
"""
from __future__ import annotations

import logging
import os
from collections import Counter

from .base import ToolResult

logger = logging.getLogger(__name__)


_STAGE_LABELS = {0: "未翻译", 1: "已翻译", 2: "有疑问", 3: "已检查", 5: "已审核", 9: "已锁定", -1: "已隐藏"}


def _tool_get_app_state(args: dict, ctx) -> ToolResult:
    """返回当前应用状态摘要。"""
    slot = ctx.active_slot
    # m4: 只暴露文件名，不泄露绝对路径
    # m7: 安全访问 active_project，兼容多种类型
    project_name = None
    if hasattr(ctx, 'active_project') and ctx.active_project is not None:
        project_name = getattr(ctx.active_project, 'name', None)
        if project_name is None and isinstance(ctx.active_project, dict):
            project_name = ctx.active_project.get('name')
    # C5: ParaTranz 配置状态
    pt_configured = False
    try:
        from transbridge.paratranz.config_manager import ParatranzConfig
        pt_cfg = ParatranzConfig.load_from_file()
        pt_configured = bool(getattr(pt_cfg, 'token', None))
    except Exception as exc:
        logger.warning("操作失败: %s", exc)

    return ToolResult.ok(data={
        "active_collection": slot.label if slot else None,
        "esp_file": os.path.basename(ctx.esp_path) if ctx.esp_path else None,
        "eet_file": os.path.basename(ctx.eet_path) if ctx.eet_path else None,
        "xt_file": os.path.basename(ctx.xt_path) if ctx.xt_path else None,
        "project": project_name,
        "variant": ctx.active_variant,
        "filters": ctx.filter_state,
        "collection_count": len(ctx.slots),
        "has_active_collection": slot is not None and slot.collection is not None,
        "paratranz_configured": pt_configured,
    })


def _tool_list_collections(args: dict, ctx) -> ToolResult:
    """列出所有已加载的翻译集合。"""
    collections = []
    for key, slot in ctx.slots.items():
        col = slot.collection
        collections.append({
            "key": key,
            "label": slot.label,
            "esp_name": os.path.basename(slot.esp_path) if slot.esp_path else None,
            "entry_count": len(col) if col else 0,
            "is_active": key == ctx.active_key,
        })
    return ToolResult.ok(f"已加载 {len(collections)} 个集合", data={"collections": collections})


def _tool_switch_collection(args: dict, ctx) -> ToolResult:
    """切换活跃翻译集合。"""
    collection_name = args.get("collection_name", "")
    slot_index = args.get("slot_index")

    if slot_index is not None:
        keys = list(ctx.slots.keys())
        if slot_index < 0 or slot_index >= len(keys):
            return ToolResult.fail(f"slot_index 超出范围: 0~{len(keys) - 1}")
        target_key = keys[slot_index]
    elif collection_name:
        target_key = None
        for key, slot in ctx.slots.items():
            if slot.label == collection_name or key == collection_name:
                target_key = key
                break
        if target_key is None:
            return ToolResult.fail(f"未找到集合: {collection_name}")
    else:
        return ToolResult.fail("请指定 collection_name 或 slot_index")

    old = ctx.active_slot.label if ctx.active_slot else "无"
    ctx.activate_slot(target_key)
    new_label = ctx.active_slot.label if ctx.active_slot else "无"
    return ToolResult.ok(f"已切换集合: {old} → {new_label}", data={"active_collection": new_label})


def _tool_get_current_filters(args: dict, ctx) -> ToolResult:
    """返回当前筛选状态。"""
    fs = ctx.filter_state
    active_count = sum(1 for v in fs.values() if v)
    return ToolResult.ok(
        f"当前有 {active_count} 个活跃筛选条件",
        data={"filter_state": fs, "active_filter_count": active_count},
    )


def _tool_get_statistics(args: dict, ctx) -> ToolResult:
    """返回翻译集合的详细统计。O8: 合并原 get_collection_summary 功能。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.ok("当前未加载翻译集合", data={"total": 0, "translated": 0})

    total = len(collection)
    translated = sum(1 for e in collection if e.translation)
    untranslated = total - translated

    stage_dist = Counter()
    category_dist = Counter()
    for e in collection:
        stage_dist[_STAGE_LABELS.get(e.stage, f"unknown({e.stage})")] += 1
        if e.context:
            cat = e.context.split(":")[0] if ":" in e.context else e.context[:4]
            category_dist[cat] += 1

    return ToolResult.ok(
        f"总计 {total} 条，已翻译 {translated} 条 ({100 * translated // max(total, 1)}%)",
        data={
            "total": total,
            "translated": translated,
            "untranslated": untranslated,
            "translation_rate": round(100 * translated / max(total, 1), 1),
            "stage_distribution": dict(stage_dist.most_common()),
            "category_distribution": dict(category_dist.most_common(20)),
        },
    )


# ── 项目管理 (Story 12) ───────────────────────────────────────

def _tool_list_local_projects(args: dict, ctx) -> ToolResult:
    """列出本地工作空间中的项目。"""
    projects = []
    try:
        workspace = ctx.workspace
        if workspace and hasattr(workspace, 'projects'):
            for p in workspace.projects:
                # m10/m21: 仅返回项目名(basename)，不暴露绝对路径
                projects.append({"name": getattr(p, 'name', '')})
    except Exception as exc:
        logger.warning("操作失败: %s", exc)
    return ToolResult.ok(f"共 {len(projects)} 个本地项目", data={"projects": projects})


def _tool_get_current_project(args: dict, ctx) -> ToolResult:
    """获取当前活跃项目信息。"""
    project = ctx.active_project
    if not project:
        return ToolResult.ok("当前无活跃项目", data={"active_project": None})
    return ToolResult.ok(data={
        "name": getattr(project, 'name', ''),
        "variant": ctx.active_variant,
        "collection": ctx.active_slot.label if ctx.active_slot else None,
    })


# ── 工具发现 ──────────────────────────────────────────────

def _tool_get_tool_help(args: dict, ctx) -> ToolResult:
    """获取工具的完整定义（参数Schema、返回值、规则）。"""
    tool_name = args.get("tool") or None
    namespace = args.get("namespace") or None
    from ..tool_registry import ToolRegistry
    result = ToolRegistry.build_tool_help(tool=tool_name, namespace=namespace)
    return ToolResult.ok(data={"help": result})


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "switch_collection": {
        "collection_name": {"type": "str", "required": False, "description": "集合名称(label)或key"},
        "slot_index": {"type": "int", "required": False, "description": "槽位索引（0-based）"},
    },
}


def _register_default_tools():
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("default", [
        {"name": "get_app_state", "display_name": "应用状态", "description": "①一站式全局状态概览，用于判断当前工作阶段。②参数: 无，只读。③返回: {active_collection, esp_file, eet_file, xt_file(仅文件名), project, variant(版本变体名如\"v1\"), filters, collection_count, has_active_collection, paratranz_configured}。规则: 文件路径仅返回文件名(安全设计); 此处\"阶段\"指项目工作阶段，与翻译条目stage字段不同。",
         "execute": _tool_get_app_state, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_app_state", {})},
        {"name": "list_collections", "display_name": "列出集合", "description": "①列出所有已加载翻译集合及基本信息。②参数: 无，只读。③返回: {collections: [{key, label, esp_name(非ESP来源为null), entry_count, is_active}]}。规则: 集合生命周期——parser action=create_slot创建, 创建时自动激活或switch_collection切换, 此处查询, UI移除; 筛选/作用域绑定到活跃集合。",
         "execute": _tool_list_collections, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("list_collections", {})},
        {"name": "switch_collection", "display_name": "切换集合", "description": "①切换活跃翻译集合，后续所有操作针对新集合。②参数: collection_name(key或label, 优先), slot_index(0-based数组位置, 从list_collections推算)。③返回: {active_collection}。规则: 同时传入时collection_name优先; 建议使用collection_name而非slot_index; write权限。",
         "execute": _tool_switch_collection, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("switch_collection", {})},
        {"name": "get_current_filters", "display_name": "当前筛选", "description": "①返回当前筛选条件完整快照。②参数: 无，只读。③返回: {filter_state: {stage(num[]: 0=未翻译/1=已翻译/2=有疑问/3=已检查/5=已审核/9=已锁定/-1=已隐藏, 4/6/7/8为ParaTranz预留), category, label, search_query, search_field(id/key/original/translation/context/all)}, active_filter_count}。规则: category通过get_statistics的category_distribution发现; label通过list_labels发现; 修改筛选用set_filters。",
         "execute": _tool_get_current_filters, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_current_filters", {})},
        {"name": "get_statistics", "display_name": "翻译统计", "description": "①全量统计(不受当前筛选影响)。②参数: 无，只读。③返回: {total, translated, untranslated, translation_rate(%), stage_distribution({\"未翻译\":120,...}), category_distribution({\"NPC_\":150,...}, top 20)}。规则: 用于概览; 筛选后的条目用get_visible_entries。",
         "execute": _tool_get_statistics, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_statistics", {})},
        # Story 12: 项目管理
        {"name": "list_local_projects", "display_name": "本地项目", "description": "①列出本地工作空间中的项目。②参数: 无，只读。③返回: {projects: [{name(仅目录名, 无完整路径)}]}。规则: workspace为本地项目目录, 每子目录为一个项目; 项目CRUD仅通过UI(无工具支持)。",
         "execute": _tool_list_local_projects, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("list_local_projects", {})},
        {"name": "get_current_project", "display_name": "当前项目", "description": "①轻量当前项目查询。②参数: 无，只读。③返回: {name, variant, collection}。规则: vs get_app_state——此工具仅返回项目信息(更轻量); get_app_state返回完整上下文含文件路径/筛选/API状态; 文件路径不在此返回(安全设计), 需用get_app_state。",
         "execute": _tool_get_current_project, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_current_project", {})},
        # 工具发现
        {"name": "get_tool_help", "display_name": "工具帮助", "description": (
            "①获取工具的完整定义（参数Schema、返回值、规则），用于在使用工具前了解其详细参数。"
            "②参数: tool(str,可选,工具名如'start_translation'); namespace(str,可选,命名空间如'translator',返回该空间所有工具完整定义,支持逗号分隔多个namespace)。"
            "③返回: 指定工具或namespace的完整参数表格与规则说明。"
            "规则: 1.推荐使用namespace批量查询,一次获取整组工具; "
            "2.不要凭目录摘要直接调用非预加载工具,必须通过本工具获取完整定义后再调用。"
        ),
         "execute": _tool_get_tool_help, "permission": "read",
         "parameters": {
             "tool": {"type": "str", "required": False, "description": "工具名，如'start_translation'"},
             "namespace": {"type": "str", "required": False, "description": "命名空间，如'translator'。支持逗号分隔多个，如'parser,translator'"},
         }},
    ])


_register_default_tools()
