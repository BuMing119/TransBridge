# Story 20: manage_entry_labels 合并 (4→1)

**Epic**: agent-tool-expansion
**优先级**: P0
**净减**: -3 工具
**风险**: 低
**依赖**: S17（同文件 `tool_editor.py`，必须在 S17 之后串行）
**状态**: 已方案

> ⚠️ **用户裁决**：`create_label` 也纳入合并（覆盖架构师最初"独立保留"的建议）。`action` 参数支持全部 4 种标签操作。

## 范围

合并 `create_label` + `assign_label` + `remove_label` + `batch_assign_label` → `manage_entry_labels`，`action` 参数区分操作类型。

## 验收标准

- [ ] `manage_entry_labels` 注册到 `editor` namespace
- [ ] 参数 `action: str`（必传，enum: `create`/`assign`/`unassign`/`batch_assign`）
- [ ] 按 action 的条件参数：
  - `create`: `name: str`（必传）, `color: str`（可选，默认 `#409EFF`）
  - `assign`: `entry_ids: list[str]`（必传）, `label_name: str`（必传）
  - `unassign`: `entry_ids: list[str]`（必传）, `label_name: str`（必传）
  - `batch_assign`: `label_name: str`（必传）— 对当前筛选范围内所有条目分配标签，复用 `filter_entries()`
- [ ] `batch_assign` action 设置 `require_confirmation=True`
- [ ] 旧 4 个工具保留 deprecated wrapper，不注册到 ToolRegistry
- [ ] 现有标签相关测试适配新工具名

## 实现步骤

1. 实现 `_tool_manage_entry_labels()`：
   - 校验 `action` 值
   - 按 action 分发到现有实现的内联逻辑：
     - `create` → 在 `ctx.label_library` 中新建标签定义
     - `assign` → 在 `ctx.entry_labels` 中添加条目-标签关联
     - `unassign` → 在 `ctx.entry_labels` 中移除条目-标签关联
     - `batch_assign` → `filter_entries()` → 批量 add → `safe_mutate` → `label_data_changed.emit`
2. 将现有 4 个标签工具函数改为 deprecated wrapper（转发 + `DeprecationWarning`）
3. 更新 `_PARAM_SCHEMAS`：
   - 新增 `"manage_entry_labels": {...}`
   - 保留旧 4 个 schema（wrapper 引用）
4. 更新 `_register_editor_tools()`：注册 `manage_entry_labels`（write），移除旧 4 个标签工具注册
5. 运行标签相关测试

## 涉及文件

- `tools/tool_editor.py`

## 参数设计

```python
_PARAM_SCHEMAS["manage_entry_labels"] = {
    "action": {"type": "str", "required": True, "description": "操作类型: create(创建标签定义)/assign(分配标签给条目)/unassign(移除条目标签)/batch_assign(批量分配给筛选范围)"},
    "name": {"type": "str", "required": False, "description": "[create] 标签名称"},
    "color": {"type": "str", "required": False, "description": "[create] 标签颜色(hex)，默认 #409EFF"},
    "label_name": {"type": "str", "required": False, "description": "[assign/unassign/batch_assign] 目标标签名"},
    "entry_ids": {"type": "list", "required": False, "description": "[assign/unassign] 条目ID列表"},
}
```

## 工具描述

```
管理翻译条目的标签。action 参数决定操作类型:
- create: 创建新标签（需 name）
- assign: 为指定条目添加标签（需 entry_ids + label_name）
- unassign: 从指定条目移除标签（需 entry_ids + label_name）
- batch_assign: 为当前筛选范围内所有条目添加标签（需 label_name，需确认）

与 set_filters 的区别: manage_entry_labels 修改标签数据本身，set_filters 用于按标签筛选显示。
```

## 边界条件

- `action="create"` 但标签名已存在 → 返回错误
- `action="assign"` 但 `entry_ids` 中有不存在的 ID → `partial=True`，`failed_items` 列出
- `action="unassign"` 但条目没有该标签 → 静默跳过（幂等）
- `action="batch_assign"` 但当前筛选范围为空 → 返回 "筛选范围内无条目"
