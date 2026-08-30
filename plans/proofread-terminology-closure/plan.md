# Proofread 校对策略术语确定性闭环计划

- **状态**：已完成（3/3，2026-08-30）
- **日期**：2026-08-30
- **对应 ADR**：ADR-028（2026-08-30 修订）

## 目标与非目标

在默认 `proofread` 的一次开放式校对后增加确定性术语复检，仅对仍不一致的条目执行有界、批量的 `LLMRefiner` 恢复，并在第二次技术与术语验证全部通过后才接受候选。

不复制 strict 的 QualityGate、Polisher、Arbiter 或完整检测链；不以本地字符串规则判断语义、否定、流畅度或风格；不改变公开策略名、候选提交边界或持久化 schema。

## 当前事实与约束

- `ProofreadStage` 已负责结构化 JSON 首轮请求、Token 分批、并发、有界响应恢复和受保护语法验证，文件已接近 500 行，不能继续增加新职责。
- `ConsistencyChecker` 和 `TermDatabaseManager.match_terms_for_entry` 已提供条目作用域匹配；`LLMRefiner` 已提供只修明确问题的单条/批量 Structured Outputs 合同。
- 翻译后处理、独立/混合校对和 Smart Assistant 各有 Proofread 装配入口，必须共享同一闭环实现。

## Story 1：结构化术语问题与条件闭环阶段

### 验收标准

- 术语问题携带匹配形式、标准术语和标准译名，不从消息字符串反向解析。
- 首轮技术有效候选按条目复检；同一条目的全部剩余问题一次交给 Refiner。
- Refiner 批次按条目不可拆分，并同时受 refinement batch size 和 Token 上限约束。
- 二次验证失败时恢复运行前译文、候选不可提交，并产生带 EntryKey 的可操作诊断。

### 文件与实施

- 将现有开放式实现抽取为单一职责模块，保留 `ProofreadStage` 公共 API 兼容外层。
- 新增独立术语闭环模块，复用作用域 resolver、`ConsistencyChecker` 的确定性判定模型、`LLMRefiner` 和 `StableContentBatcher`。
- 最小扩展 `PostProcessIssue` / `RefineResult`，显式传递结构化术语字段和解析有效性。

## Story 2：Prompt、入口接线与运行控制兼容

### 验收标准

- 首轮 Prompt 明确全面独立校对、术语强制但非完整问题清单、合格不改和保护语法；首轮 JSON 不含 detected issues。
- 翻译、独立/混合和 Smart Assistant 路径使用相同闭环；Project/Variant/plugin 作用域不串线。
- 无管理器、无匹配术语或首轮已满足术语时不调用 Refiner。
- 共享请求预算、最大并发、暂停/取消和稳定 EntryKey 映射保持不变。

## Story 3：失败矩阵与回归验证

### 验收标准

- 覆盖单条多术语、多个失败条目同批且作用域隔离、成功闭环和仍缺术语。
- 覆盖占位符/标签损坏、空值、缺失、重复、未知、非法 JSON，以及批量到单条的有界恢复。
- 证明非术语语义错误仍由首轮 Proofread 修正；无术语路径无额外调用。
- 聚焦测试、受影响模块回归、Ruff check/format 和 diff 检查通过，或明确记录与本次无关的既有失败。

## 依赖、风险与回退

Story 1 先于入口接线和完整测试。风险主要是旧 Refiner 以 legacy id 映射结果、批量失败降级单条以及插件作用域丢失；实现必须以 EntryKey 生成稳定别名并把首次解析的每条术语显式传给 Refiner。回退时可切换 `strict`，失败候选始终保持不可提交。

## 完成证据

- Story 1：开放式首轮与术语闭环已拆成独立职责；`PostProcessIssue` 使用结构化术语字段，`RefineResult` 使用结构化有效性/失败分类，Refiner 响应解析也已从原超限模块抽出。
- Story 2：翻译、独立/混合和 Smart Assistant 三个装配入口统一透传 refinement batch size、Token/输出上限、并发和条目作用域术语；首轮 Prompt 保持结构化 JSON 且不含 detected issues。
- Story 3：新增闭环与入口测试覆盖条件调用、同条目多问题、跨条目/插件隔离、二次验证、非法响应、回退和稳定 EntryKey；相关模块与扩大回归通过。
- 验证：最终 Refiner/术语闭环聚焦 37 项通过；AI translator 与 Smart Assistant 扩大回归 367 项通过；`uv run ruff check src tests`、`uv run ruff format --check src tests` 和 `git diff --check` 通过。
