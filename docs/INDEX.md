# TransBridge 文档索引

## 项目简介

TransBridge 是一款 SSE (Skyrim Special Edition) Mod 本地化工具，支持 ESP/ESM 插件、EET/XT XML 与 ParaTranz 平台之间的翻译条目转换、上传和同步。内置 AI 自动翻译功能，支持多轮批量翻译与五阶段后处理。

- **技术栈**: Python 3.11+, PyQt6, openpyxl, pandas, openai>=1.0, anthropic>=0.20
- **仓库**: 本地 git 仓库，主分支 `main`
- **入口**: `src/transbridge/main.py` (CLI), `src/transbridge/ui/app.py` (GUI)

---

## 需求文档

| 文档 | 说明 | 状态 |
|------|------|------|
| [requirements.md](requirements.md) | 项目需求概述：功能需求、非功能需求、系统边界。FR7.13 Phase 1+2 已实现，FR9 Agent工具扩展已编码完成（14/14 Story, 60工具, 7 Agent） | ✅ 编码完成 |

---

## 架构决策记录 (ADR)

| 编号 | 标题 | 状态 |
|------|------|------|
| [ADR-001](adr/001-unified-translation-entry.md) | TranslationEntry 作为统一翻译数据模型 | ✅ 已接受 |
| [ADR-002](adr/002-collection-central-data-hub.md) | Collection 数据中枢与双索引设计 | ✅ 已接受 |
| [ADR-003](adr/003-three-round-translation-strategy.md) | 三轮 AI 翻译策略 | ✅ 已接受 |
| [ADR-004](adr/004-qthread-async-pattern.md) | QThread + 信号总线异步模式 | ✅ 已接受 |
| [ADR-005](adr/005-toml-prompt-no-langchain.md) | TOML Prompt 模板 + Skill 定义格式 | ✅ 已接受（更新: 2026-05-10） |
| [ADR-006](adr/006-project-persistence-variant-management.md) | 项目持久化与翻译版本管理 | ✅ 已接受 |
| [ADR-007](adr/007-mixed-translation-polish-mode.md) | AI翻译混合模式（三模式制+规则映射表+MixedWorker） | ✅ 已接受 |
| [ADR-008](adr/008-smart-assistant-code-layering.md) | SmartAssistant 代码分层（UI与业务逻辑分离 + Agent框架4子包） | ✅ 已接受（更新: 2026-05-10²） |
| [ADR-009](adr/009-agent-file-memory-reflexion.md) | Agent 文件解析、长期记忆与 Reflexion 自纠错（三模式降级） | ✅ 已接受（更新: 2026-05-10²） |
| [ADR-010](adr/010-infra-extraction.md) | 共享基础设施提取 — infra/ 包（Embedding三模式可选） | ✅ 已接受（更新: 2026-05-10） |
| [ADR-011](adr/011-graph-orchestration-engine.md) | 自研有状态图编排引擎（StatefulDAGExecutor，零新依赖） | ✅ 已接受 |
| [ADR-012](adr/012-safety-observability-mcp.md) | 安全护栏（中间件链）+ 可观测性（pyqtSignal遥测）+ MCP Server（stdio） | ✅ 已接受 |

> 详细架构文档见 [dev/ARCHITECTURE.md](dev/ARCHITECTURE.md)（模块依赖、数据流、全局状态管理、设计决策）。

---

## 方案目录

> 参见 [plans/INDEX.md](../plans/INDEX.md)

| Epic / Feature | 状态 | Story 数 |
|----------------|------|---------|
| [core-data-model](../plans/core-data-model/plan.md) | ✔️ 已实现 | 5 |
| [file-parsing](../plans/file-parsing/plan.md) | ✔️ 已实现 | 11 |
| [file-writing](../plans/file-writing/plan.md) | ✔️ 已实现 | 7 |
| [paratranz-integration](../plans/paratranz-integration/plan.md) | ✔️ 已实现 | 8 |
| [ai-translation](../plans/ai-translation/plan.md) | ✔️ 已实现 | 14 |
| [ai-post-process](../plans/ai-post-process/plan.md) | ✔️ 已实现 | 13 |
| [ui-workbench](../plans/ui-workbench/plan.md) | ✔️ 已实现 | 19 |
| [batch-operations](../plans/batch-operations/plan.md) | ✔️ 已实现 | 7 |
| [vector-term-retrieval](../plans/vector-term-retrieval/plan.md) | ✔️ 已实现 | — |
| [agent-tool-expansion](../plans/agent-tool-expansion/plan.md) | ✅ 编码完成 (60工具, 7 namespaces, 7 Agent) | 14 |
| [agent-upgrade](../plans/agent-upgrade/plan.md) | ✅ Phase 1 + Phase 2 全部完成 | 12 |
| [llm-chat](../plans/llm-chat/plan.md) | ✔️ 已实现 | 7 |

