# ADR-019：Unified Task Runtime、互斥终态与幂等恢复

- **状态**：已接受（2026-08-18）
- **日期**：2026-08-18
- **对应需求**：FR17.3、FR20、FR21、FR22.2～22.4、FR23.1～23.6、NFR1.2、NFR1.3、NFR2.1
- **关联 ADR**：ADR-003、ADR-004、ADR-007、ADR-008、ADR-011、ADR-012、ADR-014、ADR-016、ADR-018
- **承接根因**：R-027～R-039、R-041、R-043～R-050
- **部分取代**：ADR-004 的“QThread 是唯一后台通道”、ADR-007 的 MixedWorker 权威编排、ADR-008 的 TaskManager 单例状态、ADR-011 的独立 checkpoint 生命周期

## 背景与约束

当前长任务分散在 QThread、threading.Thread、ThreadPoolExecutor、GraphExecutor、TaskManager 和各业务 worker 中。TaskManager 允许任意 `set_status()`，pause/cancel 不校验任务能力和合法迁移，取消后工作线程仍可写入；deprecated callback 包装后无法用原 callback 注销。Session、Project 和入口 owner 未进入任务身份。

翻译、后处理、ParaTranz 和 FOMOD 对 partial、failed、cancelled、checkpoint 与报告的含义也不一致。统一运行时必须适配 GUI 与 headless 入口，不能把 PyQt QThread 或某个 Agent Graph 当成领域状态机。

## 决策

### 0. 配置快照来自统一 ConfigRepository（2026-08-18 增量）

所有 INI 配置收敛到单一 `transbridge.ini` 与版本化 ConfigRepository。Repository 是唯一 `ConfigParser`/文件锁/原子 replace 所有者；它产生 LLM、ParaTranz、MCP、Guardrails 等不可变子快照。`provider/base_url/model` 是不可拆分的运行规格输入，`llm_profiles` 不再保留或迁移为新概念。secret 只记录 credential reference，旧 `paratranz_config.ini` 按“安全存储验证 → 原子写新文件 → 回读校验 → 带校验和备份”的顺序只读迁移；失败不覆盖旧文件。LLMConfig/ParatranzConfig 先保留为委托 facade，删除须经所有入口迁移与发布周期验证。

### 1. TaskRuntime 是应用层唯一长任务端口

Application use case 对需要后台执行的工作提交不可变 `JobSpec`：

```text
submit(JobSpec, RuntimeContext) -> JobRef
get(JobRef, owner) -> JobSnapshot
pause/resume/cancel/stop(JobRef, owner) -> ControlResult
subscribe(filter, callback) -> Subscription
shutdown(policy) -> ShutdownResult
```

同步完成的 use case 直接返回 OperationResult；后台化的 use case 返回 JobRef。不得在同一调用条件下混用业务结果、线程对象和字符串 task_id。

### 2. JobSpec、JobRef 与 owner

`JobSpec` 至少包含：

- 唯一 `run_id`、job_type、不可变输入引用和输入 fingerprint；
- project_id、variant_id、session_id、entrypoint owner；
- 配置/Profile/Prompt/语言/模型摘要；
- capability requirements、checkpoint policy、commit policy；
- 可声明的 `supports_pause/cancel/resume/checkpoint`。

`JobRef` 是不可猜测 ID + owner scope。所有查询和控制验证 owner；跨 Session/Project 的管理操作需要显式更高权限。可复用的显示名称不得作为任务身份。

### 3. 统一状态机与互斥终态

状态机固定为：

```text
queued -> running <-> paused
running/paused -> cancelling -> cancelled
running/paused -> completed | failed
queued -> cancelled
```

- `completed`、`failed`、`cancelled` 是互斥终态；`partial` 是 OperationOutcome 的结果状态，在任务层仍以 completed-with-partial-outcome 表示，并由调用结果明确展示，不得伪装为全成功。
- 所有迁移由 TaskRuntime 内部 transition table 校验；公开任意 `set_status` 被禁止。
- 非法迁移返回 domain error，在优化模式下行为相同，不使用 `assert`。
- 终态一旦提交不可覆盖；迟到 worker 回调按 run_id/lease 校验后丢弃并记录诊断。

### 4. Backend 是执行 adapter

TaskRuntime 可选择 threading、ThreadPoolExecutor、QThread wrapper 或进程 backend，但 backend 只负责调度和取消信号，不拥有任务状态。

- GUI 的 QThread/Qt signal 作为 TaskEvent adapter；业务代码不继承 QThread。
- Agent Graph 是一个 job workload，可在节点层报告进度和 checkpoint，但其终态由 TaskRuntime 提交。
- MCP/CLI 使用 headless backend，不导入 Qt。
- 并发配额按 job_type、owner 和外部服务分别配置；不得由每个工具自行创建无限线程。

