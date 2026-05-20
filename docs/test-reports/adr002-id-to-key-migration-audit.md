# ADR-002 id→key 迁移遗漏：全量审计报告

**日期**: 2026-05-18
**审计范围**: 全项目 `translation_entry.id` → `translation_entry.key` 迁移一致性
**依据**: ADR-002 更新 2026-05-18 — 主索引从 `id` 切换为 `key`

---

## 总览

| 目录 | P0 | P1/P2 | 不需改 |
|------|-----|-------|--------|
| `tools/` | 4 | ~8 | — |
| `ai_translator/` | 3 | ~18 | — |
| `converter/parser/writer/` | 0 | 0 | 32 |
| **合计** | **7** | **~26** | **32** |

---

## P0：运行时阻断（7 处）

### 链路 1：LLM 提示 ↔ 翻译匹配断裂

```
prompt_builder.py:144  → LLM收到 {e.id: e.original}
         ↓                     LLM返回 {id_val: translation}
translator.py:983      → collection.get(id_val) ← get()期望key
translator.py:368      → e.id in id_set ← id_set存的是key值
translator.py:645      → 同上
```

| # | 文件 | 行 | 当前 | 应改为 |
|---|------|-----|------|--------|
| 1 | `prompt_builder.py` | 144 | `{e.id: e.original for e in entries}` | `{e.key: e.original}` |
| 2 | `translator.py` | 368 | `candidates = [e for e in all_entries if e.id in id_set]` | `e.key in id_set` |
| 3 | `translator.py` | 645 | `entries_to_check = [e for e in all_entries if e.id in target_set and e.translation]` | `e.key in target_set` |

### 链路 2：tools/ 层 scope→entry_ids

| # | 文件 | 行 | 当前 | 应改为 |
|---|------|-----|------|--------|
| 4 | `tool_translator.py` | 80 | `entry_ids = [e.id for e in scoped]` | `[e.key for e in scoped]` |
| 5 | `tool_translator.py` | 89 | `entry_ids = [e.id for e in scoped]`（默认scope路径） | `[e.key for e in scoped]` |

### 链路 3：entry_labels 键不一致

| # | 文件 | 行 | 当前 | 应改为 |
|---|------|-----|------|--------|
| 6 | `tool_editor.py` | 421 | `_filtered_ids = [e.id for e in entries]` → entry_labels 键 | `[e.key for e in entries]` |
| 7 | `base.py` | 478 | `entry_labels.get(e.id, set())` | `entry_labels.get(e.key, set())` |

---

## P1：后处理器 + UI Worker + 内部映射（~26 处）

**共性模式**：`entry_map = {e.id: e for e in entries}` 然后用 `issue.entry_id` 查找。`issue.entry_id` 在 `consistency_checker.py:202` 被设为 `entry.id`。

| 文件 | 行 | 问题 | 修复 |
|------|-----|------|------|
| `consistency_checker.py` | 202 | `entry_id=entry.id` | `entry.key` |
| `post_processor.py` | 390, 644, 650 | `e.id in issues_by_entry` | `e.key in issues_by_entry` |
| `post_processor.py` | 812 | `entry_map = {e.id: e}` | `{e.key: e}` |
| `post_processor.py` | 946 | `collection.get(entry_id)` | `collection.get_by_id(entry_id)` |
| `translator.py` | 745, 748, 755 | `id_map_all = {e.id: e}` | `{e.key: e}` |
| `translator.py` | 813, 814, 825, 871, 895, 924 | `expected_ids = {e.id}` 等 | `{e.key}` |
| `translator.py` | 444, 561 | 批次指纹 `frozenset(e.id for ...)` | `e.key` |
| `llm_arbiter.py` | 650, 312, 326, 344, 355, 363, 378, 389, 397, 408, 434 | `entry_map = {ctx.entry.id: ...}` + `entry_id=entry.id` | `e.key` |
| `polisher.py` | 413 | `entry_map = {e.id: e}` | `{e.key: e}` |
| `llm_refiner.py` | 432 | `entry_map = {e.id: e}` | `{e.key: e}` |
| `quality_gate.py` | 343 | `entry_map = {e.id: e}` | `{e.key: e}` |
| `_mixed_worker.py` | 152 | `[e.id for e in self._translate_entries]` | `e.key` |
| `_batch_translation_worker.py` | 218 | `e.id for e in slot.collection if ...` | `e.key` |
| `batch_planner.py` | 129, 155 | `len(e.id or "")` 字符估算 | `e.key` |

**其他**:
- `tool_editor.py:185` — `get_visible_entries` 返回 `"id": e.id` 可能混淆 LLM
- `base.py:457` — docstring 仍写 `entry_id → set[label_name]`
- `base.py:485-486` — `search_field == "id"` 分支保留
- 多处 schema description 仍写 "条目ID" 应为 "条目Key"

---

## 不需修改（32 处）

converter/parser/writer 中所有 `entry.id` 均为合法的内部用途：
- `_id_index` 辅助索引维护
- 复合 id 字符串解析提取 edid/form_id/index
- EET/XT/SST apply 中按 id 精确匹配
- 构建新 TranslationEntry 时透传 id 字段
- `EET_Entry.id` 是 EET 原生字段，非 TranslationEntry

---

## 根因分析

核心断裂在 **LLM 提示 ↔ 翻译结果 的键不匹配**：

```
prompt_builder 发送:  {e.id: e.original}    ← LLM 收到 id 值
LLM 返回:             {id_val: translation}  ← 返回 id 值
translator._update_collection:
  collection.get(id_val)                     ← get() 期望 key 值
```

当前 `id==key`（构造时设相同值），暂不报错。一旦 ParaTranz 下载改写 `id` 而 `key` 不变，整个翻译管线静默失败——这正是 Story 23 要防止的问题。

---

## 关联

- **ADR-002 更新 2026-05-18**: 主索引从 `id` 切换为 `key`
- **Story 23**: `key` 升主索引