---

## 模块文档

详见 [dev/INDEX.md](dev/INDEX.md)，覆盖以下模块：

| 模块 | 文档 |
|------|------|
| converter | [dev/converter.md](dev/converter.md) |
| parser | [dev/parser.md](dev/parser.md) |
| writer | [dev/writer.md](dev/writer.md) |
| paratranz | [dev/paratranz.md](dev/paratranz.md) |
| ai_translator | [dev/ai_translator.md](dev/ai_translator.md) |
| post_processor | [dev/post_processor.md](dev/post_processor.md) |
| ui | [dev/ui.md](dev/ui.md) |
| 架构总览 | [dev/ARCHITECTURE.md](dev/ARCHITECTURE.md) |
| 数据结构 | [dev/DATA_STRUCTURES.md](dev/DATA_STRUCTURES.md) |
| DSD 格式参考 | [dev/dsd.md](dev/dsd.md) |
| 后处理报告 | [dev/post_process_report.md](dev/post_process_report.md) |

---

## 变更日志

> 参见 [changelogs/INDEX.md](changelogs/INDEX.md)

| Epic | 最新增量 | 日期 |
|------|---------|------|
| ai-post-process | [S09: 编码实现与Story文档](changelogs/ai-post-process/story-09-standalone-polish/2026-05-07-002-编码实现与Story文档.md) | 2026-05-07 |
| ui-workbench | [S15-S19 文件菜单重构系列](changelogs/ui-workbench/) | 2026-05-06 |
| ui-workbench | [S20: FR7.9 需求分析](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-001-需求分析FR79交互统一化.md) | 2026-05-07 |
| ui-workbench | [S20: FR7.9 方案策划](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-002-方案策划Story20-21.md) | 2026-05-07 |
| ui-workbench | [S20: Story-20 编码](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-003-Story20编码筛选系统统一化.md) | 2026-05-07 |
| ui-workbench | [S21: Story-21 编码](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-004-Story21编码表格交互升级.md) | 2026-05-07 |
| ui-workbench | [S22: FR7.10/5.10/8 需求分析+方案+Story展开](changelogs/ui-workbench/story-22-mark-and-visual/2026-05-07-001-需求分析和方案和Story展开.md) | 2026-05-07 |
| ui-workbench | [S16: 解析配置对话框提取](changelogs/ui-workbench/story-16-parse-config-dialog/2026-05-08-002-解析配置对话框独立提取.md) | 2026-05-08 |
| llm-chat | [S01-S03 面板+后端+循环控制](changelogs/llm-chat/) | 2026-05-06 |
| llm-chat | [S05: ContextBuilder与错误处理完善](changelogs/llm-chat/story-05-experience-optimization/2026-05-08-001-ContextBuilder与错误处理完善.md) | 2026-05-08 |
| llm-chat | [S06: 需求分析+架构+方案+Story展开](changelogs/llm-chat/story-06-layering-backend/2026-05-10-001-需求分析架构方案Story展开.md) | 2026-05-10 |
| llm-chat | [S06: 后端包创建与文件搬迁](changelogs/llm-chat/story-06-layering-backend/2026-05-10-002-后端包创建与文件搬迁.md) | 2026-05-10 |
| llm-chat | [S07: Story详细展开](changelogs/llm-chat/story-07-layering-ui/2026-05-10-001-Story07详细展开.md) | 2026-05-10 |
| llm-chat | [S07: UI层Import更新](changelogs/llm-chat/story-07-layering-ui/2026-05-10-002-UI层Import更新.md) | 2026-05-10 |
| stage-unification | [S01: FR2.5 需求+方案+3Story](changelogs/stage-unification/story-01-data-layer-stage/2026-05-07-001-需求分析和方案和3个Story展开.md) | 2026-05-07 |
| label-system | [S01: FR7.11 需求+方案+4Story](changelogs/label-system/story-01-label-model/2026-05-07-001-FR7.11需求分析+方案+4Story展开.md) | 2026-05-07 |
| stage-unification | [S01: Story-01 编码](changelogs/stage-unification/story-01-data-layer-stage/2026-05-07-002-Story01编码Stage常量定义.md) | 2026-05-07 |
| stage-unification | [S01: 常量统一与写回锁定隐藏](changelogs/stage-unification/story-01-data-layer-stage/2026-05-08-003-常量统一与写回锁定隐藏逻辑.md) | 2026-05-08 |
| ai-translation | [S09: 三维度作用域选择器重构](changelogs/ai-translation/story-09-scope-selector/2026-05-08-003-三维度作用域选择器重构.md) | 2026-05-08 |
| label-system | [S01: 标签库UI集成与右键菜单](changelogs/label-system/story-01-label-model/2026-05-08-005-标签库UI集成与右键菜单.md) | 2026-05-08 |
| project-init | [001-007 文档体系初始化](changelogs/project-init/docs-bootstrap/) | 2026-05-06 |
| project-init | [008-FR8 需求扩展](changelogs/project-init/docs-bootstrap/2026-05-08-001-FR8需求扩展翻译版本管理.md) | 2026-05-08 |
| project-init | [009-ADR006 项目持久化架构](changelogs/project-init/docs-bootstrap/2026-05-08-002-ADR006项目持久化与版本管理架构.md) | 2026-05-08 |
| project-persistence | [S01-S06 编码](changelogs/project-persistence/story-01-persistence-data-model/2026-05-08-001-S01至S06编码持久化基础设施与项目管理.md) | 2026-05-08 |
| project-persistence | [S07-S08 编码完成](changelogs/project-persistence/story-01-persistence-data-model/2026-05-08-002-S07S08编码完成transbridge归档与版本写回.md) | 2026-05-08 |
| project-init | [011-FR1.9 XT SST 解析需求](changelogs/project-init/docs-bootstrap/2026-05-08-004-FR19需求XT-SST二进制解析.md) | 2026-05-08 |
| agent-upgrade | [S01-S05: Phase 1 编码 + QA](changelogs/agent-upgrade/story-01-infra-extraction/) | 2026-05-10 |
| agent-upgrade | [S06-S12: Phase 2 编码实现 — Agent/护栏/Graph/可观测/MCP](changelogs/agent-upgrade/) | 2026-05-10 |
| agent-tool-expansion | [评审: FR9 方案分组评审](changelogs/agent-tool-expansion/council-review-fr9/2026-05-11-001-分组评审FR9方案.md) | 2026-05-11 |
| agent-tool-expansion | [确认: 修改确认书 38 项逐项确认](changelogs/agent-tool-expansion/council-review-fr9/2026-05-11-002-修改确认书逐项确认38项.md) | 2026-05-11 |
| agent-tool-expansion | [方案: plan.md v2 按确认书更新](changelogs/agent-tool-expansion/council-review-fr9/2026-05-11-003-plan-v2按确认书更新.md) | 2026-05-11 |
| agent-tool-expansion | [Story: 14个Story文档批量v2更新](changelogs/agent-tool-expansion/council-review-fr9/2026-05-11-004-Story文档批量v2更新.md) | 2026-05-11 |
| agent-tool-expansion | [S01: Story 01 编码实现 — 核心基础设施](changelogs/agent-tool-expansion/story-01-infra-tools-package/2026-05-11-001-Story01编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S02: Story 02 TaskManager 编码实现](changelogs/agent-tool-expansion/story-02-task-manager/2026-05-11-001-Story02编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S03: Story 03 AppContext ViewModel 编码实现](changelogs/agent-tool-expansion/story-03-appcontext-viewmodel/2026-05-11-001-Story03编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S04: Story 04 P0 筛选编辑工具编码实现](changelogs/agent-tool-expansion/story-04-p0-filter-search-tools/2026-05-11-001-Story04编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S06: Story 06 P0 翻译执行控制编码实现](changelogs/agent-tool-expansion/story-06-p0-translation-control/2026-05-11-001-Story06编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S07: Story 07 P0 状态查询编码实现](changelogs/agent-tool-expansion/story-07-p0-state-query-proofread/2026-05-11-001-Story07编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S08: Story 08 P1 标签工具编码实现](changelogs/agent-tool-expansion/story-08-p1-label-tools/2026-05-11-001-Story08编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S09: Story 09 P1 翻译配置编码实现](changelogs/agent-tool-expansion/story-09-p1-translation-config/2026-05-11-001-Story09编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S10: Story 10 P1 后处理工具编码实现](changelogs/agent-tool-expansion/story-10-p1-postprocess-tools/2026-05-11-001-Story10编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S11: Story 11 P1 ParaTranz 工具编码实现](changelogs/agent-tool-expansion/story-11-p1-paratranz-tools/2026-05-11-001-Story11编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S12: Story 12 P2 解析写回项目编码实现](changelogs/agent-tool-expansion/story-12-p2-parser-writer-project/2026-05-11-001-Story12编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S13: Story 13 Agent 集成编码实现](changelogs/agent-tool-expansion/story-13-agent-integration/2026-05-11-001-Story13编码实现.md) | 2026-05-11 |
| agent-tool-expansion | [S14: Story 14 集成测试编码实现](changelogs/agent-tool-expansion/story-14-integration-tests/2026-05-11-001-Story14集成测试编码.md) | 2026-05-11 |
| agent-tool-expansion | [QA修复: 28项安全/质量修复](changelogs/agent-tool-expansion/qa-fix/2026-05-11-001-QA审查28项修复.md) | 2026-05-11 |

