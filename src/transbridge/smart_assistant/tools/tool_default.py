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
        "collection_name": {"type": "str", "required": False, "description": "Collection label or key"},
        "slot_index": {"type": "int", "required": False, "description": "Zero-based slot index"},
    },
}


def _register_default_tools():
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("default", [
        {"name": "get_app_state", "display_name": "应用状态", "description": "①Return a comprehensive global state overview for identifying the current workflow phase. ②No arguments; read-only. ③Returns {active_collection, esp_file, eet_file, xt_file(filename only), project, variant(version variant such as \"v1\"), filters, collection_count, has_active_collection, paratranz_configured}. Rules: file paths expose filenames only; workflow phase is distinct from an entry's stage field.",
         "execute": _tool_get_app_state, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_app_state", {})},
        {"name": "list_collections", "display_name": "列出集合", "description": "①List all loaded translation collections and basic information. ②No arguments; read-only. ③Returns {collections: [{key, label, esp_name(null for non-ESP sources), entry_count, is_active}]}. Rules: parser action=create_slot creates and activates a collection; switch_collection changes it; this tool queries it; the UI removes it. Filters and scope belong to the active collection.",
         "execute": _tool_list_collections, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("list_collections", {})},
        {"name": "switch_collection", "display_name": "切换集合", "description": "①Switch the active translation collection; subsequent operations target the new collection. ②Arguments: preferred collection_name (key or label), or slot_index (zero-based position from list_collections). ③Returns {active_collection}. Rules: collection_name wins when both are supplied; prefer it over slot_index; write permission.",
         "execute": _tool_switch_collection, "permission": "write",
         "parameters": _PARAM_SCHEMAS.get("switch_collection", {})},
        {"name": "get_current_filters", "display_name": "当前筛选", "description": "①Return a complete snapshot of current filters. ②No arguments; read-only. ③Returns {filter_state: {stage(num[]: 0=untranslated/1=translated/2=question/3=checked/5=reviewed/9=locked/-1=hidden; 4/6/7/8 reserved for ParaTranz), category, label, search_query, search_field(id/key/original/translation/context/all)}, active_filter_count}. Rules: discover categories via get_statistics.category_distribution and labels via list_labels; modify filters with set_filters.",
         "execute": _tool_get_current_filters, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_current_filters", {})},
        {"name": "get_statistics", "display_name": "翻译统计", "description": "①Return full statistics unaffected by current filters. ②No arguments; read-only. ③Returns {total, translated, untranslated, translation_rate(%), stage_distribution({\"untranslated\":120,...}), category_distribution({\"NPC_\":150,...}, top 20)}. Rule: use for an overview; use get_visible_entries for filtered entries.",
         "execute": _tool_get_statistics, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_statistics", {})},
        # Story 12: 项目管理
        {"name": "list_local_projects", "display_name": "本地项目", "description": "①List projects in the local workspace. ②No arguments; read-only. ③Returns {projects: [{name(directory name only, no full path)}]}. Rule: each workspace subdirectory is a project; project CRUD is available only through the UI.",
         "execute": _tool_list_local_projects, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("list_local_projects", {})},
        {"name": "get_current_project", "display_name": "当前项目", "description": "①Return lightweight current-project information. ②No arguments; read-only. ③Returns {name, variant, collection}. Rule: unlike get_app_state, this returns only project information; use get_app_state for full context including filenames, filters, and API state.",
         "execute": _tool_get_current_project, "permission": "read",
         "parameters": _PARAM_SCHEMAS.get("get_current_project", {})},
        # 工具发现
        {"name": "get_tool_help", "display_name": "工具帮助", "description": (
            "①Return a tool's complete definition, including parameter schema, return value, and rules, before use."
            "②Arguments: optional tool name such as 'start_translation'; optional namespace such as 'translator', with comma-separated namespaces supported."
            "③Returns the complete parameter table and rules for the requested tool or namespace."
            "Rules: prefer namespace queries to retrieve a whole group at once. Do not call a non-preloaded tool from its directory summary; retrieve its complete definition first."
        ),
         "execute": _tool_get_tool_help, "permission": "read",
         "parameters": {
             "tool": {"type": "str", "required": False, "description": "Tool name, such as 'start_translation'"},
             "namespace": {"type": "str", "required": False, "description": "Namespace, such as 'translator'; comma-separated values such as 'parser,translator' are supported"},
         }},
    ])


_register_default_tools()
