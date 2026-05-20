# 004: id→key 迁移遗漏全量审计

**日期**: 2026-05-18
**类型**: 增
**关联**: Epic: Agent 工具系统全面扩展 > Story 23: TranslationEntry.key 升主索引

## 修改文件

### `docs/test-reports/adr002-id-to-key-migration-audit.md` (增)
- **修改内容**: 全项目 ADR-002 id→key 迁移一致性审计报告。分三层扫描 tools/（4 P0 + ~8 P1）、ai_translator/（3 P0 + ~18 P1/P2）、converter/parser/writer/（0 需改，32 合法内部用途）。识别 3 条断裂链路：LLM提示↔翻译匹配（prompt_builder→translator→collection.get）、scope→entry_ids（tool_translator）、entry_labels键不一致（tool_editor+base.filter_entries）。根因定位：prompt_builder 发送 {e.id: e.original} 给 LLM，LLM 返回 id_val，但 collection.get() 期望 key，ParaTranz 下载改写 id 后将静默失败。
- **原因**: Story 23 将主索引从 id 切换为 key，但 tools/ 和 ai_translator/ 层多处仍使用 entry.id 进行条目匹配、集合查找和标签操作。当前 id==key 暂时遮蔽问题，一旦 ParaTranz 下载改写 id 将触发批量故障。
