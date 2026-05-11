# Story 05: P0 编辑与选择工具 (editor namespace) — ⛔ 已废弃

**所属方案**: `plans/agent-tool-expansion/plan.md`
**状态**: ⛔ **废弃**（内容已合并至 Story 04）
**废弃日期**: 2026-05-11
**废弃原因**: v2 方案合并 Story 04 与 Story 05，统一负责「筛选→选择→编辑→标记」完整操作链，减少跨 Story 依赖

> 以下内容仅供历史参考。所有工具（select_entries、edit_translation）已移至 Story 04。
> 新增的 set_stage 批量标记工具同样归入 Story 04。select_entries 改为使用 _selected_ids 独立集合而非标签系统。

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult` + 装饰器
- Story 04 → `tool_editor.py` 模块骨架已存在

### 引用的架构决策
- ADR-008: 纯数据操作 — `edit_translation` 直接修改 `TranslationEntry.translation`，不操作 QLineEdit
- ADR-012: write 权限

## 验收标准

- [ ] `select_entries` — 参数 `entry_ids: list[str], action: str("select"/"deselect")`，通过标签系统标记选中
- [ ] `edit_translation` — 参数 `entry_id: str, new_translation: str`，修改 `TranslationEntry.translation` + 设 stage=2
- [ ] 两个工具注册到 `editor` namespace，permission 为 `write`

## 关键接口

```python
# tools/tool_editor.py 追加

_SELECTED_LABEL_NAME = "__agent_selected__"  # 系统保留标签名

def _tool_select_entries(args, ctx, collection) -> ToolResult:
    entry_ids = args["entry_ids"]
    action = args["action"]
    # 获取或创建 __agent_selected__ 标签
    # 对每个 entry_id: 分配/移除该标签
    # 返回 ToolResult.ok(data={"selected_count": N})

def _tool_edit_translation(args, ctx, collection) -> ToolResult:
    entry_id = args["entry_id"]
    new_translation = args["new_translation"]
    entry = collection.get(entry_id)
    if entry is None:
        return ToolResult.fail(f"条目不存在: {entry_id}")
    entry.translation = new_translation
    entry.stage = 2  # 有疑问（与 UI 编辑行为一致）
    return ToolResult.ok(f"已更新条目 {entry_id} 的译文")
```

## 实现步骤

### 步骤 1: 实现 `select_entries` 工具

**涉及文件**: `tools/tool_editor.py`（追加）

**实现要点**:
- 使用系统保留标签 `__agent_selected__` 标记选中状态
- `action="select"` → 分配 `__agent_selected__` 标签给 entry_ids
- `action="deselect"` → 移除标签
- 返回选中条目数量

**边界条件**:
- entry_id 不存在 → 跳过并收集到 failed_items，返回 `partial` 状态
- action 非法值 → `ToolResult.fail("无效的操作: {action}，合法值: select/deselect")`

---

### 步骤 2: 实现 `edit_translation` 工具

**涉及文件**: 同上

**实现要点**:
- 通过 `collection.get(entry_id)` 查找条目
- 直接赋值 `entry.translation = new_translation` + `entry.stage = 2`
- 触发 `collection_changed` 信号（如果已有）

**边界条件**:
- entry_id 不存在 → `ToolResult.fail("条目不存在: {entry_id}")`
- new_translation 为空字符串 → 合法操作（清空译文）
- stage=9（已锁定）或 stage=-1（已隐藏）的条目 → 允许编辑（工具层面不限制），但 message 中提醒

---

### 步骤 3: 注册两个工具

**涉及文件**: 同上

**注册代码**: 在 `_register_editor_tools()` 中追加注册，permission="write"

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_editor.py` | 追加 | select_entries + edit_translation + 注册 |

## 风险与注意事项

- **注意**: `edit_translation` 修改 stage 为 2 的行为与 UI 行内编辑一致，确保 Agent 编辑和手动编辑产生相同的 stage 状态
- **注意**: `__agent_selected__` 为系统保留标签名，在标签管理工具中需过滤或特殊标记
