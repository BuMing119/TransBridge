# Story 08: P1 标签管理工具 (editor namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: +前置依赖Story 03标签数据(B1联动) +复用_filter_entries(H8)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult` + 装饰器
- Story 04 → `tool_editor.py` 模块骨架（`_PARAM_SCHEMAS` + `_register_editor_tools`）
- FR7.11 标签系统（已实现）→ `ctx._label_library` + `ctx._entry_labels`

### 引用的架构决策
- ADR-008: 纯数据操作 — 标签分配修改 `ctx._entry_labels`，不操作 UI
- ADR-012: write 权限

## 验收标准

- [ ] `list_labels` — 列出所有标签（name/color/count），permission: read
- [ ] `create_label` — 参数 `name: str, color: str`，permission: write
- [ ] `assign_label` — 参数 `entry_ids: list[str], label_name: str`，permission: write
- [ ] `remove_label` — 参数 `entry_ids: list[str], label_name: str`，permission: write
- [ ] `batch_assign_label` — 参数 `label_name: str`，批量分配给当前筛选范围内所有条目，permission: write, require_confirmation: true
- [ ] 全部注册到 `editor` namespace

## 数据流

```
list_labels()
    → 读取 ctx._label_library
    → 统计每个标签的使用次数（遍历 ctx._entry_labels）
    → 返回 ToolResult.ok(data={"labels": [{name, color, count}, ...]})

create_label(name="待审核", color="#FF5722")
    → 检查名称是否已存在
    → 在 ctx._label_library 中创建新标签
    → 返回 ToolResult.ok(data={"label_id": id, "name": name})

assign_label(entry_ids=["abc", "def"], label_name="待审核")
    → 查找 label_id
    → 对每个 entry_id: ctx._entry_labels[entry_id].add(label_id)
    → 收集失败的 entry_ids 到 failed_items
    → 返回 ToolResult (success="partial" 如果有部分失败)

batch_assign_label(label_name="待审核")
    → 获取当前 filter_state 匹配的所有条目 ID
    → 对全部匹配条目分配标签
    → 返回操作影响的条目数
```

## 实现步骤

### 步骤 1: `list_labels` + `create_label`

**涉及文件**: `tools/tool_editor.py`（追加）

**实现要点**:
- `list_labels`: 遍历 `ctx._label_library`，为每个标签计算 `entry_labels` 中引用次数，过滤 `__agent_selected__` 系统标签
- `create_label`: 自动生成 label_id（UUID 或 slug），默认颜色 "#2196F3"

**边界条件**:
- 标签名重复 → `ToolResult.fail("标签已存在: {name}")`
- 标签库为空 → `list_labels` 返回空列表

---

### 步骤 2: `assign_label` + `remove_label`

**涉及文件**: 同上

**实现要点**:
- `assign_label`: 查找 label_id → 对每个 entry_id 执行 `ctx._entry_labels.setdefault(entry_id, set()).add(label_id)`
- `remove_label`: 查找 label_id → 对每个 entry_id 执行 `ctx._entry_labels.get(entry_id, set()).discard(label_id)`
- 部分失败收集到 `failed_items`

**边界条件**:
- entry_id 不存在 → 加入 failed_items
- label_name 不存在 → 直接返回 fail
- entry_ids 超过 500 个 → 不做特殊限制，但护栏输出校验限制 100KB

---

### 步骤 3: `batch_assign_label`

**涉及文件**: 同上

**实现要点**:
- 读取 `ctx.filter_state` → 获取当前筛选匹配的所有条目 ID
- 对全部匹配条目执行与 `assign_label` 相同的逻辑
- `require_confirmation: true` — 操作可能影响大量条目

**边界条件**:
- 无筛选条件 → 影响全部条目（高风险，require_confirmation 保护）
- 筛选结果 0 条 → `ToolResult.fail("当前筛选条件下无匹配条目")`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_editor.py` | 追加 | 5 个标签管理工具 + 注册调用追加 |

## 风险与注意事项

- **注意**: `batch_assign_label` 不额外创建新参数，直接读取 `ctx.filter_state` 获取范围。Agent 应先调用 filter_* 设置范围再 batch_assign
- **注意**: 系统保留标签 `__agent_selected__` 在 `list_labels` 中过滤
