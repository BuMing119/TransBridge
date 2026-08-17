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
| [requirements.md](requirements.md) | 项目需求概述：功能需求、非功能需求、系统边界。FR7.13 Phase 1+2 已实现，FR9 Agent工具扩展已编码完成（26/26 Story, 60工具, 7 Agent），FR7.17 已实现，FR10 已实现（4/4 Story，330测试通过），FR16 通用文件与词条工具已实现（fileops/migrator + 7 Agent工具），FR15 FOMOD 翻译流水线已实现（翻译记忆 + fomod_xml/builder/pipeline/GUI） | ✅ 已实现 |

---

## 架构决策记录 (ADR)

| 编号 | 标题 | 状态 |
|------|------|------|
| [ADR-001](adr/001-unified-translation-entry.md) | TranslationEntry 作为统一翻译数据模型 | ✅ 已接受 |
| [ADR-002](adr/002-collection-central-data-hub.md) | Collection 数据中枢与双索引设计 | ✅ 已接受（更新: 2026-05-18） |
| [ADR-003](adr/003-three-round-translation-strategy.md) | 三轮 AI 翻译策略 | ✅ 已接受 |
| [ADR-004](adr/004-qthread-async-pattern.md) | QThread + 信号总线异步模式 | ✅ 已接受 |
| [ADR-005](adr/005-toml-prompt-no-langchain.md) | TOML Prompt 模板 + Skill 定义格式 | ✅ 已接受（更新: 2026-05-10） |
| [ADR-006](adr/006-project-persistence-variant-management.md) | 项目持久化与翻译版本管理 | ✅ 已接受 |
| [ADR-007](adr/007-mixed-translation-polish-mode.md) | AI翻译混合模式（三模式制+规则映射表+MixedWorker） | ✅ 已接受 |
| [ADR-008](adr/008-smart-assistant-code-layering.md) | SmartAssistant 代码分层（UI与业务逻辑分离 + Agent框架4子包） | ✅ 已接受（更新: 2026-05-10³, 2026-05-22, 2026-08-05²） |
| [ADR-009](adr/009-agent-file-memory-reflexion.md) | Agent 文件解析、长期记忆与 Reflexion 自纠错（三模式降级） | ✅ 已接受（更新: 2026-05-10²） |
| [ADR-010](adr/010-infra-extraction.md) | 共享基础设施提取 — infra/ 包（Embedding三模式可选） | ✅ 已接受（更新: 2026-05-10） |
| [ADR-011](adr/011-graph-orchestration-engine.md) | 自研有状态图编排引擎（StatefulDAGExecutor，零新依赖） | ✅ 已接受 |
| [ADR-012](adr/012-safety-observability-mcp.md) | 安全护栏（中间件链）+ 可观测性（pyqtSignal遥测）+ MCP Server（stdio） | ✅ 已接受（更新: 2026-05-14） |
| [ADR-013](adr/013-vector-retrieval-enhancement.md) | 向量语义检索增强（BM25 混合检索 + 增量索引 + 编码缓存） | ✅ 已接受 |
| [ADR-014](adr/014-fomod-translation-memory.md) | FOMOD 翻译流水线 + 通用翻译记忆（键+文本分层匹配 / 独立双包 / py7zr+rarfile 自包含 / 精确匹配） | ✅ 已接受（更新: 2026-08-14） |
| [ADR-015](adr/015-generic-file-entry-tools.md) | 通用文件与词条工具（fileops/migrator 独立包 / archive·editor·translator namespace / 键对齐与词典套用严格分离） | ✅ 已接受 |

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
| [ui-workbench](../plans/ui-workbench/plan.md) | ✔️ 已实现 | 22 |
| [batch-operations](../plans/batch-operations/plan.md) | ✔️ 已实现 | 7 |
| [vector-term-retrieval](../plans/vector-term-retrieval/plan.md) | ✔️ 已实现 | — |
| [agent-tool-expansion](../plans/agent-tool-expansion/plan.md) | ✔️ S01-21+S23-26已实现 + ✔️ S22已实现 (描述+代码修复) | 26 |
| [agent-upgrade](../plans/agent-upgrade/plan.md) | ✅ Phase 1 + Phase 2 全部完成 | 12 |
| [llm-chat](../plans/llm-chat/plan.md) | ✅ 全部完成 (S01-10，含 ChatWidget拆分) | 10 |
| [smart-assistant-qa-fix](../plans/smart-assistant-qa-fix/plan.md) | ✅ 第五轮全量修复完成 (166/166) | 7 |
| [smart-assistant-refactor](../plans/smart-assistant-refactor/plan.md) | ✔️ 已实现 | 4 |
| [tool-prompt-layering](../plans/tool-prompt-layering/plan.md) | ✅ 全部完成 (S01-S05) | 5 |
| [session-controller](../plans/session-controller/plan.md) | ✅ 全部完成（2/2） | 2 |
| [session-manager](../plans/session-manager/plan.md) | ✅ 全部完成（3/3） | 3 |
| [task-monitor](../plans/task-monitor/plan.md) | ✔️ 已实现 (2/2 Story + QA 通过) | 2 |
| [translation-memory](../plans/translation-memory/plan.md) | ✅ 全部完成 (S01-10，含词典粒度重构) | 10 |
| [stage-unification](../plans/stage-unification/plan.md) | ✔️ 已实现 | 3 |
| [label-system](../plans/label-system/plan.md) | ✔️ 已实现 | 4 |
| [project-persistence](../plans/project-persistence/plan.md) | ✔️ 已实现 | 8 |
| [fr5.12-embedding-optimization](../plans/fr5.12-embedding-optimization/plan.md) | ✔️ 已实现 | 3 |
| [agent-infra-tools](../plans/agent-infra-tools/plan.md) | ✔️ 已实现 | 5 |
| [fomod-translation](../plans/fomod-translation/plan.md) | ✔️ 已实现 | 4 |

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
| ai-post-process | [修复: _replace 崩溃](changelogs/ai-post-process/fix/2026-05-11-001-修复check_quality的_replace调用错误.md) | 2026-05-11 |
| ui-workbench | [S15-S19 文件菜单重构系列](changelogs/ui-workbench/) | 2026-05-06 |
| ui-workbench | [S20: FR7.9 需求分析](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-001-需求分析FR79交互统一化.md) | 2026-05-07 |
| ui-workbench | [S20: FR7.9 方案策划](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-002-方案策划Story20-21.md) | 2026-05-07 |
| ui-workbench | [S20: Story-20 编码](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-003-Story20编码筛选系统统一化.md) | 2026-05-07 |
| ui-workbench | [S21: Story-21 编码](changelogs/ui-workbench/story-20-ux-unification/2026-05-07-004-Story21编码表格交互升级.md) | 2026-05-07 |
| ui-workbench | [S22: FR7.10/5.10/8 需求分析+方案+Story展开](changelogs/ui-workbench/story-22-mark-and-visual/2026-05-07-001-需求分析和方案和Story展开.md) | 2026-05-07 |
| ui-workbench | [S16: 解析配置对话框提取](changelogs/ui-workbench/story-16-parse-config-dialog/2026-05-08-002-解析配置对话框独立提取.md) | 2026-05-08 |
| llm-chat | [S01-S03 面板+后端+循环控制](changelogs/llm-chat/) | 2026-05-06 |
| llm-chat | [S05: ContextBuilder与错误处理完善](changelogs/llm-chat/story-05-experience-optimization/2026-05-08-001-ContextBuilder与错误处理完善.md) | 2026-05-08 |
| llm-chat | [S05: 系统提示词更新自我认知与能力描述](changelogs/llm-chat/story-05-experience-optimization/2026-05-11-002-系统提示词更新自我认知与能力描述.md) | 2026-05-11 |
| llm-chat | [S06: 需求分析+架构+方案+Story展开](changelogs/llm-chat/story-06-layering-backend/2026-05-10-001-需求分析架构方案Story展开.md) | 2026-05-10 |
| llm-chat | [S06: 后端包创建与文件搬迁](changelogs/llm-chat/story-06-layering-backend/2026-05-10-002-后端包创建与文件搬迁.md) | 2026-05-10 |
| llm-chat | [S07: Story详细展开](changelogs/llm-chat/story-07-layering-ui/2026-05-10-001-Story07详细展开.md) | 2026-05-10 |
| llm-chat | [S07: UI层Import更新](changelogs/llm-chat/story-07-layering-ui/2026-05-10-002-UI层Import更新.md) | 2026-05-10 |
| stage-unification | [S01: FR2.5 需求+方案+3Story](changelogs/stage-unification/story-01-data-layer-stage/2026-05-07-001-需求分析和方案和3个Story展开.md) | 2026-05-07 |
| label-system | [S01: FR7.11 需求+方案+4Story](changelogs/label-system/story-01-label-model/2026-05-07-001-FR7.11需求分析+方案+4Story展开.md) | 2026-05-07 |
| stage-unification | [S01: Story-01 编码](changelogs/stage-unification/story-01-data-layer-stage/2026-05-07-002-Story01编码Stage常量定义.md) | 2026-05-07 |
| stage-unification | [S01: 常量统一与写回锁定隐藏](changelogs/stage-unification/story-01-data-layer-stage/2026-05-08-003-常量统一与写回锁定隐藏逻辑.md) | 2026-05-08 |
| ai-translation | [S09: 三维度作用域选择器重构](changelogs/ai-translation/story-09-scope-selector/2026-05-08-003-三维度作用域选择器重构.md) | 2026-05-08 |
| ai-translation | [修复: EmbeddingConfig属性访问与默认模型名](changelogs/ai-translation/fix/2026-05-11-001-修复EmbeddingConfig属性访问与默认模型名.md) | 2026-05-11 |
| ai-translation | [修复: 配置窗口滚动区域与宽度调整](changelogs/ai-translation/fix/2026-05-11-002-配置窗口滚动区域与宽度调整.md) | 2026-05-11 |
| ai-translation | [修复: embedding语义检索断连](changelogs/ai-translation/fix/2026-08-13-003-修复embedding语义检索断连.md) | 2026-08-13 |
| ai-translation | [FR5.12: 需求分析语义检索优化](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-001-需求分析FR5.12语义检索优化.md) | 2026-08-13 |
| ai-translation | [FR5.12: 架构决策 ADR-013](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-002-架构决策ADR013.md) | 2026-08-13 |
| ai-translation | [FR5.12: 方案策划 3 Story](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-003-方案策划3Story.md) | 2026-08-13 |
| ai-translation | [FR5.12: Story 展开 3 个实现指南](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-004-Story展开3个Story.md) | 2026-08-13 |
| ai-translation | [FR5.12: 编码实现 3 Story](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-005-编码实现3Story.md) | 2026-08-13 |
| ai-translation | [FR5.12: QA 审查与 Critical 修复](changelogs/ai-translation/fr5.12-embedding-optimization/2026-08-13-006-QA审查与Critical修复.md) | 2026-08-13 |
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
| agent-tool-expansion | [S15: 工具补完](changelogs/agent-tool-expansion/story-15-tool-completion/2026-05-15-001-Story15需求分析方案编码全流程.md) | 2026-05-15 |
| agent-tool-expansion | [S16: 死代码清理与注册样板消除](changelogs/agent-tool-expansion/story-16-dead-code-registration/2026-05-18-001-死代码清理与注册样板消除.md) | 2026-05-18 |
| agent-tool-expansion | [S17: set_filters 合并 5→1](changelogs/agent-tool-expansion/story-17-set-filters-merge/2026-05-18-001-set-filters合并5to1.md) | 2026-05-18 |
| smart-assistant-qa-fix | [S05-④: checkpoint数据目录路径修复](changelogs/smart-assistant-qa-fix/story-05-thread-resource/2026-05-14-004-checkpoint数据目录路径修复.md) | 2026-05-14 |
| smart-assistant-qa-fix | [QA5修复: 第五轮全量修复155项](changelogs/smart-assistant-qa-fix/qa-round5-fix/2026-05-15-001-第五轮全量QA修复编码155项.md) | 2026-05-15 |
| tool-prompt-layering | [S02: Phase 1 核心基础设施](changelogs/tool-prompt-layering/story-02-summary-and-builders/2026-05-25-001-Phase1核心基础设施实现.md) | 2026-05-25 |
| tool-prompt-layering | [S03: Phase 2 元工具注册与Prompt重构](changelogs/tool-prompt-layering/story-03-get-tool-help-and-prompt/2026-05-25-001-Phase2元工具注册与Prompt重构.md) | 2026-05-25 |
| tool-prompt-layering | [S03: QA修复 output验证+orchestrator+自动保存+测试mock](changelogs/tool-prompt-layering/story-03-get-tool-help-and-prompt/2026-05-25-002-QA修复-output验证与orchestrator防御.md) | 2026-05-25 |

