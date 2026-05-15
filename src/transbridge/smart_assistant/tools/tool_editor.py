"""P0 编辑器工具 — 筛选 + 搜索 + 编辑 + 选择 + 批量标记 (editor namespace)。

Story 04 v2: 合并原 Story 04（筛选搜索）和 Story 05（编辑选择），
新增 set_stage(H3) + _selected_ids(H2) + new_stage参数(H4) + filter_entries复用(H8)。
"""
from __future__ import annotations

from .base import ToolResult, filter_entries, require_collection, validate_params

_VALID_STAGES = {0, 1, 2, 3, 5, 9, -1}

# M2: _PARAM_SCHEMAS 必须在函数定义之前（供 @validate_params 装饰器使用）
_PARAM_SCHEMAS = {
    "filter_by_stage": {
        "stages": {"type": "list", "required": True, "description": "stage 值列表: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏"},
    },
    "filter_by_category": {
        "categories": {"type": "list", "required": True, "description": "分类名列表如 ['NPC_', 'INFO', 'BOOK']"},
    },
    "filter_by_label": {
        "label_names": {"type": "list", "required": True, "description": "标签名列表"},
    },
    "search_entries": {
        "query": {"type": "str", "required": True, "description": "搜索关键词"},
        "field": {"type": "str", "required": False, "description": "搜索字段: id/key/original/translation/context/all，默认 original"},
    },
    "get_visible_entries": {
        "limit": {"type": "int", "required": False, "description": "返回条数上限，默认 50，最大 200"},
        "offset": {"type": "int", "required": False, "description": "偏移量，默认 0"},
    },
    "select_entries": {
        "entry_ids": {"type": "list", "required": True, "description": "条目 ID 列表"},
        "action": {"type": "str", "required": False, "description": "操作: select/deselect/clear，默认 select"},
    },
    "edit_translation": {
        "entry_id": {"type": "str", "required": True, "description": "条目 ID"},
        "new_translation": {"type": "str", "required": True, "description": "新译文"},
        "new_stage": {"type": "int", "required": False, "description": "新翻译阶段（可选，不传则保持原 stage）"},
    },
    "set_stage": {
        "entry_ids": {"type": "list", "required": True, "description": "条目 ID 列表"},
        "stage": {"type": "int", "required": True, "description": "目标 stage: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏"},
    },
    # Story 08: 标签管理
    "list_labels": {},
    "create_label": {
        "name": {"type": "str", "required": True, "description": "标签名"},
        "color": {"type": "str", "required": False, "description": "颜色(hex)，默认 #409EFF"},
    },
    "assign_label": {
        "entry_ids": {"type": "list", "required": True, "description": "条目 ID 列表"},
        "label_name": {"type": "str", "required": True, "description": "标签名"},
    },
    "remove_label": {
        "entry_ids": {"type": "list", "required": True, "description": "条目 ID 列表"},
        "label_name": {"type": "str", "required": True, "description": "要移除的标签名"},
    },
    "batch_assign_label": {
        "label_name": {"type": "str", "required": True, "description": "标签名（批量分配给当前筛选范围内所有条目）"},
    },
}


# ── 筛选工具 ──────────────────────────────────────────────────

@validate_params(_PARAM_SCHEMAS["filter_by_stage"])
def _tool_filter_by_stage(args: dict, ctx) -> ToolResult:
    """按翻译阶段筛选。stage 值: 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏"""
    stages = args["stages"]
    invalid = [s for s in stages if s not in _VALID_STAGES]
    if invalid:
        return ToolResult.fail(f"无效的 stage 值: {invalid}，合法值: {sorted(_VALID_STAGES)}")
    ctx.set_filter(stage=list(stages))
    result = ToolResult.ok(f"已按阶段筛选: {stages}", data={"stages": stages})
    result.tool_suggestions = ["get_visible_entries", "get_statistics"]
    return result


@validate_params(_PARAM_SCHEMAS["filter_by_category"])
def _tool_filter_by_category(args: dict, ctx) -> ToolResult:
    """按分类筛选（如 NPC_、INFO、BOOK 等）。"""
    categories = args["categories"]
    ctx.set_filter(category=list(categories))
    result = ToolResult.ok(f"已按分类筛选: {categories}", data={"categories": categories})
    result.tool_suggestions = ["get_visible_entries", "get_statistics"]
    return result


@validate_params(_PARAM_SCHEMAS["filter_by_label"])
def _tool_filter_by_label(args: dict, ctx) -> ToolResult:
    """按标签名筛选。"""
    label_names = args["label_names"]
    ctx.set_filter(label=list(label_names))
    result = ToolResult.ok(f"已按标签筛选: {label_names}", data={"labels": label_names})
    result.tool_suggestions = ["get_visible_entries", "get_statistics"]
    return result


