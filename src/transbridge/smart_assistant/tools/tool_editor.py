# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Expose workbench filters, table selection, entry edits and labels to the assistant."""

from __future__ import annotations

from transbridge.application.translation.entry_alias import ai_entry_key
from transbridge.application.workbench_filters import FilterState, updated_filter_state
from transbridge.application.workbench_labels import label_catalog

from ._editor_edits import execute_entry_edit
from ._editor_labels import manage_entry_labels
from .base import ToolResult, require_collection, require_runtime_context, validate_params
from .diagnostics import technical_error
from .input_prerequisites import original_available

# M2: _PARAM_SCHEMAS 必须在函数定义之前（供 @validate_params 装饰器使用）
_PARAM_SCHEMAS = {
    "get_visible_entries": {
        "limit": {
            "type": "int",
            "required": False,
            "description": "Maximum number of entries to return; default 50, maximum 200",
        },
        "offset": {"type": "int", "required": False, "description": "Pagination offset; default 0"},
    },
    "select_entries": {
        "entry_ids": {"type": "list", "required": True, "description": "Entry ID list"},
        "action": {"type": "str", "required": False, "description": "Action: select/deselect/clear; default select"},
    },
    "edit_translation": {
        "entry_id": {"type": "str", "required": True, "description": "Entry ID"},
        "new_translation": {"type": "str", "required": True, "description": "New translation text"},
        "new_stage": {
            "type": "int",
            "required": False,
            "description": "Optional stage; otherwise entering text advances an untranslated entry to translated",
        },
    },
    "set_stage": {
        "entry_ids": {"type": "list", "required": True, "description": "Entry ID list"},
        "stage": {
            "type": "int",
            "required": True,
            "description": (
                "Target stage: 0=untranslated, 1=translated, 2=question, 3=checked, 5=reviewed, 9=locked, -1=hidden"
            ),
        },
    },
    # Story 08: 标签管理
    "list_labels": {},
    # Story 20: manage_entry_labels 合并 4→1
    "manage_entry_labels": {
        "action": {"type": "str", "required": True, "description": "Action: create/assign/unassign/batch_assign"},
        "name": {"type": "str", "required": False, "description": "Label name; required for create/assign/unassign"},
        "color": {"type": "str", "required": False, "description": "Hex color for create only; default #409EFF"},
        "entry_ids": {"type": "list", "required": False, "description": "Entry ID list; required for assign/unassign"},
    },
    # Story 17: set_filters 合并 5→1
    "set_filters": {
        "stages": {
            "type": "list",
            "required": False,
            "description": "Stage values; None keeps the current value and [] clears it",
        },
        "categories": {
            "type": "list",
            "required": False,
            "description": "Category names; None keeps the current value and [] clears it",
        },
        "labels": {
            "type": "list",
            "required": False,
            "description": "Label IDs or unique names; None keeps the current value and [] clears it",
        },
        "search_query": {
            "type": "str",
            "required": False,
            "description": "Search text; None keeps the current value and an empty string clears it",
        },
        "search_field": {
            "type": "str",
            "required": False,
            "description": "Search field: key/original/translation/all; default original",
        },
        "clear": {
            "type": "bool",
            "required": False,
            "description": "Clear all filters before applying new values; default false",
        },
    },
}


# ── EditorController ──────────────────────────────────────────


