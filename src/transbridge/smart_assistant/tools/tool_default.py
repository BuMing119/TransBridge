# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""P0 状态查询工具 — 集合/项目/统计查询 (default namespace)。

Story 07: get_collection_summary deprecated(O8)，功能合并到 get_statistics。
"""

from __future__ import annotations

import logging
from pathlib import PureWindowsPath

from transbridge.application.workbench_filters import FilterState
from transbridge.converter.translation_entry import STAGE_LABELS

from .base import ToolResult
from .diagnostics import technical_error
from .workbench_rows import tool_scope_statistics

logger = logging.getLogger(__name__)


def _tool_get_app_state(args: dict, ctx) -> ToolResult:
    """Read one coherent snapshot from the same context used by the workbench."""
    from .types import ExecutionContext
    from .workbench_rows import check_workbench_source

    app = ctx.app_context if isinstance(ctx, ExecutionContext) else ctx
    if app is None:
        return ToolResult.fail("工作台上下文未初始化。", error_code="RUNTIME_CONTEXT_REQUIRED")

    def snapshot():
        check_workbench_source(ctx, app)
        slot = app.active_slot
        return {
            "active_collection": slot.content_name(app.active_key) if slot else None,
            "esp_file": PureWindowsPath(slot.esp_path).name if slot and slot.esp_path else None,
            "eet_file": PureWindowsPath(slot.eet_path).name if slot and slot.eet_path else None,
            "xt_file": PureWindowsPath(slot.xt_path).name if slot and slot.xt_path else None,
            "project": app.project_name,
            "variant": app.active_variant,
            "filters": FilterState.from_mapping(ctx.filter_state).to_mapping(),
            "collection_count": len(app.slots),
            "has_active_collection": slot is not None and slot.collection is not None,
            "paratranz_configured": app.config.credential_configured,
        }

    try:
        data = ctx.safe_mutate_wait(snapshot) if isinstance(ctx, ExecutionContext) else snapshot()
    except ValueError as error:
        return ToolResult.fail(str(error), error_category="input")
    return ToolResult.ok(data=data)


def _read_workbench(ctx, read):
    from .types import ExecutionContext

    if not isinstance(ctx, ExecutionContext):
        return read(ctx)
    app = ctx.app_context

    def snapshot():
        if app is None or ctx._target_version_identity != getattr(app, "active_version_identity", None):
            return ToolResult.fail("活动工程版本已变化，请重新读取工作台。")
        return read(app)

    return ctx.safe_mutate_wait(snapshot)


def _tool_list_collections(args: dict, ctx) -> ToolResult:
    """列出所有已加载的翻译集合。"""
    return _read_workbench(ctx, _list_collections)


def _list_collections(ctx):
    collections = []
    for key, slot in ctx.slots.items():
        col = slot.collection
        collections.append({
            "key": key,
            "label": slot.label,
            "esp_name": PureWindowsPath(slot.esp_path).name if slot.esp_path else None,
            "entry_count": len(col) if col else 0,
            "is_active": key == ctx.active_key,
        })
    return ToolResult.ok(f"已加载 {len(collections)} 个集合", data={"collections": collections})


def _tool_switch_collection(args: dict, ctx) -> ToolResult:
    """切换活跃翻译集合。"""
    from .types import ExecutionContext

    if isinstance(ctx, ExecutionContext):
        app = ctx.app_context

        def activate():
            if ctx._target_version_identity != app.active_version_identity:
                return ToolResult.fail("活动工程版本已变化，请重新选择来源。")
            token = getattr(ctx, "cancellation_token", None)
            if token is not None:
                token.raise_if_cancelled()
            return _switch_source(args, app)

        return ctx.safe_mutate_wait(activate)
    return _switch_source(args, ctx)


def _switch_source(args, ctx):
    collection_name = args.get("collection_name", "")
    slot_index = args.get("slot_index")

    if slot_index is not None:
        keys = list(ctx.slots.keys())
        if slot_index < 0 or slot_index >= len(keys):
            return ToolResult.fail(f"slot_index 超出范围: 0~{len(keys) - 1}")
        target_key = keys[slot_index]
    elif collection_name:
        if collection_name in ctx.slots:
            target_key = collection_name
        else:
            matches = [key for key, slot in ctx.slots.items() if slot.label == collection_name]
            if not matches:
                return ToolResult.fail(f"未找到集合: {collection_name}")
            if len(matches) > 1:
                return ToolResult.fail("多个来源同名，请使用来源键或 slot_index。")
            target_key = matches[0]
    else:
        return ToolResult.fail("请指定 collection_name 或 slot_index")

    old = ctx.active_slot.label if ctx.active_slot else "无"
    ctx.activate_slot(target_key)
    new_label = ctx.active_slot.label if ctx.active_slot else "无"
    return ToolResult.ok(f"已切换集合: {old} → {new_label}", data={"active_collection": new_label})


def _tool_get_current_filters(args: dict, ctx) -> ToolResult:
    """返回当前筛选状态。"""
    try:
        fs = FilterState.from_mapping(ctx.filter_state).to_mapping()
    except ValueError as exc:
        return ToolResult.fail(str(exc), error_category="input")
    active_count = sum(1 for v in fs.values() if v)
    return ToolResult.ok(
        f"当前有 {active_count} 个活跃筛选条件",
        data={"filter_state": fs, "active_filter_count": active_count},
    )


def _tool_get_statistics(args: dict, ctx) -> ToolResult:
    try:
        statistics = tool_scope_statistics(ctx)
    except (ValueError, RuntimeError, OSError) as exc:
        return ToolResult.fail(technical_error(exc))
    summary = statistics.summary
    return ToolResult.ok(
        f"总计 {summary.total} 条，已翻译 {summary.completed} 条 ({summary.translation_rate}%)",
        data={
            "total": summary.total,
            "translated": summary.completed,
            "untranslated": summary.untranslated,
            "needs_review": summary.needs_review,
            "hidden": summary.hidden,
            "translation_rate": summary.translation_rate,
            "stage_distribution": {
                STAGE_LABELS.get(stage, f"unknown({stage})"): count for stage, count in statistics.stages.most_common()
            },
            "category_distribution": dict(statistics.categories.most_common()),
            "label_distribution": dict(statistics.labels),
        },
    )


# ── 项目管理 (Story 12) ───────────────────────────────────────


def _tool_list_local_projects(args: dict, ctx) -> ToolResult:
    """列出本地工作空间中的项目。"""
    catalog = ctx.project_catalog
    if catalog is None:
        return ToolResult.fail("工程目录服务不可用。")
    try:
        projects = [{"name": item.name} for item in catalog.list_projects().projects]
    except Exception as exc:
        logger.exception("Cannot read the current project catalog")
        return ToolResult.fail(f"读取工程目录失败：{exc}")
    return ToolResult.ok(f"共 {len(projects)} 个本地项目", data={"projects": projects})


def _tool_get_current_project(args: dict, ctx) -> ToolResult:
    """获取当前活跃项目信息。"""
    return _read_workbench(ctx, _current_project)


def _current_project(ctx):
    project_name = ctx.project_name
    if not project_name:
        return ToolResult.ok("当前无活跃项目", data={"active_project": None})
    return ToolResult.ok(
        data={
            "name": project_name,
            "variant": ctx.active_variant,
            "collection": ctx.active_slot.label if ctx.active_slot else None,
        }
    )


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

    ToolRegistry.register_tools(
        "default",
        [
            {
                "name": "get_app_state",
                "display_name": "应用状态",
                "description": (
                    "①Read the current workbench project, version, and loaded source. ②No arguments. "
                    "③Returns {active_collection, esp_file, eet_file, xt_file, project, variant, filters, "
                    "collection_count, has_active_collection, paratranz_configured}. "
                    "④Files are shown by name. Filters follow the admitted request. "
                    "The ParaTranz flag reports whether the desktop has a credential configured."
                ),
                "execute": _tool_get_app_state,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_app_state", {}),
            },
            {
                "name": "list_collections",
                "display_name": "列出集合",
                "description": (
                    "①List all loaded translation collections and basic information. "
                    "②No arguments; read-only. ③Returns "
                    "{collections: [{key, label, esp_name(null for non-ESP sources), entry_count, is_active}]}. Rules: "
                    "parser action=create_slot registers a source in the current project; "
                    "switch_collection changes it; this tool "
                    "queries it; the UI removes it. Filters and scope belong to the active collection."
                ),
                "execute": _tool_list_collections,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("list_collections", {}),
            },
            {
                "name": "switch_collection",
                "display_name": "切换集合",
                "description": (
                    "①Switch the active workbench source. ②Use collection_name (key or unique label) or "
                    "slot_index (zero-based position in list_collections). When both are provided, slot_index "
                    "takes precedence. ③Returns {active_collection}."
                ),
                "execute": _tool_switch_collection,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("switch_collection", {}),
            },
            {
                "name": "get_current_filters",
                "display_name": "当前筛选",
                "description": (
                    "①Return a complete snapshot of current filters. ②No arguments; "
                    "read-only. ③Returns {filter_state: "
                    "{stage(num[]: 0=untranslated/1=translated/2=question/3=checked/5=reviewed/9=locked/-1=hidden; "
                    "4/6/7/8 reserved for ParaTranz), category, label, search_all, search_key, "
                    "search_orig, search_trans, focus_labeled}, active_filter_count}. "
                    "Rules: discover categories "
                    "from the workbench and label IDs via list_labels; "
                    "modify filters with set_filters."
                ),
                "execute": _tool_get_current_filters,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_current_filters", {}),
            },
            {
                "name": "get_statistics",
                "display_name": "翻译统计",
                "description": (
                    "①Return full statistics unaffected by current filters. "
                    "②No arguments; read-only. ③Returns {total, "
                    "translated, untranslated, needs_review, hidden, translation_rate(%), "
                    "stage_distribution, category_distribution, label_distribution (by label ID)}. "
                    "Counts use workbench stages and category names. "
                    "Rule: use for an overview; use get_visible_entries "
                    "for filtered entries."
                ),
                "execute": _tool_get_statistics,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_statistics", {}),
            },
            # Story 12: 项目管理
            {
                "name": "list_local_projects",
                "display_name": "本地项目",
                "description": (
                    "①List projects in the local workspace. ②No arguments; read-only. "
                    "③Returns {projects: [{name}]}. Project names come from the desktop project catalog."
                ),
                "execute": _tool_list_local_projects,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("list_local_projects", {}),
            },
            {
                "name": "get_current_project",
                "display_name": "当前项目",
                "description": (
                    "①Return lightweight current-project information. ②No arguments; "
                    "read-only. ③Returns {name, variant, "
                    "collection}. Rule: unlike get_app_state, this returns only project information; "
                    "use get_app_state for "
                    "full context including filenames, filters, and API state."
                ),
                "execute": _tool_get_current_project,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_current_project", {}),
            },
            # 工具发现
            {
                "name": "get_tool_help",
                "display_name": "工具帮助",
                "description": (
                    "①Return a tool's complete definition, including parameter schema, return value, "
                    "and rules, before use."
                    "②Arguments: optional tool name such as 'start_translation'; optional "
                    "namespace such as 'translator', "
                    "with comma-separated namespaces supported."
                    "③Returns the complete parameter table and rules for the requested tool or namespace."
                    "Rules: prefer namespace queries to retrieve a whole group at once. "
                    "Do not call a non-preloaded tool "
                    "from its directory summary; retrieve its complete definition first."
                ),
                "execute": _tool_get_tool_help,
                "permission": "read",
                "parameters": {
                    "tool": {"type": "str", "required": False, "description": "Tool name, such as 'start_translation'"},
                    "namespace": {
                        "type": "str",
                        "required": False,
                        "description": (
                            "Namespace, such as 'translator'; comma-separated values such as 'parser,translator' are "
                            "supported"
                        ),
                    },
                },
            },
        ],
    )


_register_default_tools()