### 5. 能力型控制与取消屏障

任务只暴露 JobSpec 声明且 backend 实际支持的控制能力。pause 是协作式安全点，不支持时按钮/工具不可见；不得创建 pause_event 后假装业务循环会检查。

取消流程：

1. transition 到 cancelling；
2. 设置 cancellation token，阻止开始新的外部副作用；
3. workload 在安全点停止；
4. 未提交候选丢弃，staging 按策略清理；
5. 提交 cancelled 终态和 checkpoint/diagnostics。

所有正式 mutation/publish 调用 commit guard，验证 run_id 仍为 running 且 owner/revision 未变化；取消后的迟到结果不能写集合、Variant、远端或正式文件。

### 6. Stop 与 Shutdown

- `cancel`：终止一个 job，可按 policy 保留可恢复 checkpoint。
- `stop`：业务级停止当前运行，通常保留已验证 checkpoint 和 partial outcome，不等同于清空队列。
- `shutdown`：TaskRuntime 停止接收新任务，按 `wait | cancel | checkpoint-and-cancel` 策略处理活动任务，等待有上限并释放 backend、订阅和外部客户端。

应用退出必须调用 AppRuntime.shutdown；daemon thread 自然消失不构成成功 shutdown。

### 7. CheckpointPort 与幂等恢复

Checkpoint 由统一端口保存，包含 run_id、JobSpec digest、input fingerprint、owner、稳定 EntryKey、已完成步骤、候选/提交边界、revision 和 schema version。

- 使用 staging + 校验 + 原子替换；
- 恢复前验证 JobSpec/input/owner/schema；
- 每个提交步骤拥有 idempotency key，重复恢复不会重复修改集合、上传远端或发布文件；
- checkpoint 记录“已计算”和“已提交”两个边界，避免候选存在就被视为正式结果；
- Graph、翻译、后处理、ParaTranz 和 FOMOD 可扩展 payload，但共享 envelope 和验证。

ADR-011 的 graph checkpoint 保留为 payload adapter，其路径、身份、owner、终态和提交语义由本 ADR 约束。

### 8. 事件、订阅与 Task Monitor

TaskRuntime 发布只读 TaskEvent：created、state_changed、progress、diagnostic、artifact、finished。`subscribe()` 返回幂等 `Subscription.dispose()`；回调包装关系由 runtime 保存，确保可用原订阅句柄注销。

Task Monitor、SessionController、GUI 进度窗口、Agent observation 和 MCP 查询均是事件 projection：

- 只读，不可直接改 TaskHandle；
- 按 owner/run_id 过滤；
- 控制按钮由 capability + current state 派生；
- 终态和 OperationOutcome 与原入口报告一致。

### 9. 工作流提交模型

翻译和后处理使用 candidate → validate → commit；只有 commit use case 修改 Collection/Variant。FOMOD 和文件发布使用 staging → validate → atomic publish。ParaTranz 使用 plan/dry-run → confirm → remote operations → isolated merge → commit。

单项失败可按错误分类和幂等性策略重试；最终 outcome 汇总 succeeded/failed/skipped/cancelled，存在未接受失败时为 partial 或 failed。异常不得吞掉后返回 completed。

## 备选方案

### 继续扩展 TaskManager 单例

无法解决 owner、组合根、多个 backend 和应用 shutdown，且任意状态写入风险继续存在，拒绝。

### 全部统一为 QThread

不适用于 MCP/CLI/headless 测试，并将业务生命周期绑定 UI 框架，拒绝。

### 引入第三方工作队列

Celery/RQ 等需要额外服务，不适合本地桌面模块化单体。先定义端口并使用进程内 backend；未来达到跨进程需求时可新增 adapter。

## 影响与风险

- 正面：取消、恢复、终态和 Task Monitor 不再各自定义；所有入口可共享。
- 成本：现有 worker/TaskManager/Graph callback 需逐步包装，迁移期要防止双通知。
- 风险：统一 runtime 变成跨域上帝类。缓解：runtime 只管调度、状态和事件；翻译、同步、FOMOD 逻辑仍在各自 use case/workload。

## 迁移与回退

1. 先实现状态机、JobSpec/Ref/Snapshot、Subscription 和内存 backend。
2. 提供 TaskManager compatibility facade，将旧 register/notify 映射到 runtime；禁止新增旧 API 调用。
3. 先迁移一个可控长任务并验证取消/迟到结果/订阅释放，再迁移 translation/postprocess、Graph、ParaTranz、FOMOD。
4. Task Monitor 切为统一 projection 后，旧回调总线保持只读桥接。
5. 任一 workload 迁移失败可回退 facade，但不得同时让新旧 runtime提交同一正式结果。