历史发布记录见 [更新日志.md](更新日志.md)（2026-01 至 2026-03）。

---

## 测试报告

> 参见 [test-reports/](test-reports/)

| 时间 | 范围 | 结果 |
|------|------|------|
| 2026-05-09 | ai-post-process Story-10~13 (报告系统) | ✅ 通过 — 19项覆盖，0 Blocker |
| 2026-05-10 | agent-upgrade Phase 2 (Agent框架升级) | ✅ 通过 — 15项覆盖，0 Blocker，安全加固完成（AST沙箱+路径防御） |
| 2026-05-11 | agent-tool-expansion QA 审查 (Agent工具扩展) | ⚠ 需修复 — 89测试通过，发现28项问题(2B+6C+8M+12m)，已全部修复 |
| 2026-05-11 | smart-assistant 全面审查 (Smart Assistant AI助手) | ⚠ 需修复 — 4维度并行审查，发现3B+10C+16M+21m，综合评分32/60 |
| 2026-05-12 | smart-assistant-qa-fix 修复验证 (FR7.15 QA修复) | ⚠ 部分回退 — 46/50 修复，综合评分 32→51/60，测试覆盖 ~165 用例，仅余 4 Minor 已知限制 |
| 2026-05-13 | smart-assistant 第三轮全面审查 (4维度并行) | ⚠ 需修复 — 发现 3B+6C+11M+12m (32项)，综合评分 36/60，5项原修复存在缺陷 |
| 2026-05-13 | smart-assistant 第四轮全面审查 (4维度并行) | ⚠ 需修复 — 发现 4B+15C+28M+40m (87项)，综合评分 36/60，全新独立审查 |
| 2026-05-13 | smart-assistant Stack Overflow 评审委员会 (4角色: 架构师/开发者/QA/安全) | ⚠ 需修复 — 发现后端 6 类 QObject 耦合为 C 栈溢出根因，输出 7 条共识建议 + Phase 1-3 分阶段解耦计划，[纪要](council-review-stack-overflow-decoupling.md) |
| 2026-05-14 | smart-assistant 第五轮审查 — Story-08 前端 QA | ✅ 复验通过 — 修复 5B+2C，综合评分 38→51/60，[报告](test-reports/llm-chat-story-08-frontend-qa.md) |
| 2026-05-14 | smart-assistant 第五轮全量审查 (8维度并行) | ⚠ 需修复 → 2026-05-15 已修复 111/111 项 (9B+27C+68M+~48m)，[报告](test-reports/smart-assistant-full-qa-2026-05-14.md) |
| 2026-05-13 | smart-assistant Phase 1 QObject 解耦 QA 审查 (4维度并行) | ✅ 条件通过 — 15/15 功能测试通过，2 Blocker 已修复 (跨线程回调投递)，4 Major + 8 Minor 已知限制，[报告](test-reports/smart-assistant-phase1-decoupling.md) |
| 2026-05-14 | llm-chat Story-10 ToolResult 观察消息序列化增强 QA 审查 | ✅ 通过 — 25/25 功能测试通过，0 Blocker/Critical/Major/Minor，性能/安全/代码质量全绿，[报告](test-reports/llm-chat-story-10-toolresult-observation.md) |
| 2026-05-15 | smart-assistant 第五轮全量修复编码 (30+ Agent 并行) | ✅ 全部完成 — 修复 166/166 项 + 4 运行时错误，60文件 +2826/-1606行，[增量①](changelogs/smart-assistant-qa-fix/qa-round5-fix/2026-05-15-001-第五轮全量QA修复编码155项.md) · [②](changelogs/smart-assistant-qa-fix/qa-round5-fix/2026-05-15-002-第五轮收尾C18C19M4与Minor修复.md) · [③](changelogs/smart-assistant-qa-fix/qa-round5-fix/2026-05-15-003-第五轮Minor收尾类型注册竞态修复.md) · [④](changelogs/smart-assistant-qa-fix/qa-round5-fix/2026-05-15-004-运行时错误修复循环导入sip与setMaxLength.md) |
| 2026-05-15 | agent-tool-expansion Story-15 工具补完 (search_entries 6字段 + PT项目切换) | ✅ 通过 — 12/12 验收标准通过，0 Blocker/Critical/Major/Minor，[报告](test-reports/agent-tool-expansion-story-15-tool-completion.md) |
| 2026-05-15 | 工具架构路线 A 实施方法设计 — 评审委员会第二轮 (4角色: 架构师/开发者/QA/产品) | ✅ 完成 — 7项共识、2项分歧待用户裁决，[纪要](council-review-tool-architecture-methodology.md) |
| 2026-05-20 | agent-tool-expansion Story-25 后处理工具统一 QA 审查 | 🔴 不通过 — 5 Blocker(运行时崩溃) + 5 Critical(功能缺失)，[报告](test-reports/story-25-postprocess-unification-qa.md) |
| 2026-05-20 | AI 助手工具 vs 原后处理工作流等价性复验 | ✅ 基本通过 — 上轮11项全修复，核心管线等价，4体验缺口(G1-G4)，87/89测试通过，[报告](test-reports/ai-tool-postprocess-parity-2026-05-20.md) |
| 2026-05-20 | Story 25 G1-G4 体验缺口修补 QA 复验 | ✅ 通过 — 4项修补全部验证，120/123测试通过，[报告](test-reports/story-25-g1g4-fix-verification.md) |
| 2026-05-20 | AI 助手工具 vs 原后处理工作流 — 完整等价性评估（独立全新审查） | ✅ 基本通过 — 核心管线等价，发现 3B+6C+12M+13M(34项)，二次复核补全 8项遗漏，P0+P1共14项已修复，135/137测试通过，[报告](test-reports/ai-tool-postprocess-parity-2026-05-20-final.md) |
| 2026-05-21 | AI 助手工具 vs 原后处理工作流 — 等价性复验（修复后验证） | ✅ 通过 — 3B+6C全部修复，48/48 parity pass + 87/89 integration pass，核心管线等价，start_polish完整可用，[报告](test-reports/ai-tool-postprocess-parity-2026-05-21.md) |
| 2026-05-21 | Story 26: 后处理断点续传与暂停/恢复 QA 审查 | ✅ 通过 — 6/6验收标准实现，18/18新测试通过，153/155全量通过，0 Blocker/Critical/Major，[报告](test-reports/story-26-checkpoint-pause.md) |
| 2026-05-21 | agent-tool-expansion Story 22 QA 复验 — 工具描述修复与代码缺陷修复 | ✅ 通过 — 40/40修复验证，120/123测试通过，0新引入失败 |
| 2026-05-21 | agent-tool-expansion 全面 QA 审查 (4维度并行) — 工具系统 | ⚠ 需修复 — 26项问题(1B+4C+10M+11m)，综合评分45/60，[报告](test-reports/agent-tool-expansion-qa-full-2026-05-21.md) |
| 2026-05-21 | agent-tool-expansion QA 复验 — 5项Blocker+Critical修复验证 | ✅ 通过 — 34新测试通过，221/223全量通过，0新问题，[报告](test-reports/agent-tool-expansion-qa-full-2026-05-21.md) · [复验](test-reports/agent-tool-expansion-qa-round2-verify-2026-05-21.md) |
| 2026-05-25 | tool-prompt-layering QA 审查 — 工具提示词分层加载机制 | ✅ 通过 — 356/356 零回归，28 新测试，综合评分 55/60，0 Blocker/Critical/Major，[报告](test-reports/tool-prompt-layering-qa-2026-05-25.md) |
| 2026-08-05 | tool-prompt-layering Phase 4 调优 QA | ✅ 通过 — 354/354 零新回归（4预存失败），5/5验收达标，零安全问题，[报告](test-reports/tool-prompt-layering-qa-2026-08-05.md) |
| 2026-08-05 | session-manager QA | ✅ 通过 — 426/428 通过（2预存），3/3 Story验收，零新问题，[报告](test-reports/session-manager-qa-2026-08-05.md) |
| 2026-08-05 | task-monitor QA | ✅ 通过 — 449/451 通过（2预存），2/2 Story验收，23新测试，零 Blocker/Critical/Major，[报告](test-reports/task-monitor-qa-2026-08-05.md) |
| 2026-08-13 | unit-test-staleness QA — 预存测试失败根因定位 | ⚠ 需修复 — 19 失败（非 2 预存）：17 数据模型漂移 + 2 护栏默认拒绝，全部测试侧，零真实代码缺陷，[报告](test-reports/unit-test-staleness-qa-2026-08-13.md) |
| 2026-08-13 | embedding 语义检索断连修复 QA | ✅ 通过 — 5 新测试 + 540/540 全绿，无 Blocker/Critical/Major，[报告](test-reports/ai-translation-qa-2026-08-13.md) |
| 2026-08-13 | FR5.12 embedding 语义检索优化 QA | ✅ 通过 — 10 新测试 + 550/550 全绿，1 Critical（加载 _row_map 未重建）已修复，[报告](test-reports/fr5.12-embedding-optimization-qa-2026-08-13.md) |
| 2026-08-14 | translation-memory 词典粒度重构 QA | ✅ 通过 — 27/27 翻译记忆测试全绿（零 Blocker/Critical/Major），全量测试的 28 failed+50 errors 为沙箱 tmp_path 预存问题（非本次引入），[报告](test-reports/translation-memory-granularity-refactor-qa-2026-08-14.md) |

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
| 2026-05-11 | AI 翻译器修复：EmbeddingConfig 属性访问回归修正（cfg.embedding_provider → cfg.embedding.provider 等5项）+ 默认模型名修正（gpt-4o → deepseek-v4-pro） | — |
| 2026-05-11 | AI 翻译器 UI 修复：内容区包裹 QScrollArea 防止窗口超出屏幕 + 默认宽度 560→680 | — |
| 2026-05-11 | Smart AI 系统提示词更新：身份重定位为 TransBridge 操作助手 + 多 Agent/Skill/文件/记忆能力感知 + 回复风格约束 | — |
| 2026-05-11 | Smart Assistant QA 全面审查：4维度并行（功能/安全/性能/代码质量），发现 3B+10C+16M+21m，综合评分 32/60，报告写入 docs/test-reports/smart-assistant.md | — |
| 2026-05-11 | AI 后处理修复：check_quality 工具 _execute_decisions 调用不存在的 entry._replace() 崩溃 → 改为直接修改 dataclass 字段 | — |
| 2026-05-11 | 新增 FR7.14 智能助手页面体验全面翻新需求（布局重组+对话增强+交互简化+视觉现代化，Markdown渲染器作为 infra/ 共享基础设施）→ changelog s08-001 | /bm-analyze |
| 2026-05-11 | FR7.14 方案策划：llm-chat plan.md 追加 Story-08（4子Story：Markdown渲染器/视觉翻新/布局重组/交互优化），plans/INDEX.md 同步更新 Story 数 7→8 → changelog s08-001 | /bm-plan |
| 2026-05-11 | Story-08-1 编码完成：新建 infra/markdown_renderer.py (~270行, MarkdownRenderer 类)，支持标题/代码块/列表/表格/链接/水平线，零外部依赖，12/12 验证通过 → changelog s08-002 | /bm-dev |
| 2026-05-11 | Story-08-2 视觉翻新与去Emoji：message_bubble 重写(MarkdownRenderer渲染)，chat_widget/tool_card/plan_card 样式现代化(圆角/配色/字体)，全局去除所有代码中emoji → changelog s08-003 | /bm-dev |
| 2026-05-11 | Story-08-3 布局重组与滚动优化：quick_actions 重写为 QuickActionsChips(chips标签行)，panel 精简布局，chat_widget 删除Agent指示器+合并工具栏+回到底部浮动按钮 → changelog s08-004 | /bm-dev |
| 2026-05-11 | Story-08-4 流式打字机与自动模式：chat_widget 实现流式渲染(50ms节流+MarkdownRenderer增量刷新)、自动模式开关(QSettings持久化)、admin级工具安全护栏、流式中断清理 → changelog s08-005 | /bm-dev |
| 2026-05-12 | 新增 FR7.15 Smart Assistant QA 全面修复需求 + 方案策划 + 7 Story 展开：基于 2026-05-11 QA 审查报告（3B+10C+16M+21m，评分 32/60），新建统一修复 Epic `smart-assistant-qa-fix`，7 Story 全部已确认，预估 22h → changelog planning-001 | /bm-plan + /bm-story-batch |
| 2026-05-12 | FR7.15 S01-S06 编码完成：13 文件修改，覆盖 2B+9C+10M — ReAct护栏接入/异步通知/MCP认证/路径校验/前置条件/ToolResult扩展/线程清理/ADR-008修复/RetryHandler实例化 → changelog s01~06-001 | /bm-dev |
| 2026-05-12 | FR7.15 S06 补充：v1 工具 @deprecated 标记 + ReAct retry 循环 + progress 锁修复 + TaskManager.reset() + clear_all_filters 权限修正 + list_local_projects 路径脱敏 → changelog s06-002 | /bm-dev |
| 2026-05-12 | FR7.15 S07 部分测试：新建 3 个测试文件（ConversationManager 10 用例 / ContextBuilder 7 用例 / MarkdownRenderer 14 用例），31 测试全部通过 → changelog s07-001 | /bm-dev |
| 2026-05-12 | FR7.15 S06 Minor 收尾：修复 17 项（m1/m2/m5/m6/m7/m8/m9/m11/m12/m14/m15/m19 + M2 深度去重 + M6 RetryHandler 集成），11 文件修改，5 个 v1 工具标记 deprecated，filter_entries 重命名，忙等轮询改 Condition.wait，ThreadPoolExecutor 复用，FAISS rebuild_index → changelog s06-003 | /bm-dev |
| 2026-05-12 | FR7.15 S05 补完 + S06 批量装饰器：MemoryStore 异步写入(MemoryWriterThread QThread) + LRU 淘汰(max_entries) + close()；ConversationManager _trim 重写为按轮次裁剪 + add_observation/plan_result 2000 字截断；build_system_prompt namespace 参数；panel closeEvent memory_store.close()；@require_collection 批量替换 8 函数(3 文件) + 装饰器补全 error_category/code → changelog s05-002 + s06-004 | /bm-dev |
| 2026-05-12 | FR7.15 S07 测试补完：5 个新测试文件（MemoryStore 10/MCP 10/ChatWorker 6/Observability 9/ExecutionEngine 10），45/49 通过(4 skip)，测试报告更新评分 51/60 + 审查结论 ✅ 通过 → changelog s07-002 | /bm-qa |
| 2026-05-13 | smart-assistant Stack Overflow 修复 (0xC00000FD)：`__init__.py` 模块级导入全部改为惰性 `__getattr__`（根因），`chat_widget.py` TaskManager 信号连接延迟初始化（次级防御）→ changelog s02-002 |
| 2026-05-13 | smart-assistant Stack Overflow Phase 1 解耦：MemoryWriterThread 改 threading.Thread + TaskManager 去 QObject/pyqtSignal 改回调 + except:pass 全量日志审计 (11处) + _run_llm_round 微阶段拆分 (3-stage QTimer) + 评审委员会纪要 → changelog s02-003 |
| 2026-05-13 | smart-assistant QA 审查 Blocker 修复：TaskManager.notify_* 使用 QMetaObject.invokeMethod(Qt.QueuedConnection) 跨线程投递回调 + _safe_callback 异常隔离 + 4维度并行测试报告 → changelog s02-004 |
| 2026-05-13 | smart-assistant Phase 2 QObject 解耦：ObservabilityCollector 去 QObject(回调注入) + ChatWorker/AgentWorker 去 QThread(AsyncWorker基类) + 7文件回调+QTimer桥接适配。后端 QObject 类从 6 → 1(仅剩ExecutionEngine) → changelog s02-005 |
| 2026-05-13 | smart-assistant Phase 2 修复：_SignalBridge(QObject+pyqtSignal)统一跨线程桥接替代QTimer/QMetaObject.invokeMethod + main_window遗漏isRunning→is_alive适配 → changelog s02-006 | — |
| 2026-05-14 | smart-assistant checkpoint 路径修复：execution_engine._checkpoint_path() fallback 从相对路径 `Path("data")` 改为 `ParatranzConfig.get_data_dir()`，消除 `src/transbridge/data/` 误创建 → changelog s05-004 | — |
| 2026-05-14 | llm-chat Story-08 方案补充：新增 Story-08-5（思考过程折叠显示）+ 全部 5 子 Story 提取到独立文件 `stories/story-08-experience-overhaul.md` → changelog s08-006 | — |
| 2026-05-14 | 新增 FR7.16 对话 UI 文档流重构需求：9 项子需求（纯文档流/文字头像/居中输入框/内联卡片/融入式系统消息/思考折叠/观测融入/面板放宽/保留元素）→ changelog s08-007 | — |
| 2026-05-14 | FR7.16 编码实现（Story-08-2/08-3/08-5）：message_bubble 重写为文档流 + thinking_indicator 新建 + chat_widget 输入框/系统消息/观测流化 + panel 放宽 + 卡片内联样式 → changelog s08-009 | — |
| 2026-05-14 | 新增 FR7.17 ToolResult 结构化数据传递增强：P0 数据序列化到 LLM 观察消息 + P1 6 工具补 data + P2 扩展字段（pagination/execution_meta/tool_suggestions） | /bm-analyze |
| 2026-05-14 | Smart Assistant 第五轮全量 QA 报告独立复核与修正：13 Agent 并行逐项验证 40 项 Blocker+Critical，推翻 1 项误报(B6)，修正 10 项描述偏差，问题识别率 97.5% | /bm-chronicle |
| 2026-05-15 | Smart Assistant 第五轮 QA 收尾②：4 Agent 并行修复 C18(Orchestrator迁移)/C19(Handler迁移)/M4(AST扩展)/Minor(冗余异常/常量/annotations) | /bm-orchestrator --auto |
| 2026-05-15 | Smart Assistant 运行时修复：循环导入/sip/setMaxLength/_worker守卫 + 全部组件移除 maxWidth 撑满面板 | — |
| 2026-05-15 | 评审委员会第二轮：路线 A 实施方法设计（4角色并行评审，产出 7共识+2分歧），新增 docs/council-review-tool-architecture-methodology.md | — |
| 2026-05-18 | agent-tool-expansion Story 16 编码完成：删除 Orchestrator/AgentWorker 死代码(~194行) + 7模块 register_tools() 注册样板消除(-35行)，净减~229行 | — |
| 2026-05-18 | agent-tool-expansion Story 17 编码完成：set_filters 合并 5→1（6 可选参数，None=保持/[]=清除），5 deprecated wrapper，Editor 14→10 工具，总非废弃工具 56→52 | — |
| 2026-05-20 | agent-tool-expansion Story 25 QA 审查：发现 PostProcessor/LLMPolisher 构造参数错误等 5 Blocker + 报告/断点/预览等 5 Critical 缺失，报告产出 docs/test-reports/story-25-postprocess-unification-qa.md | — |
| 2026-05-20 | agent-tool-expansion Story 25 后处理报告补全：集成 ReportGenerator (Excel生成) + 中间数据保留 (refine/polish/decisions) + list_quality_reports 工具 + start_polish scope 参数 + max_workers，proofreader 2→3 工具，changelog 007 | — |
| 2026-05-22 | agent-tool-expansion QA 第三轮修复审查 (4维度并行): 21/21 原始问题全部修复，4 项新发现问题已当场修复，综合评分 49/60，0 新增失败，[报告](test-reports/agent-tool-expansion-qa-round3-2026-05-22.md) | — |
| 2026-05-20 | agent-tool-expansion Story 25 QA 二次复核 + P0/P1 修复：14 项修复（3B+6C+5M）涵盖 tool_proofreader.py/tool_translator.py/agent_registry.py/post_processor.py + 15 测试更新，135/137 通过，changelog 008 | — |
| 2026-05-22 | agent-tool-expansion QA 第三轮修复：21 项 Major+Minor 全量修复 — base.py(m5+M3)/task_manager.py(m3+m4+M2)/tool_paratranz.py(M4+m7+m8+m9)/tool_parser.py(M1+M9)/tool_translator.py(M2+M3+M8+m6+m10)/tool_proofreader.py(M5+M8+M10+M2+M3+m6)/tool_editor.py(m1+M7)/test 补全(+27) | — |
| 2026-05-25 | FR11 工具提示词分层加载 QA 通过（综合评分 55/60）：356/356 零回归，28 新测试，0 Blocker/Critical/Major。已知限制：LLM 回归测试待手动运行、Phase 4 调优待数据 | /bm-qa |
| 2026-08-05 | tool-prompt-layering Phase 4 调优 QA 通过：161/161 smart_assistant 零回归，system prompt ~3,435 tokens（vs 全量 9,183，节省 62.6%），[报告](test-reports/tool-prompt-layering-s05-tuning-2026-08-05.md) | /bm-qa |
| 2026-05-25 | FR11 QA 修复 5 项：output_validator 放宽 str 类型 + orchestrator _stage_c 防御 + AI翻译器自动保存防抖 + 测试 mock save_to_file + INI model 修正为 deepseek-v4-pro | /bm-chronicle |
| 2026-05-25 | 评审委员会：智能助手工具提示词管理机制方案讨论（4角色并行独立评审）→ changelog 001 | — |
| 2026-05-25 | 评审委员会第二轮：交叉讨论与最终方案共识（4角色2轮讨论），更新 docs/council-reviews/council-review-tool-prompt-mechanism.md。用户新约束（拒绝描述瘦身、接受function calling迁移、架构层面方案）推动方案升级为 ToolPreviewBuilder + ToolVisibilityPolicy Protocol + tier纯元数据 + 三阶段路线 → changelog 002 | — |
| 2026-05-25 | FR11 工具提示词分层加载 Phase 1-3 编码完成：tool_registry.py (+118: summary + build_tool_directory + build_tool_help)、tool_default.py (+16: get_tool_help 注册)、prompts.py (+48/-22: 分层 prompt 重构 + 删除旧指南)、test_tool_prompt_layering.py (28 测试)。工具段 ~14,000 → ~1,040 tokens (92.5% 节省)，356 全量测试零回归 | /bm-pilot → /bm-dev |
| 2026-08-05 | FR11 tool-prompt-layering Story 01 (Phase 0 Token 精确测量) 完成：scripts/measure_tokens.py (测量脚本) + docs/temp/tool-prompt-layering-token-measurement.md (测量报告)。分层 system prompt ~3,365 tokens vs 全量 9,183 (节省 63.4%), 42 工具/7 namespace，发现 ToolRegistry 双重导入隐患 | /bm-pilot |
| 2026-08-05 | FR11 tool-prompt-layering Story 05 (Phase 4 调优) 完成：工具目录瘦身(1,324→1,249, -5.7%) + 路由表关键词扩充(401→547) + ToolRegistry 导入统一(8文件, 消除双重导入) + 最终测量报告。System prompt ~3,435 tokens，161/161 测试零回归。Epic 全部完成 ✅ | /bm-pilot |
| 2026-08-13 | 修复 19 项预存测试失败：6 个测试文件同步到演进后的数据模型（复合 id / key-context 对调 / DSD 字段 / SSEPluginWithContext / extract_strings_with_context），`pytest` 516/19 → 535/0 全绿。根因定位报告 + 增量归档 | /bm-pilot |
| 2026-08-13 | 修复 embedding 语义检索断连（P0 回归）：term_database.py 过期导入路径对齐 infra 包 + create_embedding_client 工厂函数改读 config.embedding.* 子对象（原读旧平铺字段致语义召回静默失效），新增 5 用例回归保护，540/540 全绿 → changelog ai-translation/fix-003 | /bm-pilot |
| 2026-08-13 | FR5.12 embedding 语义检索优化需求分析：基于 vector-term-retrieval 后续优化方向，三项增强（批量召回 / 增量索引+缓存 / BM25 混合检索），5 子需求 → changelog ai-translation/fr5.12-001 | /bm-pilot |
| 2026-08-13 | FR5.12 架构决策 ADR-013：引入 rank_bm25 + 加权求和融合 + ID 映射软删除增量索引 + term_vector_index 层 LRU 缓存 → changelog ai-translation/fr5.12-002 | /bm-pilot |
| 2026-08-13 | FR5.12 方案策划：3 Story（批量召回+阈值统一 / 增量索引+缓存 / BM25 混合检索），plan 已确认 → changelog ai-translation/fr5.12-003 | /bm-pilot |
| 2026-08-13 | FR5.12 Story 展开：3 个详细实现指南（数据流/接口/边界/伪代码/测试）全部已确认 → changelog ai-translation/fr5.12-004 | /bm-pilot |
| 2026-08-13 | FR5.12 编码实现：增量索引+软删除压缩 + LRU 缓存 + BM25 融合检索（rank_bm25），549/549 测试全绿 → changelog ai-translation/fr5.12-005 | /bm-pilot |
| 2026-08-13 | FR5.12 QA 审查：1 Critical（加载 _row_map 未重建）修复 + 550/550 全绿，状态 → 已实现 → changelog ai-translation/fr5.12-006 | /bm-pilot |
| 2026-08-14 | FR15 翻译记忆（词典）系统：需求分析 FR15 + ADR-014 + plan + 5 Story + 技术议会 2 轮评审（5 专家）+ backend 实现（model.py/manager.py 单表权威对象+双索引，17 测试通过）。scope 收敛为 project/global 两档，game 降为 global 的 scope_id 标签 → changelog translation-memory/story-01-001 | /bm-pilot → /bm-council → /bm-dev |
| 2026-08-14 | FR15.1.6 词典粒度重构：一文件一 mod（.tbdict）+ scope 降为单值标签 + 多词典全查兜底 + 冲突可视化仲裁 + 分享导入 + 旧数据弃置。技术议会查漏（13 项问题清单）+ 逐条确认 13 项决策 + ADR-014 更新节 + S06-10 Story 展开 + 编码（model/manager/panel/dialog/conflict_dialog）+ 27 测试通过 → changelog translation-memory/story-02-005 | /bm-pilot → /bm-council → /bm-arch → /bm-plan → /bm-story-batch → /bm-dev → /bm-chronicle |