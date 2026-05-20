# 005: id→key 迁移遗漏代码修复 — P0+P1+P2 全量

**日期**: 2026-05-18
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > Story 23: TranslationEntry.key 升主索引

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容**: 行80/89 `[e.id for e in scoped]` → `[e.key for e in scoped]`，scope→entry_ids 解析使用 key 值
- **原因**: AutoTranslator.translate() 中 `e.id in id_set` 已改为 `e.key`，scope 解析需对齐

### `src/transbridge/smart_assistant/tools/tool_editor.py` (改)
- **修改内容**: 行421 batch_assign 中 `[e.id for e in entries]` → `[e.key for e in entries]`
- **原因**: entry_labels 字典键应与 LLM 传入的 key 值一致，避免标签查找失败

### `src/transbridge/smart_assistant/tools/base.py` (改)
- **修改内容**: 行478 filter_entries 中 `entry_labels.get(e.id, set())` → `entry_labels.get(e.key, set())`
- **原因**: entry_labels 键已切换为 key，标签筛选需对齐

### `src/transbridge/ai_translator/prompt_builder.py` (改)
- **修改内容**: 行144 `{e.id: e.original}` → `{e.key: e.original}`，LLM 提示中的条目键切换为 key
- **原因**: LLM 返回的翻译字典键将变为 key 值，下游 translator 和 collection.get() 均使用 key

### `src/transbridge/ai_translator/translator.py` (改)
- **修改内容**: ~22处 `e.id` → `e.key`（候选过滤、批次指纹、id_map_all→key_map_all、expected_ids→expected_keys、id_to_entry→key_to_entry、流式翻译匹配、_post_process 过滤），行983 `collection.get(entry_id)` → `collection.get_by_id(entry_id)`
- **原因**: prompt_builder 切换为 key 后，translator 内所有条目匹配/查找/指纹均需对齐，否则流式翻译会丢结果

### `src/transbridge/ai_translator/post_processor/consistency_checker.py` (改)
- **修改内容**: 行202 `entry_id=entry.id` → `entry_id=entry.key`
- **原因**: PostProcessIssue.entry_id 是后处理链的根标识，切换为 key 后所有下游自动对齐

### `src/transbridge/ai_translator/post_processor/post_processor.py` (改)
- **修改内容**: 行390/644/650 `e.id in issues_by_entry` → `e.key`，行812 `entry_map = {e.id: e}` → `{e.key: e}`
- **原因**: issues_by_entry 键来自 PostProcessIssue.entry_id (已改 key)，entry_map 查找需对齐

### `src/transbridge/ai_translator/post_processor/polisher.py` (改)
- **修改内容**: 行413 `entry_map = {e.id: e}` → `{e.key: e}`
- **原因**: 同上

### `src/transbridge/ai_translator/post_processor/llm_refiner.py` (改)
- **修改内容**: 行432 `entry_map = {e.id: e}` → `{e.key: e}`
- **原因**: 同上

### `src/transbridge/ai_translator/post_processor/quality_gate.py` (改)
- **修改内容**: 行343 `entry_map = {e.id: e}` → `{e.key: e}`
- **原因**: 同上

### `src/transbridge/ai_translator/post_processor/llm_arbiter.py` (改)
- **修改内容**: 行312/326 `decisions[ctx.entry.id]` → `ctx.entry.key`，行344-434 多处 `entry_id=entry.id` → `entry.key`，行650 `entry_map = {ctx.entry.id: ...}` → `{ctx.entry.key: ...}`
- **原因**: 裁决结果存储和后处理链路统一使用 key

### `src/transbridge/ui/tools/ai_translator/_mixed_worker.py` (改)
- **修改内容**: 行152 `[e.id for e in self._translate_entries]` → `[e.key]`
- **原因**: target_entry_ids 现在期望 key 值

### `src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py` (改)
- **修改内容**: 行218 `e.id for e in slot.collection` → `e.key`
- **原因**: 同上

### `src/transbridge/ai_translator/batch_planner.py` (改)
- **修改内容**: 行129/155 `len(e.id or "")` → `len(e.key or "")`
- **原因**: 批次字符数估算使用 key 长度更准确
