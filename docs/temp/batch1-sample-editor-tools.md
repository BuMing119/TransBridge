# Editor 工具 — LLM 使用参考（Batch 1 Sample）

> 格式参照 `claude-code-tools-reference.md`，纯使用面，无开发信息。
> 条目标识符：所有 `entry_id` / `entry_ids` 参数使用 `get_visible_entries` 返回的 **`key`** 字段值。

---

## 1. set_filters

**描述:**
设置条目表格的筛选条件。多个维度可自由组合，只需传需要修改的维度，未传的维度保持当前值不变。

何时用：
- 需要查看特定翻译阶段的条目（如只看"未翻译"）
- 需要按分类筛选（如只看 NPC_ 对话）
- 需要按标签筛选
- 需要搜索特定关键词

与 `manage_entry_labels` 的区别：set_filters 控制"表格里显示哪些条目"（视图层），manage_entry_labels 控制"条目有什么标签"（数据层）。

**参数:**
- `stages` (可选): 翻译阶段列表。合法值: 0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏。传空列表 `[]` 清除阶段筛选，不传保持当前值
- `categories` (可选): 分类名列表（如 `["NPC_", "INFO", "BOOK"]`）。`[]` 清除，不传保持
- `labels` (可选): 标签名列表。可用标签名通过 `list_labels` 获取。`[]` 清除，不传保持
- `search_query` (可选): 搜索关键词文本。`""` 清除搜索，不传保持
- `search_field` (可选): 在哪个字段搜索。可选: `"id"`, `"key"`, `"original"`, `"translation"`, `"context"`, `"all"`。不传则保持当前搜索字段不变
- `clear` (可选): 设为 `true` 先清除所有筛选，再应用本次参数。默认 `false`（叠加到现有筛选）

**副作用:**
- 后续 `get_visible_entries`、`get_statistics`、`manage_entry_labels(action=batch_assign)` 返回的条目范围会基于新筛选结果

**使用规则:**
- 仅传需要修改的维度即可，其他维度自动保持不变
- `clear: true` 单独使用 = 清除所有筛选条件
- `clear: true` + 其他参数 = 全新筛选（先清空再设置）
- 返回: 正常设置后返回 `{stage, category, label, search_query, search_field}`（当前筛选条件快照）；所有参数均为 None 且 clear=false 时返回 `{unchanged: true}`（筛选条件无变化）
- 修改前可通过 `get_current_filters` 查看当前筛选状态
- 常用组合：
  - `set_filters stages=[0]` → 只看未翻译 → `get_visible_entries` 获取列表
  - `set_filters search_query="龙裔" search_field="translation"` → 在译文中搜索
  - `set_filters clear=true stages=[1]` → 清除旧筛选，只看已翻译

---

## 2. get_visible_entries

**描述:**
获取当前筛选条件下表格中可见的条目列表（分页）。原文和译文均截断至 200 字符（固定限制，不可绕过）。统计数据（翻译率、分布等）用 `get_statistics`。

**参数:**
- `limit` (可选, int): 每页返回条数，默认 50，最大 200
- `offset` (可选, int): 分页偏移量，默认 0（第一页）

**返回:**
```json
{
  "entries": [{"key": "...", "id": "...", "original": "...", "translation": "...", "stage": 0}],
  "total_count": 150,
  "truncated": false
}
```
- `key` — 条目标识符（传给其他工具的 `entry_id` / `entry_ids` 参数时使用此值）
- `id` — 辅助标识（跨 ParaTranz 同步可能变化，不用于工具查找）
- `original` / `translation` — 原文/译文，均截断至 200 字
- `stage` — 翻译阶段: 0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏

**使用规则:**
- 调用前先用 `set_filters` 设置筛选条件，否则返回全部条目
- `truncated: true` 表示还有更多条目未显示，翻页用 `offset` 参数
- 不要循环遍历所有页——若需统计信息直接用 `get_statistics`；完整文本当前不可获取

---

## 3. select_entries

**描述:**
选中或取消选中条目。选择的条目存储在独立的临时选择集合中（与标签系统无关），可供后续批量操作（如 `set_stage` 批量标记）使用。

**参数:**
- `action` (可选): 操作类型。`"select"` 加入选择(默认), `"deselect"` 移除选择, `"clear"` 清空全部选择
- `entry_ids` (select/deselect 时必填): `get_visible_entries` 返回的 `key` 值列表。`action="clear"` 时无需传此参数

**副作用:**
- 选择状态保持到下次 `select_entries` 或 `clear` 操作之前

**使用规则:**
- 选择集合是临时的，不会持久化
- 选择结果不影响标签（标签用 `manage_entry_labels` 管理）
- 返回: `{selected_count, selected_ids}` — `selected_ids` 为当前已选条目 key 列表，`selected_count` 为已选条目总数
- 典型流程: `get_visible_entries` → 从返回结果取 `key` → `select_entries action=select entry_ids=["key1","key2"]` → `set_stage stage=3`。可通过返回的 `selected_ids` 确认当前已选条目

---

## 4. edit_translation

**描述:**
修改单条条目的翻译文本。可同时调整翻译阶段，也可保持原阶段不变。

与 `set_stage` 的区别：edit_translation 改的是翻译文本（可附带改 stage），一次只改一条；set_stage 只改阶段标记，可批量操作。

