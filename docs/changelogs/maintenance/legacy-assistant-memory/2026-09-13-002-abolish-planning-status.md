# 002：废除旧记忆需求与规划状态

- 日期：2026-09-13
- Epic / Story：maintenance / legacy-assistant-memory
- 来源：用户要求将对应需求、plan、story 等状态统一设置为废除。
- 范围：本记录只描述前次代码移除后的文档状态补全，不重复归属代码删除。

## 文件变更

- `docs/requirements.md`：FR7.13.3、FR10.5 改为“已废除”，FR7.13 总状态注明部分废除；明确新记忆另立需求与计划。
- `plans/agent-upgrade/plan.md`：S04 状态、历史验收及实施步骤全部废除，退出当前实施范围。
- `plans/agent-upgrade/stories/story-04-long-term-memory.md`：整篇 Story 已废除，旧设计仅供历史查阅，验收项取消任务复选框。
- `plans/smart-assistant-qa-fix/plan.md`：S03 的旧记忆文件存储/检索、S05 的线程/LRU/关闭及 S07 的旧记忆测试部分废除。
- `plans/smart-assistant-qa-fix/stories/story-03-security-hardening.md`：旧记忆存储/检索方案废除；保留 Prompt 注入防护及其他安全要求。
- `plans/smart-assistant-qa-fix/stories/story-05-thread-resource.md`：旧记忆专属接口、步骤与验收标为废除，其他线程与资源范围保留。
- `plans/smart-assistant-qa-fix/stories/story-06-code-cleanup.md`：取消旧 MemoryStore 异步写入的上游依赖。
- `plans/smart-assistant-qa-fix/stories/story-07-testing.md`：旧记忆测试验收及步骤 4 废除。
- `plans/smart-assistant-refactor/plan.md`：S04 子任务 B 的写入线程外提、重导出和验收废除。
- `plans/smart-assistant-refactor/stories/story-04-orchestrator-memory-task-cleanup.md`：子任务 B 与对应接口、验收、文件清单废除，A/C 保留原范围。
- `plans/llm-chat/stories/story-09-chatwidget-refactor.md`：旧 MemoryRetriever 集成部分废除，其余拆分范围保留。
- `docs/adr/008-smart-assistant-code-layering.md`：旧记忆子包布局与 D5 外提决策废除。
- `docs/adr/009-agent-file-memory-reflexion.md`：旧长期记忆和三模式降级明确标为已废除历史决策。
- `docs/adr/010-infra-extraction.md`：旧记忆消费方接线与条件初始化部分废除；共享 infra 能力保留。
- `plans/INDEX.md`、`docs/INDEX.md`：同步各入口的部分废除状态，S04 不再列为全部完成的一部分。
- `docs/changelogs/INDEX.md`：登记本增量；已有 001 记录和其他历史变更日志保持原样。

## 验证

- PowerShell 文档断言：9 个相关 plan/story 的状态均含“已废除”；未发现仍有效的旧记忆验收任务；整体废除的 S04 不再含任务复选框。通过。
- `git diff --check`：通过。
- 本轮仅修改 Markdown，未重复运行 pytest 或 Ruff；上轮代码验证见 [001](2026-09-13-001-remove-legacy-memory.md)。

## 边界与遗留

只废除旧 AI 助手记忆。混合文档保留非记忆功能的原有状态；FR15 翻译词典、当前会话历史与上下文压缩不在废除范围。旧设计留作历史，新记忆另立需求、plan 和 Story。本轮无未完成项。
