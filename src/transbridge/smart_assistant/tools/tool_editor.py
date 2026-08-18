"""P0 编辑器工具 — 筛选 + 搜索 + 编辑 + 选择 + 批量标记 (editor namespace)。

Story 04 v2: 合并原 Story 04（筛选搜索）和 Story 05（编辑选择），
新增 set_stage(H3) + _selected_ids(H2) + new_stage参数(H4) + filter_entries复用(H8)。
Story 03B: 重构为 EditorController 类。
"""
from __future__ import annotations

from .base import ToolResult, filter_entries, require_collection, require_runtime_context, validate_params

_VALID_STAGES = {0, 1, 2, 3, 5, 9, -1}

# M2: _PARAM_SCHEMAS 必须在函数定义之前（供 @validate_params 装饰器使用）
_PARAM_SCHEMAS = {
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
    # Story 20: manage_entry_labels 合并 4→1
    "manage_entry_labels": {
        "action": {"type": "str", "required": True, "description": "操作: create/assign/unassign/batch_assign"},
        "name": {"type": "str", "required": False, "description": "标签名（create/assign/unassign 必填）"},
        "color": {"type": "str", "required": False, "description": "颜色 hex（仅 create，默认 #409EFF）"},
        "entry_ids": {"type": "list", "required": False, "description": "条目 ID 列表（assign/unassign 必填）"},
    },
    # Story 17: set_filters 合并 5→1
    "set_filters": {
        "stages": {"type": "list", "required": False, "description": "stage 值列表，None=保持，[]=清除"},
        "categories": {"type": "list", "required": False, "description": "分类名列表，None=保持，[]=清除"},
        "labels": {"type": "list", "required": False, "description": "标签名列表，None=保持，[]=清除"},
        "search_query": {"type": "str", "required": False, "description": "搜索关键词，None=保持，''=清除"},
        "search_field": {"type": "str", "required": False, "description": "搜索字段: id/key/original/translation/context/all，默认 original"},
        "clear": {"type": "bool", "required": False, "description": "是否先清除所有筛选再应用新值，默认 false"},
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
        """Story 17: 合并 5→1。统一筛选入口，6维度均可选。None=保持/[]=清除/clear=True=先清空再设置。"""
        clear = args.get("clear", False)
        stages = args.get("stages")
        categories = args.get("categories")
        labels = args.get("labels")
        search_query = args.get("search_query")
        search_field = args.get("search_field")

        if stages is not None and len(stages) > 0:
            invalid = [s for s in stages if s not in _VALID_STAGES]
            if invalid:
                return ToolResult.fail(f"无效的 stage 值: {invalid}，合法值: {sorted(_VALID_STAGES)}")

        if search_field is not None:
            VALID_FIELDS = ("id", "key", "original", "translation", "context", "all")
            if search_field not in VALID_FIELDS and search_field != "":
                return ToolResult.fail(f"无效的搜索字段: {search_field}，可选: {', '.join(VALID_FIELDS)}")

        if clear:
            ctx.clear_filters()

        changes = []
        if stages is not None:
            ctx.set_filter(stage=list(stages))
            changes.append(f"stages={stages}")
        if categories is not None:
            ctx.set_filter(category=list(categories))
            changes.append(f"categories={categories}")
        if labels is not None:
            ctx.set_filter(label=list(labels))
            changes.append(f"labels={labels}")
        if search_query is not None:
            ctx.set_filter(search_query=search_query)
            changes.append(f"search_query='{search_query}'")
        if search_field is not None:
            ctx.set_filter(search_field=search_field)
            changes.append(f"search_field='{search_field}'")

        if not changes:
            if clear:
                return ToolResult.ok("已清除全部筛选条件", data=ctx.filter_state)
            return ToolResult.ok("未修改任何筛选条件", data={"unchanged": True})

        prefix = "已清除并重新设置筛选: " if clear else "已更新筛选: "
        result = ToolResult.ok(prefix + ", ".join(changes), data=ctx.filter_state)
        result.tool_suggestions = ["get_visible_entries", "get_statistics"]
        return result

    # ── 数据查询 ──────────────────────────────────────────────────

    def get_visible_entries(self, args: dict, ctx, collection) -> ToolResult:
        """获取当前筛选条件下可见的条目列表（分页）。H8: 复用 filter_entries。"""
        limit = min(args.get("limit", 50), 200)
        offset = max(args.get("offset", 0), 0)

        filter_state = ctx.filter_state
        entry_labels = getattr(ctx, 'entry_labels', None)  # M1 联动: 传入 entry_labels
        # M7: 每次分页从零过滤整个 collection，翻 N 页则扫描 N 次。后续可引入缓存层。
        results = filter_entries(collection, filter_state, entry_labels=entry_labels)
        total = len(results)

        page = results[offset:offset + limit]
        entries = [
            {
                "key": e.key,        # 主标识（LLM 请用此值传给 entry_id/entry_ids 参数）
                "id": e.id,          # 辅助标识（跨 ParaTranz 同步可能变化）
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
            result.tool_suggestions = ["get_visible_entries", "set_filters"]
        return result

    # ── 选择 ──────────────────────────────────────────────────────

    def select_entries(self, args: dict, ctx) -> ToolResult:
        """选择/取消选择条目。H2: 操作独立 _selected_ids 集合，与标签系统隔离。"""
        entry_ids = args["entry_ids"]
        action = args.get("action", "select")

        if action not in ("select", "deselect", "clear"):
            return ToolResult.fail(f"无效操作: {action}，可选: select, deselect, clear")

        count = ctx.select_entries(entry_ids, action)
        selected = list(ctx.selected_ids) if hasattr(ctx, 'selected_ids') else []
        action_names = {"select": "选中", "deselect": "取消选中", "clear": "清空选择"}
        return ToolResult.ok(
            f"{action_names.get(action, action)}完成，当前已选 {count} 条",
            data={"selected_count": count, "selected_ids": selected},
        )

    # ── 编辑 ──────────────────────────────────────────────────────

    def edit_translation(self, args: dict, ctx, collection) -> ToolResult:
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

    def set_stage(self, args: dict, ctx, collection) -> ToolResult:
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

    def _resolve_label_id(self, label_name: str, ctx) -> str | None:
        """根据标签名查找标签 ID。返回 lid 或 None（标签不存在时）。

        M51: 先构建 name→id 查找字典，O(n) 一次建表后 O(1) 查询。
        M27: 消除 assign/remove/batch 三处重复查找循环。
        """
        label_lib = getattr(ctx, 'label_library', None) or {}
        # M51: 构建 name→id 字典避免每次线性扫描
        name_to_id: dict[str, str] = {v.get("name", ""): k for k, v in label_lib.items() if v.get("name")}
        return name_to_id.get(label_name)

    def list_labels(self, args: dict, ctx) -> ToolResult:
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

    # ── Story 20: 统一标签管理（合并 4→1）─────────────────────────

    def manage_entry_labels(self, args: dict, ctx, collection=None) -> ToolResult:
        """Story 20: 合并 create_label/assign_label/remove_label/batch_assign_label → 统一入口。action 参数裁决。"""
        action = args["action"].strip().lower()
        if action not in ("create", "assign", "unassign", "batch_assign"):
            return ToolResult.fail(f"无效的 action: {action}，可选: create/assign/unassign/batch_assign")

        if action == "create":
            name = args.get("name", "").strip()
            if not name:
                return ToolResult.fail("标签名不能为空")
            color = args.get("color", "#409EFF")
            import uuid
            lid = uuid.uuid4().hex[:8]
            def _mutate():
                if not hasattr(ctx, 'label_library') or ctx.label_library is None:
                    ctx.label_library = {}
                ctx.label_library[lid] = {"name": name, "color": color}
                if hasattr(ctx, 'label_data_changed'):
                    ctx.label_data_changed.emit()
            ctx.safe_mutate(_mutate)
            return ToolResult.ok(f"已创建标签: {name}", data={"label_id": lid, "name": name, "color": color})

        # assign / unassign / batch_assign 共用参数校验
        if collection is None:
            return ToolResult.fail("需要活跃集合，请先加载翻译数据")

        label_name = args.get("name", "").strip()
        if not label_name:
            return ToolResult.fail("请提供标签名 (name)")
        lid = self._resolve_label_id(label_name, ctx)
        if lid is None:
            return ToolResult.fail(f"标签不存在: {label_name}")

        if action == "assign":
            entry_ids = args.get("entry_ids", [])
            if not entry_ids:
                return ToolResult.fail("请提供 entry_ids")
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
            return ToolResult.ok(f"已为 {len(entry_ids)} 条条目分配标签 '{label_name}'",
                                data={"assigned_count": len(entry_ids)})

        elif action == "unassign":
            entry_ids = args.get("entry_ids", [])
            if not entry_ids:
                return ToolResult.fail("请提供 entry_ids")
            entry_labels_read = getattr(ctx, 'entry_labels', None) or {}
            removed = sum(1 for eid in entry_ids if eid in entry_labels_read and lid in entry_labels_read[eid])
            def _mutate():
                el = getattr(ctx, 'entry_labels', None) or {}
                for eid in entry_ids:
                    if eid in el and lid in el[eid]:
                        el[eid].discard(lid)
                if hasattr(ctx, 'label_data_changed'):
                    ctx.label_data_changed.emit()
            ctx.safe_mutate(_mutate)
            return ToolResult.ok(f"已从 {removed} 条条目移除标签 '{label_name}'",
                                data={"removed_count": removed})

        elif action == "batch_assign":
            filter_state = ctx.filter_state
            entry_labels_read = getattr(ctx, 'entry_labels', None) or {}
            entries = filter_entries(collection, filter_state, entry_labels=entry_labels_read)
            _filtered_ids = [e.key for e in entries]
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
                f"已为筛选范围内 {len(entries)} 条条目批量分配标签 '{label_name}'",
                data={"assigned_count": len(entries), "filter_total": len(entries)})


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
    return _editor_ctrl.manage_entry_labels(args, ctx, collection)


# ── 注册 ──────────────────────────────────────────────────────


def _register_editor_tools():
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("editor", [
        {"name": "set_filters", "display_name": "设置筛选", "description": "①设置条目表格筛选条件，多维度自由组合，仅传需修改维度，未传维度保持不变。②参数: stages(可选,list,0/1/2/3/5/9/-1), categories(可选,list), labels(可选,list), search_query(可选,str), search_field(可选,id/key/original/translation/context/all), clear(可选,bool,默认false,先清空再应用)。None=保持当前值，[]=清除。③返回: 筛选状态快照{stage,category,label,search_query,search_field}，无变更时返回{unchanged:true}。规则: 修改前用get_current_filters预检当前筛选状态；后续get_visible_entries/get_statistics/batch_assign均受此筛选影响。示例: set_filters stages=[0] 只看未翻译；set_filters clear=true stages=[1] search_field=all 全新筛选",
         "execute": _tool_set_filters, "permission": "read", "parameters": _PARAM_SCHEMAS.get("set_filters", {})},
        {"name": "get_visible_entries", "display_name": "获取可见条目", "description": "①获取当前筛选下可见条目列表(分页)。原文和译文均硬编码截断至200字符，无全文本检索通道。②参数: limit(可选,int,默认50,最大200), offset(可选,int,默认0)。③返回: {entries:[{key,id,original,translation,stage}], total_count, truncated}。规则: key字段为条目标识符，传给其他工具的entry_id/entry_ids参数；先用set_filters设筛选条件；truncated=true表示还有更多条目；统计信息用get_statistics，勿遍历全部分页",
         "execute": _tool_get_visible_entries, "permission": "read", "parameters": _PARAM_SCHEMAS.get("get_visible_entries", {})},
        {"name": "select_entries", "display_name": "选择条目", "description": "①选中/取消选中条目，选择集合为独立临时存储，与标签系统隔离。②参数: action(可选,select/deselect/clear,默认select), entry_ids(select/deselect时必填,key值列表,clear时无需传)。③返回: {selected_count, selected_ids}。规则: 选择状态为临时不持久化；不影响标签(标签用manage_entry_labels)；典型流程: get_visible_entries取key→select_entries action=select→set_stage批量标记",
         "execute": _tool_select_entries, "permission": "write", "parameters": _PARAM_SCHEMAS.get("select_entries", {})},
        {"name": "edit_translation", "display_name": "编辑翻译", "description": "①修改单条条目的翻译文本，可同时调整翻译阶段。与set_stage区别: 本工具改译文+可选stage，单条；set_stage只改阶段，可批量。②参数: entry_id(必填,key值), new_translation(必填), new_stage(可选,0/1/2/3/5/9/-1,4/6/7/8为ParaTranz预留不可用)。不传则保持原stage。③返回: {entry_id, old_translation, new_translation, stage, stage_changed}(译文截断至100字)。错误: 条目不存在→fail；stage值非法→fail，提示合法值列表",
         "execute": _tool_edit_translation, "permission": "write", "parameters": _PARAM_SCHEMAS.get("edit_translation", {})},
        {"name": "set_stage", "display_name": "批量设置阶段", "description": "①批量设置多条条目的翻译阶段标记，不修改译文。与edit_translation区别: 本工具只改阶段、可批量；edit_translation改文本、单条。②参数: entry_ids(必填,key值列表,空列表静默返回{updated_count:0}), stage(必填,0/1/2/3/5/9/-1,4/6/7/8预留不可用)。③返回: 全部成功→{updated_count}；部分条目未找到→额外含{not_found}。规则: 流转路径0(未翻译)→1(已翻译)→2(有疑问)→3(已检查)→5(已审核)；9(已锁定)/-1(已隐藏)为特殊状态不参与常规流转。典型: get_visible_entries→select_entries→set_stage stage=3",
         "execute": _tool_set_stage, "permission": "write", "parameters": _PARAM_SCHEMAS.get("set_stage", {})},
        # Story 20: manage_entry_labels 合并 4→1
        {"name": "list_labels", "display_name": "列出标签", "description": "①列出所有已定义标签及其信息和使用次数(只读)。②参数: 无。③返回: {labels:[{id,name,color,count}]}。规则: 标签库为空/未初始化时返回空列表不报错；所有标签操作(assign/unassign/batch_assign)使用name字段，id仅供内部追踪不应传给其他工具；创建/修改标签用manage_entry_labels",
         "execute": _tool_list_labels, "permission": "read", "parameters": _PARAM_SCHEMAS.get("list_labels", {})},
        {"name": "manage_entry_labels", "display_name": "管理条目标签",
         "description": "①管理条目标签(数据层)——创建标签、分配/移除条目标签、批量分配，通过action参数选择操作类型。与set_filters区别: 本工具改变条目标签关系(数据层)，set_filters控制表格显示(视图层)。②参数: action(必填,create/assign/unassign/batch_assign), name(必填,标签名), color(可选,hex,仅create,默认#409EFF), entry_ids(assign/unassign必填,key值列表)。③返回: create→{label_id,name,color}；assign→{assigned_count}；unassign→{removed_count}；batch_assign→{assigned_count,filter_total}(需用户确认，取消则assigned_count=0)。规则: label_id仅create时返回，后续assign/unassign/batch_assign使用name而非id；批量分配前先用set_filters确认范围；先list_labels确保标签存在。示例: create标签→batch_assign→set_filters按标签筛选→get_visible_entries",
         "execute": _tool_manage_entry_labels, "permission": "write", "require_confirmation": True,
         "parameters": _PARAM_SCHEMAS.get("manage_entry_labels", {})},
    ])


_register_editor_tools()
