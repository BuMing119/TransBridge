# Story 17: set_filters 合并 (5→1)

**Epic**: agent-tool-expansion
**优先级**: P0
**净减**: -4 工具
**风险**: 低
**依赖**: S16（注册样板改造后的 tool_editor.py）
**状态**: 已方案

## 范围

合并 `filter_by_stage` / `filter_by_category` / `filter_by_label` / `search_entries` / `clear_all_filters` → `set_filters`。五个工具操作同一对象 `ctx.filter_state` 的不同 key，合并到单一入口。

## 验收标准

- [ ] `set_filters` 注册到 `editor` namespace，参数全部可选：
  - `stages: list[int] | None`
  - `categories: list[str] | None`
  - `labels: list[str] | None`
  - `search_query: str | None`
  - `search_field: str | None`（默认 `"original"`）
  - `clear: bool | None`（默认 `False`）
- [ ] `None` = 不修改该维度（保持现有筛选值）
- [ ] `[]`（空列表）= 清除该维度筛选
- [ ] `clear=True` = 清除所有筛选后应用新值；`clear=False`（默认）= 叠加到现有筛选
- [ ] 旧 5 个工具函数保留 deprecated wrapper（仅转发到 `set_filters`），添加 `DeprecationWarning`，不注册到 ToolRegistry
- [ ] `get_visible_entries` 不受影响（复用 `filter_entries()`）
- [ ] 现有测试适配新工具名

## 实现步骤

1. 在 `tool_editor.py` 实现 `_tool_set_filters()`：
   - 解析全部 6 个可选参数
   - 若 `clear=True`，先重置 `ctx.filter_state` 为默认值
   - 逐个维度检查：参数为 `None` → 跳过；参数为 `[]` → 清除该维度；参数有值 → 设置
   - 通过 `ctx.safe_mutate` + `ctx.filter_changed.emit` 通知 UI
2. 编写 5 个 deprecated wrapper：
   - `_tool_filter_by_stage(args, ctx)` → `_tool_set_filters({"stages": args["stages"]}, ctx)`
   - `_tool_filter_by_category(args, ctx)` → `_tool_set_filters({"categories": args["categories"]}, ctx)`
   - `_tool_filter_by_label(args, ctx)` → `_tool_set_filters({"labels": args["label_names"]}, ctx)`
   - `_tool_search_entries(args, ctx)` → `_tool_set_filters({"search_query": args["query"], "search_field": args.get("field")}, ctx)`
   - `_tool_clear_all_filters(args, ctx)` → `_tool_set_filters({"clear": True}, ctx)`
   - 每个 wrapper 添加 `warnings.warn("xxx is deprecated, use set_filters instead", DeprecationWarning)`
3. 更新 `_PARAM_SCHEMAS`：
   - 新增 `"set_filters": {...}`
   - 旧 5 个 schema 保留（deprecated wrapper 仍需要参数校验引用）
4. 更新 `_register_editor_tools()`：注册 `set_filters`（read），移除旧 5 个工具的注册
5. 运行 editor 相关测试，更新工具名引用

## 涉及文件

- `tools/tool_editor.py`

## 参数设计

```python
_PARAM_SCHEMAS["set_filters"] = {
    "stages": {"type": "list", "required": False, "description": "stage 值列表，None=保持，[]=清除"},
    "categories": {"type": "list", "required": False, "description": "分类名列表，None=保持，[]=清除"},
    "labels": {"type": "list", "required": False, "description": "标签名列表，None=保持，[]=清除"},
    "search_query": {"type": "str", "required": False, "description": "搜索关键词，None=保持，''=清除"},
    "search_field": {"type": "str", "required": False, "description": "搜索字段: id/key/original/translation/context/all，默认 original"},
    "clear": {"type": "bool", "required": False, "description": "是否先清除所有筛选再应用新值，默认 false"},
}
```

## 边界条件

- `clear=True` + 其他参数同时传 → 先清空再设置
- 所有参数都是 `None` 且 `clear` 未传 → 无操作，返回 "未修改任何筛选条件"
- `stages` 含无效值（如 4, 6-8）→ 校验拒绝
- `search_field` 不在白名单 → 校验拒绝