@validate_params(_PARAM_SCHEMAS["search_entries"])
def _tool_search_entries(args: dict, ctx) -> ToolResult:
    """按关键词搜索条目。field: id/key/original/translation/context/all，默认 original。"""
    query = args["query"]
    field = args.get("field", "original")
    # "text" 向后兼容，映射到 "original"
    if field == "text":
        field = "original"
    VALID_FIELDS = ("id", "key", "original", "translation", "context", "all")
    if field not in VALID_FIELDS:
        return ToolResult.fail(
            f"无效的搜索字段: {field}，可选: {', '.join(VALID_FIELDS)}"
        )
    ctx.set_filter(search_query=query, search_field=field)
    result = ToolResult.ok(f"已搜索: '{query}' (字段: {field})", data={"query": query, "field": field})
    result.tool_suggestions = ["get_visible_entries"]
    return result


def _tool_clear_all_filters(args: dict, ctx) -> ToolResult:
    """清除所有筛选条件。"""
    ctx.clear_filters()
    result = ToolResult.ok("已清除所有筛选条件", data={"filters_cleared": True})
    result.tool_suggestions = ["get_visible_entries", "get_statistics", "filter_by_stage"]
    return result


# ── 数据查询 ──────────────────────────────────────────────────

@require_collection
@validate_params(_PARAM_SCHEMAS["get_visible_entries"])
def _tool_get_visible_entries(args: dict, ctx, collection) -> ToolResult:
    """获取当前筛选条件下可见的条目列表（分页）。H8: 复用 filter_entries。"""
    limit = min(args.get("limit", 50), 200)
    offset = max(args.get("offset", 0), 0)

    filter_state = ctx.filter_state
    entry_labels = getattr(ctx, 'entry_labels', None)  # M1 联动: 传入 entry_labels
    results = filter_entries(collection, filter_state, entry_labels=entry_labels)
    total = len(results)

    page = results[offset:offset + limit]
    entries = [
        {
            "id": e.id,
            "key": e.key,
            "original": e.original[:200] if e.original else "",
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
        result.tool_suggestions = ["get_visible_entries", "search_entries"]
    return result


# ── 选择 ──────────────────────────────────────────────────────

@validate_params(_PARAM_SCHEMAS["select_entries"])
def _tool_select_entries(args: dict, ctx) -> ToolResult:
    """选择/取消选择条目。H2: 操作独立 _selected_ids 集合，与标签系统隔离。"""
    entry_ids = args["entry_ids"]
    action = args.get("action", "select")

    if action not in ("select", "deselect", "clear"):
        return ToolResult.fail(f"无效操作: {action}，可选: select, deselect, clear")

    count = ctx.select_entries(entry_ids, action)
    action_names = {"select": "选中", "deselect": "取消选中", "clear": "清空选择"}
    return ToolResult.ok(f"{action_names.get(action, action)}完成，当前已选 {count} 条", data={"selected_count": count})


# ── 编辑 ──────────────────────────────────────────────────────

@require_collection
@validate_params(_PARAM_SCHEMAS["edit_translation"])
def _tool_edit_translation(args: dict, ctx, collection) -> ToolResult:
    """编辑单条翻译。H4: 不传 new_stage 时保持现有 stage 不变。"""
    entry_id = args["entry_id"]
    new_translation = args["new_translation"]
    new_stage = args.get("new_stage")

    entry = collection.get(entry_id)
    if entry is None:
        return ToolResult.fail(f"条目不存在: {entry_id}")

    old_translation = entry.translation
    entry.translation = new_translation

    old_stage = entry.stage
    if new_stage is not None:
        if new_stage not in _VALID_STAGES:
            return ToolResult.fail(f"无效的 stage 值: {new_stage}，合法值: {sorted(_VALID_STAGES)}")
        entry.stage = int(new_stage)

    # C10: 通知 UI 条目已修改（信号在主线程发射）
    ctx.safe_mutate(lambda: ctx.notify_collection_modified())

    return ToolResult.ok(
        f"已更新 {entry_id}",
        data={
            "entry_id": entry_id,
            "old_translation": old_translation[:100] if old_translation else "",
            "new_translation": new_translation[:100],
            "stage": entry.stage,
            "stage_changed": old_stage != entry.stage,
        },
    )


# ── 批量标记 ──────────────────────────────────────────────────

@require_collection
@validate_params(_PARAM_SCHEMAS["set_stage"])
def _tool_set_stage(args: dict, ctx, collection) -> ToolResult:
    """批量设置条目翻译阶段。H3: 填补批量标记缺口。

    NOTE(M9): 当前实现逐条遍历 entry_ids 设置 stage，无批处理优化。
    对于大批量条目（>1000条），逐条循环可能有性能影响。
    已知限制，后续可优化为批量 update。
    """
    entry_ids = args["entry_ids"]
    stage = args["stage"]

    if stage not in _VALID_STAGES:
        return ToolResult.fail(f"无效的 stage 值: {stage}，合法值: {sorted(_VALID_STAGES)}")
    if not entry_ids:
        return ToolResult.fail("entry_ids 不能为空")

    updated = 0
    not_found = []
    for eid in entry_ids:
        entry = collection.get(eid)
        if entry is None:
            not_found.append(eid)
            continue
        entry.stage = int(stage)
        updated += 1

    # C10: 通知 UI 条目已修改（信号在主线程发射）
    if updated > 0:
        ctx.safe_mutate(lambda: ctx.notify_collection_modified())

    failed = [{"entry_id": eid, "reason": "条目不存在"} for eid in not_found] if not_found else None
    if failed:
        return ToolResult.partial_ok(
            f"已将 {updated} 条条目设为 stage={stage}（{len(failed)} 条未找到）",
            data={"updated_count": updated, "not_found": not_found},
            failed_items=failed,
        )
    return ToolResult.ok(
        f"已将 {updated} 条条目设为 stage={stage}",
        data={"updated_count": updated},
    )


# ── 标签管理 (Story 08) ───────────────────────────────────────

def _resolve_label_id(label_name: str, ctx) -> str | None:
    """根据标签名查找标签 ID。返回 lid 或 None（标签不存在时）。

    M51: 先构建 name→id 查找字典，O(n) 一次建表后 O(1) 查询。
    M27: 消除 assign/remove/batch 三处重复查找循环。
    """
    label_lib = getattr(ctx, 'label_library', None) or {}
    # M51: 构建 name→id 字典避免每次线性扫描
    name_to_id: dict[str, str] = {v.get("name", ""): k for k, v in label_lib.items() if v.get("name")}
    return name_to_id.get(label_name)


def _tool_list_labels(args: dict, ctx) -> ToolResult:
    """列出所有已定义的标签。"""
    # m28: 区分"未初始化"和"空标签库"两种情况
    if not hasattr(ctx, 'label_library') or ctx.label_library is None:
        return ToolResult.ok("标签库未初始化，请先创建标签", data={"labels": []})
    label_lib = ctx.label_library
    if not label_lib:
        return ToolResult.ok("标签库为空，请先创建标签", data={"labels": []})
    entry_labels = getattr(ctx, 'entry_labels', {})
    labels = []
    for lid, info in label_lib.items():
        count = sum(1 for ids in entry_labels.values() if lid in ids)
        labels.append({"id": lid, "name": info.get("name", lid), "color": info.get("color", ""), "count": count})
    return ToolResult.ok(f"共 {len(labels)} 个标签", data={"labels": labels})


@validate_params(_PARAM_SCHEMAS["create_label"])
def _tool_create_label(args: dict, ctx) -> ToolResult:
    """创建新标签。"""
    name = args["name"].strip()
    color = args.get("color", "#409EFF")
    if not name:
        return ToolResult.fail("标签名不能为空")
    import uuid
    lid = uuid.uuid4().hex[:8]

    # C10: 将 shared state 写入调度到主线程
    def _mutate():
        if not hasattr(ctx, 'label_library') or ctx.label_library is None:
            ctx.label_library = {}
        ctx.label_library[lid] = {"name": name, "color": color}
        if hasattr(ctx, 'label_data_changed'):
            ctx.label_data_changed.emit()
    ctx.safe_mutate(_mutate)

    return ToolResult.ok(f"已创建标签: {name}", data={"label_id": lid, "name": name, "color": color})


@require_collection
@validate_params(_PARAM_SCHEMAS["assign_label"])
def _tool_assign_label(args: dict, ctx, collection) -> ToolResult:
    """为指定条目分配标签。"""
    entry_ids = args["entry_ids"]
    label_name = args["label_name"]
    lid = _resolve_label_id(label_name, ctx)
    if lid is None:
        return ToolResult.fail(f"标签不存在: {label_name}")

    assigned = len(entry_ids)

    # C10: 将 shared state 写入调度到主线程
    def _mutate():
        if not hasattr(ctx, 'entry_labels') or ctx.entry_labels is None:
            ctx.entry_labels = {}
        for eid in entry_ids:
            if eid not in ctx.entry_labels:
                ctx.entry_labels[eid] = set()
            ctx.entry_labels[eid].add(lid)
        if hasattr(ctx, 'label_data_changed'):
            ctx.label_data_changed.emit()
    ctx.safe_mutate(_mutate)

    return ToolResult.ok(f"已为 {assigned} 条条目分配标签 '{label_name}'", data={"assigned_count": assigned})


@require_collection
@validate_params(_PARAM_SCHEMAS["remove_label"])
def _tool_remove_label(args: dict, ctx, collection) -> ToolResult:
    """移除条目的标签。"""
    entry_ids = args["entry_ids"]
    label_name = args["label_name"]
    lid = _resolve_label_id(label_name, ctx)
    if lid is None:
        return ToolResult.fail(f"标签不存在: {label_name}")

    # 从当前状态计算移除数量（只读，用于返回值）
    entry_labels = getattr(ctx, 'entry_labels', None) or {}
    removed = sum(1 for eid in entry_ids if eid in entry_labels and lid in entry_labels[eid])

    # C10: 将 shared state 写入调度到主线程
    def _mutate():
        el = getattr(ctx, 'entry_labels', None) or {}
        for eid in entry_ids:
            if eid in el and lid in el[eid]:
                el[eid].discard(lid)
        if hasattr(ctx, 'label_data_changed'):
            ctx.label_data_changed.emit()
    ctx.safe_mutate(_mutate)

    return ToolResult.ok(f"已从 {removed} 条条目移除标签 '{label_name}'", data={"removed_count": removed})


@require_collection
@validate_params(_PARAM_SCHEMAS["batch_assign_label"])
def _tool_batch_assign_label(args: dict, ctx, collection) -> ToolResult:
    """批量分配标签——对当前筛选范围内所有条目分配标签。H8: 复用 filter_entries。"""
    label_name = args["label_name"]
    lid = _resolve_label_id(label_name, ctx)
    if lid is None:
        return ToolResult.fail(f"标签不存在: {label_name}")

    filter_state = ctx.filter_state
    entry_labels_read = getattr(ctx, 'entry_labels', None) or {}  # M1 联动
    entries = filter_entries(collection, filter_state, entry_labels=entry_labels_read)
    assigned = len(entries)

    # C10: 将 shared state 写入调度到主线程
    _filtered_ids = [e.id for e in entries]
    def _mutate():
        if not hasattr(ctx, 'entry_labels') or ctx.entry_labels is None:
            ctx.entry_labels = {}
        for eid in _filtered_ids:
            if eid not in ctx.entry_labels:
                ctx.entry_labels[eid] = set()
            ctx.entry_labels[eid].add(lid)
        if hasattr(ctx, 'label_data_changed'):
            ctx.label_data_changed.emit()
    ctx.safe_mutate(_mutate)

    return ToolResult.ok(
        f"已为筛选范围内 {assigned} 条条目批量分配标签 '{label_name}'",
        data={"assigned_count": assigned, "filter_total": len(entries)},
    )


# ── 注册 ──────────────────────────────────────────────────────


def _register_editor_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    tools = [
        ("filter_by_stage", "按阶段筛选", "按翻译阶段筛选表格条目（可多选 stage）", _tool_filter_by_stage, "read"),
        ("filter_by_category", "按分类筛选", "按分类名筛选条目（如 NPC_、INFO）", _tool_filter_by_category, "read"),
        ("filter_by_label", "按标签筛选", "按标签名筛选条目", _tool_filter_by_label, "read"),
        ("search_entries", "搜索条目", "按关键词在指定字段中搜索条目", _tool_search_entries, "read"),
        ("clear_all_filters", "清除筛选", "清除所有筛选条件，恢复显示全部条目", _tool_clear_all_filters, "read"),
        ("get_visible_entries", "获取可见条目", "获取当前筛选条件下可见的条目列表（分页，上限200）", _tool_get_visible_entries, "read"),
        ("select_entries", "选择条目", "选择/取消选择条目（使用独立选择集合，不影响标签系统）", _tool_select_entries, "write"),
        ("edit_translation", "编辑翻译", "编辑单条条目的翻译文本，可同时设置翻译阶段", _tool_edit_translation, "write"),
        ("set_stage", "批量设置阶段", "批量设置多条条目的翻译阶段", _tool_set_stage, "write"),
        # Story 08: 标签管理
        ("list_labels", "列出标签", "列出所有已定义的标签及其使用次数", _tool_list_labels, "read"),
        ("create_label", "创建标签", "创建新标签（名称+颜色）", _tool_create_label, "write"),
        ("assign_label", "分配标签", "为指定条目分配标签", _tool_assign_label, "write"),
        ("remove_label", "移除标签", "从指定条目移除标签", _tool_remove_label, "write"),
        ("batch_assign_label", "批量分配标签", "为当前筛选范围内所有条目批量分配标签（需确认）", _tool_batch_assign_label, "write"),
    ]

    for name, display_name, description, execute, permission in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}),
            execute=execute, permission=permission,
            require_confirmation=(name == "batch_assign_label"),
        ), namespace="editor")


_register_editor_tools()
