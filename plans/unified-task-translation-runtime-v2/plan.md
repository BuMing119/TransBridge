# Unified Task and Translation Runtime V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR20、FR21（含 FR21.9 配置增量）、FR17.3、NFR1.2/1.3、NFR2.1
- **架构**：ADR-019（含统一 ConfigRepository 增量）、ADR-003/004/005/007/008/011/013 的保留或 2026-08-18 增量
- **问题**：R-027～R-039
- **依赖**：`platform-contract-foundation-v2` S02～S04；`translation-io-kernel-v2` S02/S05；`project-session-persistence-v2` S04 可与前两 Story 并行、在 owner 集成前完成

## 目标与边界

以 TaskRuntime 统一长任务状态、owner、run_id、能力型控制、取消提交屏障、checkpoint 和订阅生命周期，并将 AI 翻译、后处理和 Graph 作为 workload adapter 迁入。运行规格不可变，候选结果在唯一提交点进入正式 Collection，报告由 canonical snapshot 派生。

本 Plan 保留现有三轮策略、Prompt TOML、Mixed 动作规则和 Graph 能力；它们不再拥有应用级任务状态。ParaTranz 与 FOMOD 的特有阶段由各自 Plan 实现，但复用本运行时。

## Story 清单

### Story 01：TaskRuntime 核心状态机、Owner 与订阅

[详细设计](stories/story-01-task-runtime-state-owner.md)

- **目标**：建立 queued/running/paused/cancelling/cancelled/completed/failed 的唯一权威状态。
- **文件落点**：新增 `application/tasks/`、内存 backend adapter；兼容 `smart_assistant/tools/task_manager.py`；合同测试。
- **实施**：定义 JobSpec/Ref/Snapshot、OwnerRef、RunId、transition table、capabilities、Subscription handle；终态不可逆；同步 OperationResult 与 Deferred TaskRef 类型固定；TaskManager 变为 facade。
- **验收**：非法迁移被 domain error 拒绝；终态互斥；owner 不匹配无法控制任务；unsubscribe 实际移除 wrapper；优化模式不绕过校验。
- **测试**：状态模型属性测试、owner/late event、listener 回归、同步/异步类型、并发 transition race。

### Story 02：取消提交屏障、Stop/Shutdown 与 Backend Adapters

[详细设计](stories/story-02-cancel-shutdown-backends.md)

- **目标**：取消后停止新副作用，迟到结果不提交，stop/shutdown 释放资源。
- **文件落点**：TaskRuntime backends、QThread/thread pool adapters、应用 shutdown wiring、Task Monitor adapter。
- **实施**：cooperative cancellation token；run_id+terminal guard；stop 保留策略化 checkpoint；shutdown 拒绝新任务并等待/取消活动任务；backend 不写状态；Task Monitor 只读 JobSnapshot。
- **验收**：取消后不出现 completed；stop 不等同于清队列；shutdown 后线程/句柄释放；按钮按 capability 启用。
- **测试**：取消 race、迟到回调、异常 backend、shutdown timeout、UI dispose、100 条假 LLM 最大并发 3 与取消 P95≤1s。

### Story 03：CheckpointPort、Graph Workload 与幂等恢复

[详细设计](stories/story-03-checkpoint-graph-recovery.md)

- **目标**：统一原子 checkpoint 身份和 Graph frontier/result 恢复语义。
- **文件落点**：新增 checkpoint port/filesystem adapter；迁移 AI/PostProcess/Graph checkpoint；Graph Task adapter。
- **实施**：checkpoint 保存 run spec fingerprint、稳定 EntryKey、动作、阶段、frontier、结果摘要和 revision；临时写+校验+替换；规格不符拒绝；修正 pause event、分支/frontier 恢复；GraphExecutor 不拥有 Job 终态。
- **验收**：重复恢复不重复提交；损坏 checkpoint 明确失败/隔离；Graph pause/resume 保持 frontier 和结果；100k 更新 P95≤100ms。
- **测试**：崩溃点 fault injection、重复恢复、规格漂移、分支/循环/HITL 特征测试、性能基准。

### Story 04：不可变 TranslationRunSpec 与动作/上下文计划

[详细设计](stories/story-04-translation-runspec-planning.md)