class EditorController:
    """编辑器控制器：统一管理 editor 命名空间的工具逻辑。"""

    def __init__(self, app_context=None, task_manager=None):
        self._ctx = app_context
        self._task_mgr = task_manager

    # ── 筛选工具 ──────────────────────────────────────────────────

    def set_filters(self, args: dict, ctx) -> ToolResult:
        """Apply one atomic update to the captured workbench table."""
        from .types import ExecutionContext
        from .workbench_rows import current_workbench_source

        execution = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)
        app = execution.app_context

        def update():
            current_workbench_source(execution, execution._target_collection)
            current = ctx.filter_state
            state = updated_filter_state(current, args, getattr(ctx, "label_library", None))
            try:
                unchanged = state == FilterState.from_mapping(current).to_mapping()
            except ValueError:
                # clear=True can replace a saved search whose field has been retired.
                unchanged = False

            app.filter_state = state
            return state, unchanged

        try:
            gate = execution.assistant_gate
            commit = update if gate is None else lambda: gate.change_workbench_filter(execution, update)
            state, unchanged = execution.safe_mutate_wait(commit)
            if "filter_state" in vars(execution):
                object.__setattr__(execution, "filter_state", state)
        except (ValueError, RuntimeError) as exc:
            return ToolResult.fail(technical_error(exc), error_category="input")
        result = ToolResult.ok("已应用工作台筛选", data={**state, "unchanged": unchanged})
        result.tool_suggestions = ["get_visible_entries", "get_statistics"]
        return result

    # ── 数据查询 ──────────────────────────────────────────────────

    def get_visible_entries(self, args: dict, ctx, collection) -> ToolResult:
        """Page through the workbench filter applied to the captured source."""
        from .workbench_rows import filtered_tool_rows

        limit = max(0, min(args.get("limit", 50), 200))
        offset = max(args.get("offset", 0), 0)

        filter_state = ctx.filter_state
        entry_labels = getattr(ctx, "entry_labels", None)
        total, page = 0, []
        try:
            for entry, _, _ in filtered_tool_rows(ctx, collection, filter_state, entry_labels):
                if offset <= total < offset + limit:
                    page.append(entry)
                total += 1
        except ValueError as exc:
            return ToolResult.fail(technical_error(exc), error_category="input")
        entries = [
            {
                "key": ai_entry_key(e),  # 操作标识；冲突项携带原文，源文件 key 不变
                "id": e.id,  # 辅助标识（跨 ParaTranz 同步可能变化）
                "original": e.original[:200] if e.original else "",
                "original_known": original_available(e),
                "translation": e.translation[:200] if e.translation else "",
                "stage": e.stage,
            }
            for e in page
        ]

        truncated = (offset + limit) < total
        msg = f"显示 {len(entries)} 条"
        if truncated:
            msg += f"（共 {total} 条，已截断）"

        result = ToolResult.ok(
            msg,
            data={"entries": entries, "total_count": total, "truncated": truncated},
        )
        result.truncated = truncated
        result.pagination = {
            "page": (offset // limit) + 1 if limit > 0 else 1,
            "page_size": limit,
            "total_count": total,
            "returned_count": len(entries),
            "has_more": truncated,
        }
        if entries and not truncated:
            result.tool_suggestions = ["select_entries", "edit_translation", "set_stage"]
        elif truncated:
            result.tool_suggestions = ["get_visible_entries", "set_filters"]
        return result

    # ── 选择 ──────────────────────────────────────────────────────

    def select_entries(self, args: dict, ctx) -> ToolResult:
        """Apply selection to the active workbench table on its owning thread."""
        from .types import ExecutionContext
        from .workbench_rows import current_workbench_source

        entry_ids = args["entry_ids"]
        action = args.get("action", "select")

        if action not in ("select", "deselect", "clear"):
            return ToolResult.fail(f"无效操作: {action}，可选: select, deselect, clear")
        if action != "clear" and not all(isinstance(key, str) and key for key in entry_ids):
            return ToolResult.fail("请选择含非空词条标识的列表。")

        execution = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)
        app = execution.app_context

        def apply():
            current_workbench_source(execution, execution._target_collection)
            if FilterState.from_mapping(ctx.filter_state) != FilterState.from_mapping(app.filter_state):
                raise ValueError("工作台筛选已改变，请核对当前显示范围后重新选择。")
            captured = getattr(ctx, "selected_entry_ids", ())
            if captured and action != "clear" and not set(entry_ids).issubset(captured):
                raise ValueError("所选词条超出本次请求的选区。")
            count = app.select_entries(entry_ids, action)
            entries = getattr(app, "selected_entries", None)
            ids = list(app.selected_ids) if entries is None else [ai_entry_key(entry) for entry in entries]
            return count, ids

        try:
            count, selected = execution.safe_mutate_wait(apply)
        except (ValueError, RuntimeError, TimeoutError) as exc:
            return ToolResult.fail(technical_error(exc))
        action_names = {"select": "选中", "deselect": "取消选中", "clear": "清空选择"}
        return ToolResult.ok(
            f"{action_names.get(action, action)}完成，当前已选 {count} 条",
            data={"selected_count": count, "selected_ids": selected},
        )

    # ── 编辑 ──────────────────────────────────────────────────────

    def edit_translation(self, args: dict, ctx, collection) -> ToolResult:
        return execute_entry_edit("edit_translation", args, ctx, collection)

    def set_stage(self, args: dict, ctx, collection) -> ToolResult:
        return execute_entry_edit("set_stage", args, ctx, collection)

    # ── 标签管理 (Story 08) ───────────────────────────────────────

    def list_labels(self, args: dict, ctx) -> ToolResult:
        """列出所有已定义的标签。"""
        from .workbench_rows import tool_scope_statistics

        try:
            library = getattr(ctx, "label_library", None)
            counts = tool_scope_statistics(ctx).labels
        except (ValueError, RuntimeError, OSError) as exc:
            return ToolResult.fail(technical_error(exc))
        if not library:
            message = "标签库未初始化" if library is None else "标签库为空"
            return ToolResult.ok(f"{message}，请先创建标签", data={"labels": []})
        labels = label_catalog(library, counts)
        return ToolResult.ok(f"共 {len(labels)} 个标签", data={"labels": labels})


