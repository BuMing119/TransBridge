# 002: AI助手翻译管线 Blocker 修复 — scope生效 + PT术语 + sync_terms删除

**日期**: 2026-05-18
**类型**: 改/删
**关联**: Epic: Agent 工具系统全面扩展 > QA修复

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容 (scope修复)**: 行67-90 `_tool_start_translation` 中条目范围解析逻辑重写。原逻辑：无 entry_ids 且无 scope 时设置默认 scope 但不使用。新逻辑：无 entry_ids 时从 `ctx.translation_scope` 解析 scope → `filter_entries()` → `[e.key for e in scoped]` → 传入 `AutoTranslator.translate(target_entry_ids=keys)`。若无 scope 则默认 scope(stage=0) 并同样解析为 entry_ids
- **原因**: `set_scope` 设置的翻译作用域被 `start_translation` 完全忽略，导致全部条目被翻译。修复后 scope 真正生效，LLM 两步完成 scope→翻译

- **修改内容 (PT术语修复)**: 行106-116 `AutoTranslator` 创建前补传 `paratranz_client` 和 `project_id`。从 `ctx.config` 创建 `ParatranzClient`，从 `ctx.paratranz_project_id` 或 `ctx.current_project.id` 获取项目 ID
- **原因**: 原代码 `AutoTranslator(cfg)` 不传 client/project_id，导致 `TermDatabaseManager._load_paratranz()` 收到两个 None 直接返回空列表，Paratranz 术语来源形同虚设。修复后与 GUI Worker 行为一致

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (删)
- **修改内容**: 删除 `_tool_sync_terms` 函数体（原调用不存在的 `client.get_terms()` API）、删除 `_PARAM_SCHEMAS["sync_terms"]`、删除注册列表中的 sync_terms 条目。paratranz namespace 从 10 工具减为 9 工具
- **原因**: 该工具调用的 API 方法不存在，且功能已被 `set_term_config term_sources=["paratranz"]` 覆盖——翻译器执行时自动通过 `TermDatabaseManager._load_paratranz()` 分页拉取全部术语

### `docs/requirements.md` (改)
- **修改内容**: 删除 FR9.5.5 sync_terms 条目，后续 FR9.5.6→FR9.5.5, FR9.5.7→FR9.5.6, FR9.5.8→FR9.5.7；新增 FR9.12 解析工具副作用补全需求条目
- **原因**: sync_terms 删除；Parser 工具副作用补全需求文档化

### `docs/test-reports/ai-assistant-translation-capability-gap.md` (改)
- **修改内容**: 审计报告新增"修复后"更新节，Blocker #1 scope→entry_ids 已修复、id→key 全量迁移完成、PT术语 client 补传。结论从"基本可用，2个缺口"更新为"全部修复，AI助手可实现与GUI完全一致的翻译流程"
- **原因**: 完整记录修复前后的能力变化

### `docs/test-reports/adr002-id-to-key-migration-audit.md` (增)
- **修改内容**: 新建全项目 id→key 迁移一致性审计报告。扫描 tools/ (4 P0 + ~8 P1)、ai_translator/ (3 P0 + ~18 P1/P2)、converter/parser/writer/ (0 需改，32 合法)。识别 3 条断裂链路并给出逐行修复建议
- **原因**: Story 23 将主索引切换为 key 后，系统性审计确保无遗漏

### `plans/agent-tool-expansion/stories/story-24-parser-side-effects.md` (增)
- **修改内容**: 新建 Story 24 详细方案文档：6 步实现（函数重构→副作用函数→HITL→schemas→plan范围→验证），验收标准、设计决策表、架构依赖
- **原因**: Parser 工具副作用补全的方案策划产出

### `plans/agent-tool-expansion/plan.md` (改)
- **修改内容**: 版本 v5→v6，Story 清单新增 Story 24，范围外声明更新（创建slot/追加条目移入范围），Story 11 验收标准删除 sync_terms
- **原因**: Story 24 追加 + sync_terms 删除的 plan 同步

### `plans/agent-tool-expansion/stories/story-11-p1-paratranz-tools.md` (改)
- **修改内容**: 删除 sync_terms 验收标准和实现说明，8→7 工具
- **原因**: sync_terms 删除的 Story 文档同步

### `plans/agent-tool-expansion/stories/story-13-agent-integration.md` (改)
- **修改内容**: paratranz Agent 工具列表删除 `"sync_terms"`
- **原因**: 同上

### `plans/agent-tool-expansion/stories/story-22-tool-description-rewrite.md` (改)
- **修改内容**: 工具描述列表删除 sync_terms 条目
- **原因**: 同上

### `docs/temp/batch4-proofreader-paratranz-tools.md` (改)
- **修改内容**: 删除 sync_terms 小节，后续章节重新编号 12-15
- **原因**: 临时文档同步删除

### `docs/temp/batch-audit-report-2026-05-18.md` (改)
- **修改内容**: 删除 sync_terms 相关表格行，更新计数 43→41
- **原因**: 同上

### `tests/test_agent_tool_integration.py` (改)
- **修改内容**: `test_parser_tools_have_read_permission` → `test_parser_tools_have_write_permission`，断言 read→write
- **原因**: Story 24 parser 工具 permission 从 read 升级为 write