- **目标**：固定语言、Prompt/profile/model/retrieval/scope，并为每条目恰好分配动作和上下文。
- **文件落点**：新增 translation application models/planner；迁移 batch planner、scope selector、MixedWorker 构造；入口 adapters。
- **实施**：RunSpec 固化统一 ConfigRepository 的 immutable revision/配置摘要；ActionPlan 分区 translate/polish/both/skip；hidden/locked 排除；上下文一次分配并保留 quest 顺序 barrier；provider/base_url/model 原子切换；检索 disabled 零加载。S04 同时迁移旧 INI 到 `transbridge.ini`，移除 `llm_profiles` 与所有生产直接 ConfigParser 旁路。
- **验收**：运行中配置变化不影响当前 run；每条目每轮恰好一个动作；unknown context 有诊断；target language 从入口到 prompt 一致；GUI/Agent/MCP/FOMOD 读取同一配置 revision，旧 INI 迁移失败不破坏旧文件且新 INI 不含 secret。
- **测试**：分区属性测试、全 context corpus、quest 顺序、profile/lang parity、disabled retrieval import/load 探针。

### Story 05：AI 翻译 Workload、候选缓冲与唯一提交

[详细设计](stories/story-05-translation-workload-commit.md)

- **目标**：修复 MixedWorker 不可执行、直接 mutation 和 partial/failed 伪成功。
- **文件落点**：迁移 `ai_translator/`、UI workers、Agent translator tools；新增 CandidateSet/commit use case。
- **实施**：workload 只产候选/诊断；批次重试有界；最终 ChangeSet 经 run_id/revision/terminal guard 一次提交；checkpoint 记录已接受批次；Mixed/Agent 模式统一。
- **验收**：真实 mixed 成功链可执行；失败批次汇总为 partial/failed；取消候选不覆盖正式集合；恢复不重复 LLM 副作用或提交。
- **测试**：受控 fake HTTP server 成功/限流/失败、真实构造链、取消 race、重复恢复、Collection commit 审计。

### Story 06：PostProcess 候选链、Stage 与 Canonical Report

[详细设计](stories/story-06-postprocess-report.md)

- **目标**：让检测→细化→复验→润色→仲裁处理候选值，并统一报告/UI/Excel/历史。
- **文件落点**：`ai_translator/post_processor/`、报告 UI/Excel adapters、history persistence。
- **实施**：每阶段读取上一候选；精确 scope；StagePolicy 驱动；batch outcome 结构化；唯一 commit；定义 ReportSnapshot schema，所有展示/导出由 snapshot 派生。
- **验收**：refiner 输出进入 polisher/arbiter；异常不被吞；UI、Excel、历史在相同 run_id 下字段/计数一致；partial/cancelled 可见。
- **测试**：真实小语料成功链、阶段候选 fixture、失败/取消/恢复、报告 golden 与多入口 parity。

### Story 07：Task Monitor、Session/Agent/GUI 集成与兼容删除门禁

[详细设计](stories/story-07-runtime-entrypoint-integration.md)

- **目标**：切换生产调用方到统一 runtime，证明旧 TaskManager/MixedWorker/Graph 状态不再权威。
- **文件落点**：Task Monitor、SessionController、ExecutionEngine、GUI workers、Agent tools、composition wiring。
- **实施**：入口取得 JobRef；Session owner 过滤事件；AWAITING_TASK 生产路径可达；UI worker 只做 backend adapter；记录每个旧 API 调用方和删除条件。
- **验收**：同一 Job 在入口、Session、Monitor、报告终态一致；旧会话迟到回调被拒绝；无 writable mirror；旧 facade parity 通过。
- **测试**：GUI/Agent/MCP 任务端到端、Session 切换中任务、Monitor 控制、shutdown、兼容路径对比。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR20；R-027～032 | S01～S03、S07 | `task-monitor`、`agent-tool-expansion` S02/S26、`agent-upgrade` Graph/checkpoint：`partially-verified`, `blocked_by` 本 Plan |
| FR21.1～21.6；R-033～037 | S04～S06 | `ai-translation`、`ai-post-process`: `partially-verified`, runtime/commit 部分 `superseded_by` 本 Plan |
| FR21.7；R-038 | S06 | 旧报告完成声明 `blocked_by: unified-task-translation-runtime-v2/S06` |
| FR21.8；R-039 | S04 | `fr5.12-embedding-optimization`: `partially-verified`, dependency/capability 另受平台与发布 Plan 阻断 |

## 风险、回退与完成门禁

- 风险：TaskRuntime 变成上帝类。控制：runtime 只管理调度/状态/事件；业务保持 workload/use case。
- 风险：并行迁移导致双提交。控制：CandidateSet + 唯一 commit port；旧 worker 写路径逐个封禁。
- 回退：backend adapter 可回切，Job 状态与 checkpoint schema 不回退；失败时保留旧 facade 只读兼容。
- 完成门禁：状态属性测试、真实翻译/后处理成功链、取消与恢复故障测试、性能预算和跨入口终态一致性全部通过。
