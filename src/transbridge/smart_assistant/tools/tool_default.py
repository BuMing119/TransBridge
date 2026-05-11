"""P0 状态查询工具 — 集合/项目/统计查询 (default namespace)。

Story 07: get_collection_summary deprecated(O8)，功能合并到 get_statistics。
"""
from __future__ import annotations

from collections import Counter

from .base import ToolResult


_STAGE_LABELS = {0: "未翻译", 1: "已翻译", 2: "有疑问", 3: "已检查", 5: "已审核", 9: "已锁定", -1: "已隐藏"}


def _tool_get_app_state(args: dict, ctx) -> ToolResult:
    """返回当前应用状态摘要。"""
    import os
    slot = ctx.active_slot
    # m4: 只暴露文件名，不泄露绝对路径
    # m7: 安全访问 active_project，兼容多种类型
    project_name = None
    if hasattr(ctx, 'active_project') and ctx.active_project is not None:
        project_name = getattr(ctx.active_project, 'name', None)
        if project_name is None and isinstance(ctx.active_project, dict):
            project_name = ctx.active_project.get('name')
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
    })


def _tool_list_collections(args: dict, ctx) -> ToolResult:
    """列出所有已加载的翻译集合。"""
    collections = []
    for key, slot in ctx.slots.items():
        col = slot.collection
        collections.append({
            "key": key,
            "label": slot.label,
            "esp_path": slot.esp_path,
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
                projects.append({"name": getattr(p, 'name', ''), "path": getattr(p, 'path', '')})
    except Exception:
        pass
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


# ── 注册 ──────────────────────────────────────────────────────

_PARAM_SCHEMAS = {
    "switch_collection": {
        "collection_name": {"type": "str", "required": False, "description": "集合名称(label)或key"},
        "slot_index": {"type": "int", "required": False, "description": "槽位索引（0-based）"},
    },
}


def _register_default_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    tools = [
        ("get_app_state", "应用状态", "返回当前应用状态（集合/项目/版本/筛选/API连接）", _tool_get_app_state, "read"),
        ("list_collections", "列出集合", "列出所有已加载的翻译集合及基本信息", _tool_list_collections, "read"),
        ("switch_collection", "切换集合", "切换活跃翻译集合（按名称或索引）", _tool_switch_collection, "write"),
        ("get_current_filters", "当前筛选", "返回当前筛选状态", _tool_get_current_filters, "read"),
        ("get_statistics", "翻译统计", "返回集合详细统计（总数/翻译率/stage分布/分类分布）。O8:合并get_collection_summary", _tool_get_statistics, "read"),
        # Story 12: 项目管理
        ("list_local_projects", "本地项目", "列出本地工作空间中的项目", _tool_list_local_projects, "read"),
        ("get_current_project", "当前项目", "获取当前活跃项目信息", _tool_get_current_project, "read"),
    ]

    for name, display_name, description, execute, permission in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}),
            execute=execute, permission=permission,
        ), namespace="default")


_register_default_tools()