**参数:**
- `entry_id` (必填): `get_visible_entries` 返回的 `key` 值
- `new_translation` (必填): 新的翻译文本
- `new_stage` (可选): 新的翻译阶段。合法值: 0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏。注意值不连续（4/6/7/8 为 ParaTranz 平台预留，不可使用）。不传则保持原 stage 不变

**副作用:**
- 条目的翻译文本被永久修改

**使用规则:**
- 只操作单条条目，需要批量改文本请逐条调用
- 批量改阶段（不改文本）用 `set_stage`
- 返回: `{entry_id, old_translation, new_translation, stage, stage_changed}`。其中 `old_translation` 和 `new_translation` 均截断至 100 字
- 错误返回：
  - `entry_id` 不存在 → `ToolResult.fail("条目不存在: {entry_id}")`
  - `new_stage` 值非法 → `ToolResult.fail("无效的 stage 值: {new_stage}，合法值: {sorted(_VALID_STAGES)}")`（注意 4/6/7/8 为 ParaTranz 平台预留，不可使用）

---

## 5. set_stage

**描述:**
批量设置多条条目的翻译阶段标记。不修改翻译文本本身。

与 `edit_translation` 的区别：set_stage 只改阶段、可批量；edit_translation 改文本、单条。

**参数:**
- `entry_ids` (必填): `get_visible_entries` 返回的 `key` 值列表。传空列表 `[]` 时静默返回 `{updated_count: 0}`
- `stage` (必填): 目标阶段。合法值: 0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏。注意值不连续（4/6/7/8 为 ParaTranz 平台预留，不可使用）

**副作用:**
- 条目的阶段标记被永久修改

**使用规则:**
- 典型流程: `get_visible_entries` → 确认条目 `key` → `select_entries` 选择 → `set_stage stage=3` 批量标记为已检查
- stage 值不在合法范围内会被拒绝
- 返回: 全部成功返回 `{updated_count}`，部分失败额外含 `not_found`（未找到的条目 key 列表）
- 典型流转路径: 0→1(翻译) → 2→3(检查) → 5(审核)。各阶段含义：
  - 0(未翻译) → 1(已翻译) — AI 翻译完成后
  - 1(已翻译) → 2(有疑问) — 标记需人工审核
  - 2(有疑问) → 3(已检查) — 人工审核通过
  - 3(已检查) → 5(已审核) — 最终定稿
  - 9(已锁定)和-1(已隐藏)为特殊状态，不参与常规流转。

---

## 6. manage_entry_labels

**描述:**
管理条目标签——创建标签、为条目分配/移除标签、批量分配标签。通过 `action` 参数选择操作类型。

与 `set_filters` 的区别：manage_entry_labels 改变的是"条目身上有什么标签"（数据层），set_filters 改变的是"表格显示哪些条目"（视图层）。先用 manage_entry_labels 打好标签，再用 set_filters 按标签筛选。

**参数:**
- `action` (必填): 操作类型。可选:
  - `"create"` — 创建新标签（此时 `name` 必填、`color` 可选）
  - `"assign"` — 为指定条目分配标签（此时 `name` 必填、`entry_ids` 必填）
  - `"unassign"` — 从指定条目移除标签（此时 `name` 必填、`entry_ids` 必填）
  - `"batch_assign"` — 为当前筛选范围内所有条目批量分配标签（此时 `name` 必填，需用户确认）
- `name` (必填): 标签名。所有 action 均需要，先调用 `list_labels` 获取可用标签名列表
- `color` (可选): 颜色 Hex 值，如 `"#FF0000"`。仅 `create` 使用，默认 `"#409EFF"`
- `entry_ids` (assign/unassign 时必填): `get_visible_entries` 返回的 `key` 值列表。`create` / `batch_assign` 不需要

**副作用:**
- create: 新标签会出现在 `list_labels` 的返回结果中
- assign/unassign/batch_assign: 条目标签关系会持久化，下次 `list_labels` 可看到更新后的计数

**使用规则:**
- 操作前确保标签已存在（`assign` / `unassign` / `batch_assign` 操作不存在的标签会失败）
- `batch_assign` 操作当前 `set_filters` 筛选范围内的全部条目，调用时会弹出用户确认对话框，用户确认后执行；若用户取消，不分配任何标签，静默返回 `{assigned_count: 0}` 或类似结果。执行前应先通过 `set_filters` 确认范围，并通过 `get_current_filters` 验证筛选条件
- 返回: create→`{label_id, name, color}`（`label_id` 仅创建时返回，后续 assign/unassign/batch_assign 使用 `name` 而非 `label_id`） / assign→`{assigned_count}` / unassign→`{removed_count}` / batch_assign→`{assigned_count, filter_total}`（若用户取消确认，`assigned_count` 为 0）
- 典型流程:
  - 创建标签: `manage_entry_labels action=create name="重要" color="#FF0000"`
  - 打标签: `set_filters stages=[0]` → `manage_entry_labels action=batch_assign name="重要"`
  - 按标签筛选: `set_filters labels=["重要"]` → `get_visible_entries`

---

## 7. list_labels

**描述:**
列出所有已定义的标签，包括标签名、颜色、每个标签被多少条目使用。

**参数:** 无

**使用规则:**
- 只读操作，随时可调用
- 标签库为空或未初始化时返回空列表（不报错）
- 返回格式: `{"labels": [{id, name, color, count}]}`
- 需要创建或修改标签用 `manage_entry_labels`
- **重要**: 所有标签操作（assign/unassign/batch_assign）使用标签的 `name` 字段，而非 `id`。`id` 仅供内部追踪，不应传给其他工具。