# ── 无状态 controller + 模块级兼容 wrapper ───────────────────────

_editor_ctrl = EditorController()


@require_runtime_context
@validate_params(_PARAM_SCHEMAS["set_filters"])
def _tool_set_filters(args: dict, ctx) -> ToolResult:
    return _editor_ctrl.set_filters(args, ctx)


@require_runtime_context
@require_collection
@validate_params(_PARAM_SCHEMAS["get_visible_entries"])
def _tool_get_visible_entries(args: dict, ctx, collection=None) -> ToolResult:
    return _editor_ctrl.get_visible_entries(args, ctx, collection)


@require_runtime_context
@validate_params(_PARAM_SCHEMAS["select_entries"])
def _tool_select_entries(args: dict, ctx) -> ToolResult:
    return _editor_ctrl.select_entries(args, ctx)


@require_runtime_context
@require_collection
@validate_params(_PARAM_SCHEMAS["edit_translation"])
def _tool_edit_translation(args: dict, ctx, collection=None) -> ToolResult:
    return _editor_ctrl.edit_translation(args, ctx, collection)


@require_runtime_context
@require_collection
@validate_params(_PARAM_SCHEMAS["set_stage"])
def _tool_set_stage(args: dict, ctx, collection=None) -> ToolResult:
    return _editor_ctrl.set_stage(args, ctx, collection)


@require_runtime_context
def _tool_list_labels(args: dict, ctx) -> ToolResult:
    return _editor_ctrl.list_labels(args, ctx)


@require_runtime_context
@validate_params(_PARAM_SCHEMAS["manage_entry_labels"])
def _tool_manage_entry_labels(args: dict, ctx, collection=None) -> ToolResult:
    return manage_entry_labels(args, ctx, getattr(ctx, "collection", None) if collection is None else collection)


# ── 注册 ──────────────────────────────────────────────────────