历史发布记录见 [更新日志.md](更新日志.md)（2026-01 至 2026-03）。

---

## 测试报告

> 参见 [test-reports/](test-reports/)

| 时间 | 范围 | 结果 |
|------|------|------|
| 2026-05-09 | ai-post-process Story-10~13 (报告系统) | ✅ 通过 — 19项覆盖，0 Blocker |
| 2026-05-10 | agent-upgrade Phase 2 (Agent框架升级) | ✅ 通过 — 15项覆盖，0 Blocker，安全加固完成（AST沙箱+路径防御） |
| 2026-05-11 | agent-tool-expansion QA 审查 (Agent工具扩展) | ⚠ 需修复 — 89测试通过，发现28项问题(2B+6C+8M+12m)，已全部修复 |

---

## 修改记录

| 日期 | 修改内容 | 修改人 |
|------|---------|--------|
| 2026-05-06 | 文档体系完整初始化：顶层索引、需求文档、5 ADR、10 plan、70 Story、2 条 changelog | — |
| 2026-05-06 | 新增 FR7.7 文件菜单统一入口需求（/bm-analyze）→ changelog 003 | — |
| 2026-05-06 | 新增 FR6.9 独立润色入口需求（/bm-analyze）→ changelog 007 | — |
| 2026-05-07 | llm-chat Story-04 工具系统编码完成（ToolRegistry + 6 v1 工具 + API 修复）→ changelog s04-001 | — |
| 2026-05-07 | ui-workbench Story-18 集合工具栏回归工作台（widget.py 新增工具栏 + main_window.py 删除集合菜单）→ changelog s18-002 | — |
| 2026-05-07 | 新增 FR7.9 工作台交互统一化需求（/bm-analyze）→ changelog s20-001 | — |
| 2026-05-08 | 4 条增量记录：stage-unification 常量统一、label-system UI 集成、ai-translation 作用域选择器、ui-workbench 解析对话框提取 | — |
| 2026-05-08 | FR8 需求扩展为「项目持久化与翻译版本管理」：新增 FR8.9-FR8.12 翻译版本模型/创建复制/切换/写回，更新 FR8.1-FR8.8 为三层数据模型 | — |
| 2026-05-08 | project-persistence S01-S06 编码完成：4 新文件(persistence包) + 3 修改(context/widget/main_window)，实现持久化基础设施+项目管理+版本管理+自动保存+快照 | — |
| 2026-05-08 | project-persistence 方案策划 + 8 Story 展开：plans/project-persistence/plan.md + stories/story-01~08-*.md，共 95 Story | — |
| 2026-05-08 | file-parsing Story-09 SST SSU8 index 修正：field_a 低字节提取 per-EDID 子索引，新增 global_seq 字段 → changelog s09-003 | — |
| 2026-05-08 | file-parsing Story-09 SSU9 解析修复：str_idx 过滤移除、EDID 后缀扩展（18个）、unk12 per-EDID index 提取 + plan/story 文档同步 → changelog s09-004 | — |
| 2026-05-08 | file-parsing Story-09 XT 解析器结构调整：xt_parser/sst_parser 归入 parser/xt 子包，更新 7 个文件 import 路径 → changelog s09-005 | — |
| 2026-05-08 | file-parsing Story-10 方案策划：SST 迁移源集成（try_update_from_sst + apply_sst_entries + Step1 UI），追加到 plan.md | — |
| 2026-05-09 | file-parsing Story-09 SSU9 extra 子记录解析：新增 SST_Subrecord 数据结构 + _parse_ssu9_extra()，从 tail 中提取 101 条关联记录的双语文本 → changelog s09-006 | — |
| 2026-05-09 | FR1.9 需求扩展：SSU9 字段补充 + 新增 FR1.9.5 SST 序列化写回 → changelog project-init 012 | — |
| 2026-05-09 | file-parsing Story-11 方案策划：SST_Serializer 类（5 步实现、9 验收标准），追加到 plan.md → changelog s11-001 | — |
| 2026-05-09 | file-parsing Story-11 详细方案展开：5 步实现指南 + 数据流 + 关键接口 + 边界条件 + 风险，story 文档已确认 → changelog s11-002 | — |
| 2026-05-09 | file-parsing Story-10 编码完成：apply_sst_entries() 批量合并 + Step1 SST 加载 UI，3 文件修改 → changelog s10-004 | — |
| 2026-05-09 | file-parsing Story-11 编码完成：SST_Serializer 完整实现（from_parser/to_bytes/save/update），3 文件修改，往返测试通过 → changelog s11-003 | — |
| 2026-05-09 | file-parsing Story-09 QA 修复：group_index 字段 + header/chn_len 边界修复 + SSU9 格式注释 + SST 全模块测试报告 → changelog s09-007 | — |
| 2026-05-10 | 新增 FR7.13 Agent 框架全面升级需求（Phase1: Skill+文件+记忆+自纠错; Phase2: MCP+多Agent+Graph+护栏+可观测）→ changelog agent-upgrade-fr713-001 | — |
| 2026-05-10 | FR7.13 Phase 2 需求展开：5子需求→22详细条目，分三批实施（P0多Agent+护栏 / P1 Graph+可观测 / P2 MCP），Graph确定为自研方案 | /bm-orchestrator --auto |
| 2026-05-10 | FR7.13 Phase 2 架构决策：ADR-008更新(agents/子包) + ADR-011(自研Graph引擎) + ADR-012(护栏+可观测+MCP) | /bm-orchestrator --auto |
| 2026-05-10 | FR7.13 Phase 2 全部 7 Story 编码实现：新建 18 文件（agents/guardrails/graph/observability/mcp 5子包），修改 3 文件（tool_registry/execution_engine/__init__），零新依赖，全链路通过 | /bm-dev |
| 2026-05-11 | FR9 Agent 工具系统全面扩展分组评审完成：9 位评审员（3组×3人）+ 3轮讨论，产出 38 条建议（6阻塞+9高优+12增强+11优化），评审纪要写入 docs/council-review-fr9-tool-allocation.md | /bm-council |
| 2026-05-11 | FR9 修改确认书完成：38 项逐项确认（35 确认 / 3 跳过 / 4 用户修改方案），确认书写入 plans/agent-tool-expansion/modification-confirmation.md | /bm-orchestrator --auto |
| 2026-05-11 | FR9 plan.md v2 更新完成：按确认书重写全部 14 个 Story（05 废弃/14 新增），新增独立 PR + P2 迭代章节，方案状态 → 已确认 | /bm-plan |
| 2026-05-11 | FR9 14 个 Story 文档批量 v2 更新：Story 01-04/09 完整重写验收标准与实现步骤，Story 05 标记废弃，Story 14 新建，Story 06-08/10-13 头部标注 v2 状态 | /bm-story-batch |
| 2026-05-11 | FR9 Story 14 集成测试编码完成：tests/test_agent_tool_integration.py (~1060行, 89测试用例, 12测试类)，覆盖全链路/标签/安全/配置/ParaTranz/解析写回/Agent注册 | /bm-dev |