def _register_editor_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "editor",
        [
            {
                "name": "set_filters",
                "display_name": "设置筛选",
                "description": (
                    "①Set composable entry-table filters; omitted dimensions remain unchanged. "
                    "②Arguments: optional stages, "
                    "categories, labels, search_query, "
                    "search_field=key/original/translation/all, and clear=false. "
                    "None keeps a value and [] clears it. "
                    "③Returns the workbench filter fields and unchanged. Inspect with get_current_filters first. "
                    "These filters control visible entries and batch label assignments."
                ),
                "execute": _tool_set_filters,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("set_filters", {}),
            },
            {
                "name": "get_visible_entries",
                "display_name": "获取可见条目",
                "description": (
                    "①Return a paginated list of entries matching current filters; "
                    "source and translation text are capped at "
                    "200 characters. ②Arguments: limit=50 (maximum 200), offset=0. ③Returns "
                    "{entries:[{key,id,original,translation,stage}], total_count, truncated}. "
                    "Rules: pass key values as "
                    "entry_id/entry_ids; set filters first; use get_statistics instead of "
                    "traversing every page for counts."
                ),
                "execute": _tool_get_visible_entries,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_visible_entries", {}),
            },
            {
                "name": "select_entries",
                "display_name": "选择条目",
                "description": (
                    "①Select, deselect, or clear actual workbench table rows, including offscreen rows. ②Arguments: "
                    "action=select/deselect/clear and entry_ids for select/deselect. "
                    "③Returns {selected_count, selected_ids}. "
                    "Rules: selection is not persisted and does not change labels; typical flow is "
                    "get_visible_entries→select_entries→set_stage."
                ),
                "execute": _tool_select_entries,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("select_entries", {}),
            },
            {
                "name": "edit_translation",
                "display_name": "编辑翻译",
                "description": (
                    "①Edit one entry's translation and optionally its stage; set_stage "
                    "changes stages in batches without "
                    "editing text. ②Arguments: entry_id, new_translation, optional new_stage in 0/1/2/3/5/9/-1. "
                    "③Returns {entry_id, old_translation, new_translation, stage, stage_changed}, "
                    "with translation fields "
                    "capped at 100 characters."
                ),
                "execute": _tool_edit_translation,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("edit_translation", {}),
            },
            {
                "name": "set_stage",
                "display_name": "批量设置阶段",
                "description": (
                    "①Set the translation stage for multiple entries without changing text. "
                    "②Arguments: entry_ids and stage "
                    "in 0/1/2/3/5/9/-1. ③Returns {updated_count} and, for partial matches, "
                    "{not_found}. Rules: normal flow "
                    "is 0 untranslated→1 translated→2 question→3 checked→5 reviewed; "
                    "9 locked and -1 hidden are special "
                    "states."
                ),
                "execute": _tool_set_stage,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_stage", {}),
            },
            # Story 20: manage_entry_labels 合并 4→1
            {
                "name": "list_labels",
                "display_name": "列出标签",
                "description": (
                    "①List all defined workbench labels, counting usage in the current request source, "
                    "including offscreen entries. ②No arguments. ③Returns "
                    "{labels:[{id,name,color,count}]}. Rules: an empty or uninitialized "
                    "library returns an empty list; label "
                    "operations use name, while id is internal; create or modify labels with manage_entry_labels."
                ),
                "execute": _tool_list_labels,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("list_labels", {}),
            },
            {
                "name": "manage_entry_labels",
                "display_name": "管理条目标签",
                "description": (
                    "①Manage entry-label relationships by creating labels, assigning or removing them, "
                    "or batch assigning "
                    "through action. Unlike set_filters, this changes data rather than table display. ②Arguments: "
                    "action=create/assign/unassign/batch_assign, name, optional create color, and entry_ids for "
                    "assign/unassign. ③Returns create→{label_id,name,color}, assign→{assigned_count}, "
                    "unassign→{removed_count}, batch_assign→{assigned_count,filter_total}. "
                    "Rules: later operations use label "
                    "name, not id; confirm filters before batch assignment and use list_labels to verify existence."
                ),
                "execute": _tool_manage_entry_labels,
                "permission": "write",
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("manage_entry_labels", {}),
            },
        ],
    )


_register_editor_tools()
