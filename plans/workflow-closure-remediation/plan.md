# 跨模块流程闭环整改

- 状态：S01～S16 本地完成。2026-10-12 追加 S15～S16：用户确认退出时取消任务、回滚未提交内容；关联回归 502 passed、2 skipped，最终回执顺序调整后的 39 项复验通过。历史验证与边界见 S10～S14 记录。
- 日期：2026-10-07。
- 来源：全项目流程审查确认的 8 个问题；实施前审查基线为 17 个新增场景中 13 个失败、4 个对照通过。
- 需求：[requirements](../../docs/requirements.md) 中 FR18 身份与 I/O、FR19 生命周期原子性、FR20 统一任务、FR23 FOMOD、FR30 请求生命周期。
- 架构：[ADR-017](../../docs/adr/017-translation-io-kernel-v2.md)、[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-040](../../docs/adr/040-assistant-user-request-lifecycle.md)、[ADR-043](../../docs/adr/043-assistant-turn-results-and-undo.md)、[ADR-045](../../docs/adr/045-original-qualified-plugin-entries.md)。
- 承接：[AI 部分结果](../ai-partial-results/plan.md)、[项目持久化 V2](../project-session-persistence-v2/plan.md)、[助手任务一致性](../assistant-task-context-consistency/plan.md)、[FOMOD V2](../fomod-pipeline-v2/plan.md)、[原文限定身份](../original-match-entries/plan.md)。这是新增的跨 Epic 修复批次，不重写历史完成记录。

## 目标与边界

取消、重试、版本切换和异常收尾都走同一权威提交/状态机制。保留已提交业务结果、现有公开 facade、普通词条 key/id 以及用户已有未提交改动。

不重构整个运行时、不替换 JSON 存储、不放开写工具自动重试、不绕过术语一致性验证、不自动重放历史不确定写操作。真实远端及安装包验证按修复影响另行执行，不作为离线回归已通过的事实。

## 当前执行阶段

- [x] 审查、复现与实施方案；确认现有用户改动边界。
- [x] S01/S06：AI 取消提交保护和术语连接生命周期；207 项相关回归及终态订阅调整后 60 项复验通过。
- [x] S02/S03：版本并发校验与持久化事务恢复；636 项相关回归通过，包含 44 项事务故障与并发测试。
- [x] S04/S05：助手重试及步骤终态；1285 项扩展回归、收尾阶段 103 项复验，以及最终确认/回执故障组合 8 项复验通过。
- [x] S07/S08：FOMOD 身份与初始化终态；128 项 FOMOD/GUI/插件 I/O 回归通过。
- [x] 合并复核、交叉回归与全库静态检查；最终组合 211 passed、1 skipped，Ruff 全库通过。
- [x] 增量记录、索引更新与临时目录清理；见[本批记录](../../docs/changelogs/workflow-closure-remediation/story-01-to-08/2026-10-07-001-close-workflow-gaps.md)。

## S09：并发复审修复

- [x] 三项问题确定性复现：跨会话回执通知丢失、旧轮次后台错误污染当前会话、AI 控制事件乱序。第三项是原有公共 API 并发风险，未认定普通 GUI 操作必然触发。
- [x] 实现并补 26 项正式回归：回执按会话保留待重扫通知；释放收尾区分原轮次资源清理与当前 UI 副作用；AI 状态版本与 worker 控制动作串行交付。
- [x] 联合复验：助手 1312 passed、1 deselected；AI/runtime 163 passed，覆盖同会话新通知、新轮次/切换/关闭后的迟到回调、暂停恢复乱序及绑定/解绑竞争，并复验原取消提交边界。
- [x] 静态检查、[独立增量记录](../../docs/changelogs/workflow-closure-remediation/story-09/2026-10-07-001-concurrent-notifications-and-controls.md)及本批临时目录清理完成。

仅修改上述三个职责及其回归测试，不改变存储格式、公开任务 API 或业务执行权限；不得以业务操作重放恢复回执，也不得用新锁形成 GUI 等待后台的循环依赖。

## S10～S14：2026-10-12 流程复查修复

- [x] S10：关闭窗口前检查未提交的行内译文草稿；默认保留窗口，明确放弃或成功重试后方可退出。最终保存及模态确认期间的新修改、新后台任务也重新校验。关闭联合回归 31 项通过。
- [x] S11：单文件、批量插件及 EET 解析复用迁移步骤；批量处理应用所选迁移输入；异常明确报告来源及部分失败。共享 XML 迁移入口同时核验格式，保留合法空文件及 BOM/UTF-16 支持。最终迁移组合 61 项通过。
- [x] S12：ParaTranz 下载逐文件记录读取失败；批量及单次下载区分失败、部分完成与成功，不把失败的空结果提交或计为成功。41 项组合验证通过。
- [x] S13：任务活动中心接入功能侧结果/诊断及重新准备；当前终态任务和历史共用校验。AI 校对恢复接到现有任务记录窗口，指定 AI 结果异步定位。重新执行必须核对当前范围、预检并确认，助手所属请求仍拒绝旁路重放。相关操作/UI/契约组合 163 项通过。
- [x] S14：更新行内编辑提交边界静态守卫，稳定菜单折叠测试，任务中心无障碍用例改用真实活动投影。最终联合回归 335 项通过；全量 5939 passed、2 failed、10 skipped、13 deselected，两项失败经夹具修正后的 24 项组合及单项性能独立复验通过，未重跑整个全量套件。全库 Ruff/格式通过，[本轮增量记录](../../docs/changelogs/workflow-closure-remediation/story-10-to-14/2026-10-12-001-connect-remaining-workflows.md)、索引及临时目录清理完成。

本轮基线：不含 `llm/slow` 的测试为 5859 passed、2 failed、10 skipped、13 deselected；失败为已迁移行内编辑的 AST 允许集过期及菜单折叠时序用例。Ruff 检查和格式检查通过。保护本轮开始时已有的大量工作区变更，增量记录仅归属于上述修复。

任务中心复用 AI 持久化报告和校对恢复窗口；写回、同步和 FOMOD 回执沿用本次进程的有界结果缓存，缓存失效时不提供结果按钮。重新准备不生成任务或重放旧参数，实际提交仍在原功能的预检/确认边界；助手所属请求必须返回原请求处理。没有新增通用检查点执行器或更改持久化格式。

职责复核：解析协调器缩至 499 行，迁移准备提取到无 Qt 的 `parse_migration.py`；任务导航由专责 `task_navigation.py` 承担，核心任务层不依赖 Qt。超过阈值的 Step2 只添加关闭保护代理，下载卡片只局部修正结果判定，不增加业务职责；后续增加这两类职责前应先拆分完整功能切片。

## S15～S16：退出取消与同步结果反馈

- [x] S15：退出先暂停 TaskRuntime 接收任务，取消所有 owner 范围的活动任务；等待实际后台结束及未提交候选清理，再做最后保存。保存失败、清理失败或等待超时保留窗口，恢复任务入口，不自动重新运行已取消的任务。
- [x] S15：同步本地事务在取消、提交拒绝或异常时调用 rollback；已提交结果保留。写回/FOMOD 清理失败与资源释放异常保留到运行时诊断，不能被取消终态掩盖。独立操作对话框随安全退出关闭。
- [x] S16：ParaTranz 对话框接入真实 OperationResult；部分完成、失败、取消、结果缺失分别展示，保留条目计数和诊断，继续使用重新预检/确认入口。
- [x] 定向回归覆盖取消后的迟到提交、提交失败后的后台清理、backend 启动竞争、跨 owner 关闭、保存失败后恢复、回滚异常、部分结果及结果缺失。
- [x] 关联模块回归、全库静态检查、最终差异复核和本任务临时目录清理。

实施落点：`application/tasks/shutdown.py` 专责可恢复的接收暂停及后台收尾；runtime 保留任务状态/提交仲裁。`ui/shell/task_close.py` 使用既有 ApiWorker 执行取消和等待，避免提交锁阻塞 Qt；`window_lifecycle.py` 接入保存前后检查；`application/sync/executor.py` 补齐事务回滚，功能适配器传递清理失败；`ui/operations/sync_result.py` 单独整理同步回执。

验收边界：取消先赢得仲裁则正式提交被拒绝；正式提交先完成则保留并保存成果。已成功的远端副作用不自动反向重放。既有无协作取消协议的 ApiWorker 仍须等待实际结束，不强杀线程。默认取消等待预算为 5 秒，超时只中止本次退出。旧格式/持久化结构不变，最终 AppRuntime 关闭仍负责永久释放资源。

职责复核：把原 TaskRuntime 的最终 shutdown 和新增的可恢复收尾放入独立模块；运行时从 759 行缩至 702 行，36 个方法仍集中于状态、提交仲裁和事件交付。本轮保留同一锁下的这些接口，继续拆分可能改变取消/提交竞争和订阅顺序；若再次扩展运行时职责，须先完整抽出事件订阅与交付切片，不能继续堆入核心类。同步 executor 的修改是现有事务职责内的局部错误修复，没有添加新业务职责。

验证证据：新增 31 个参数化回归场景。初始沙箱运行被临时文件严格路径解析及本机 HTTP 连接权限阻断；同范围非沙箱运行 502 passed、2 skipped。最终清理诊断先于进度更新的调整，额外跑相关 39 项全部通过；未在这一局部调整后重复全组。Ruff 全库检查、1618 个文件格式检查及本次差异空白检查通过。未跑真实远端、LLM、slow 或安装包测试，未声称全项目全量测试通过。

实际验证命令（省略任务专用缓存、日志和 basetemp 参数）：

```powershell
uv run --no-sync python -m pytest tests/contracts/tasks tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/contracts/paratranz tests/ui/operations tests/ui/test_ai_task_shutdown.py tests/ui/test_inline_edit_shutdown.py tests/ui/test_runtime_task_shutdown.py tests/ui/test_background_gui_operations.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_main_window_shell.py tests/application/fomod tests/integration/publish tests/integration/bootstrap/test_task_runtime_wiring.py tests/smart_assistant/test_runtime_worker_controls.py tests/smart_assistant/test_projection_restore.py -q -m 'not llm and not slow'
uv run --no-sync python -m pytest tests/contracts/tasks/test_close_drain.py tests/contracts/tasks/test_operation_task_adapter.py tests/ui/operations/test_write_execution_feedback.py tests/ui/operations/test_fomod_execution_feedback.py tests/ui/test_runtime_task_shutdown.py -q
uv run --no-sync python -m ruff check src tests
uv run --no-sync python -m ruff format --check src tests
```

## S01～S09 实施前基线与设计约束

- `TaskRuntime.commit_permit()` / `try_commit()` 已使用同一锁仲裁取消和正式 mutation；UI AI 结果提交尚未接入。
- `ProjectRepository.save_if_revision()` 和共享 `mutation_lock` 已存在；activation 和版本目录修改仍有直接 `save()`。
- `ProjectSaveProtocol` 当前 journal 是 Project/Variant 两记录协议，不能直接覆盖活动指针、新增/删除版本和 baseline 登记。必须明确扩展参与记录和恢复语义。
- `RequestExecutionGate` 用工具名和参数摘要寻找未启动步骤；Reflexion 每次 attempt 重入，且 guard 拒绝会在 gate 前返回。
- `ai_entry_key()` 已负责普通 key 与原文限定身份的响应别名；FOMOD 仍用裸 key。
- 文件职责约束：`projects/lifecycle.py` 1035 行、`gui_facade.py` 544 行、`project_save_journal.py` 697 行、`tool_execution_handler.py` 501 行、`fomod/pipeline.py` 620 行。新增完整职责应提取为专用、可测试的模块；这些文件只做必要接线或局部修复。避免把新事务协议继续塞进接近 700 行的保存日志文件。
- S02/S03、S04/S05 分别共享实现边界，应成组验收，避免同文件并行编辑。

## S01：取消仲裁覆盖 AI 候选到正式提交（P1，对应问题 1）

### 实施

1. `TaskRuntimeAiTaskAdapter` 控制绑定覆盖整个 `AiTaskRun`，而非仅绑定当前 worker。worker 结束、等待预览和异步合并阶段仍接受取消；Qt 投影更新通过所属线程信号递送，避免直接跨线程调用 UI。
2. `AiTaskRun._require_current()` 在预览前、预览返回后和异步合并交付时检查运行状态；它是及时反馈，不能代替最终原子仲裁。
3. 在 `TaskSession` 正式 mutation 的唯一入口接入由 adapter 注入的提交端口，使用真实 `commit_permit/try_commit`。合并准备、网络调用和磁盘保存不放进 runtime 锁；锁内只执行必要的权威提交，提交成功后再更新账本与投影。若原提交会同步通知 UI，先明确拆开通知边界与锁顺序。
4. 取消先赢得仲裁则拒绝提交；提交先成功则保留该批已应用结果，取消只丢弃其余候选。重复取消、迟到完成、失效 permit 不重放 mutation。
5. 等待预览或部分失败待重试期间不提前标记 runtime 为已完成。取消后排空正在结束的工作并产生一次真实终态，防止永久 cancelling。

### 文件与验收

- 修改：`ui/tools/ai_translator/task_adapter.py`、`task_run.py`、`task_session.py` 及必要的完成协调接线；优先复用现有 runtime，新增提交端口接口须保持无 PyQt 依赖。
- 已有证据：[翻译闭环测试](../../tests/ui/tools/test_translation_workflow_closure_audit.py) 中 3 个取消失败用例必须转绿，运行中取消对照继续通过。
- 补充：提交前后确定性竞态、重复取消、暂停时申请 permit、旧 attempt 迟到、提交异常和已应用结果取消后保留；检查日志、历史与正式集合一致。
- 依赖：无；S06 与它联合回归。

## S02：版本创建/切换在发布时执行并发校验（P1，对应问题 2）

### 实施

1. prepared candidate 明确携带加载目标时的 persisted Project/Variant revision 和必要的内容摘要；不能用旧活动版本的 revision 代替目标版本前置条件。
2. 版本创建、复制、删除和切换均通过同一个应用命令与持久化发布边界。`_catalog_active()` 保留为快速前置反馈，最终写入必须在共享 mutation lock 内重新校验并条件保存。
3. 复用 repository 的条件写与摘要检查；出现冲突返回结构化 conflict，要求刷新后重新准备，不自动把旧 DTO 合并覆盖新值。
4. 版本创建应先准备 detached candidate，再由 S03 事务发布；不得先写新 Variant，再无条件覆盖 Project。删除路径做对称迁移，虽审查复现集中在创建和切换，也不能留下同一旁路。

### 文件与验收

- 修改：`application/projects/models.py`、`gui_facade.py`、`persistence/v2/lifecycle_transactions.py`；新增专责 `catalog_commands.py` 准备版本目录命令，lifecycle 仅接入既有 prepared-token/activation 边界。
- 已有证据：[持久化闭环测试](../../tests/persistence/v2/test_workflow_closure_audit.py) 中 prepare 后重命名、创建检查后重命名 2 例转绿，已陈旧窗口对照继续通过。
- 补充：同 root 两套服务分别改目录、活动版本、元数据；冲突无新版本残留、无旧状态覆盖、无成功 UI 信号。区分 Project 和 Variant 单独发生变化。
- 依赖：与 S03 使用同一发布合同并联合交付。

## S03：版本操作多记录事务与故障恢复（P1，对应问题 3）

### 实施

1. 在 `persistence/v2/` 新增专责的 `project_activation_journal.py`，承担版本目录/活动引用变更的持久化提交和恢复。已有 `ProjectSaveProtocol` 保持普通保存职责；共享记录校验原语提取至 `project_record_guard.py`，二者复用 CAS/摘要规则。
2. 每个事务列出实际参与项：Project 内版本目录、受影响的 Variant 文件、`active-project.json` 以及需要改变的 baseline 注册。捕获旧内容/原不存在状态、预期修订、目标内容与摘要；稳定 source 数据和未涉及的记录不重写。
3. 在独占的发布边界内检查 S02 前置条件，写 durable journal/staging，再发布数据记录与活动指针，完成校验后记录 committed。只有提交完成才切换内存 active、发事件和报告成功。
4. 提交标记前的失败依据 preimage 恢复旧状态，原不存在的记录只在能证明由该事务创建且未被修改时删除。已 committed 的事务恢复为新状态。恢复须幂等；若发现第三方修改或恢复 I/O 再失败，保留 journal、阻止继续写并返回可操作诊断，不能覆盖后来数据。
5. 启动读取活动项目及下一次写入前恢复遗留事务；将异常回滚与进程在任意记录发布后中断视为同一个协议的两种入口。
6. 确认同 root 写入排他边界：现有锁只覆盖同进程，不能声称提供跨进程原子性。如支持多进程写，复用仓库文件租约模式建立 root 写租约，所有相关写入口参与；否则显式拒绝第二写进程，不能只给新模块加一个局部锁。

### 文件与验收

- 修改：`persistence/v2/lifecycle_transactions.py`、`project_save_journal.py` 必要的公共原语接线、`application/projects/ports.py`/`models.py`、`gui_facade.py`、`bootstrap/persistence.py` 的恢复装配；新增上面的专责协议及测试。
- 已有证据：活动指针 replace 失败的创建/切换 2 个失败用例转绿。
- 补充：各记录 staging/replace、回读、提交标记、清理阶段故障；每个断点后重启恢复；恢复重复运行、回滚失败、外部内容变化、新建/删除版本、空项目、活动指针原不存在。
- 验收结果只能是全部旧状态、全部新状态，或明确阻止写入的可恢复冲突；不得“界面失败但磁盘悄悄换版本”。提交成功后的清理失败单独报告，不能伪称业务未提交。
- 依赖：S02；同一修复批次实现，先冻结发布和恢复合同。

## S04：助手逻辑步骤与 retry attempt 分离（P2，对应问题 4）

### 实施

1. 批次准备时由程序按 dispatch 和步骤序号发放稳定逻辑 step 身份；工具名/参数摘要继续作为校验材料，不能作为重试身份。相同工具和参数在两个步骤出现也必须分别计数。
2. 给执行上下文传递内部 step handle，模型参数不可注入此身份。一个逻辑步骤可以有有界只读 attempts；原始规格保持不可变，修正参数和 attempt 结果另行记录。
3. 每次 attempt 重新验证当前 turn/owner/scope/撤销状态、权限及参数，重试仅限当前已允许的只读、非长任务、无需确认工具。写操作继续按既有 effect/提交回执处理，不因本修复获得自动重试能力。
4. attempt 的暂时失败不提前终结逻辑步骤；重试协调器得到最终结果后调用 S05 的统一结束操作。调整参数只能继续当前允许工具，不得改变项目/会话或扩大捕获范围。
5. 将步骤生命周期规则提取至 `application/assistant_requests/step_execution.py`，工具重试适配提取至 `smart_assistant/step_execution.py`；现有 gate 和 handler 做接线，复用 retry executor，避免扩大工具 handler 职责。

### 文件与验收

- 修改：`smart_assistant/request_execution.py`、`tool_execution_handler.py`、`tools/base.py`、必要的 `reflexion/retry_executor.py` 与 `application/assistant_requests/models.py`/持久化 codec。
- 已有证据：[助手闭环测试](../../tests/smart_assistant/test_workflow_closure_audit.py) 的网络重试、修正参数重试 2 例转绿。
- 补充：连续相同工具同参步骤、多次重试、旧回合重试、重试中取消、恶意 step 身份参数、修改 scope 被拒、写工具不重试；最终回执不重复。
- 依赖：与 S05 共用逻辑步骤合同，联合交付。

## S05：拒绝、跳过和中断步骤也有结束回执（P2，对应问题 5）

### 实施

1. 由逻辑步骤协调器接收最终结果，覆盖护栏拒绝、工具不存在/不可用、异常、重试耗尽和用户停止。不能只在 `gate.after()` 记录结束，也不能把 guard 移到权限生效后才验证。
2. 未执行步骤也使用已授权批次中的稳定 step 身份记录失败/跳过/取消事实；不创建虚假的业务写 effect。终态写入具备幂等性，回执写盘失败可恢复并阻止错误推进。
3. 批次停止时结算尚未开始的步骤，不取消已经成功提交的效果；长任务已有 job/effect 的结束仍由 TaskEventRouter 交付，不能在“提交成功返回 JobRef”时当作实际业务完成。
4. 所有逻辑步骤有最终结果后派生 dispatch 终态，使用现有 reconcile/scheduler 推进；业务失败不等于请求目标已完成。需重试的 item 可重新调度，不能遗留无执行者的 running dispatch。
5. 对历史卡住 dispatch 提供保守 reconcile：仅在无活跃执行者、无未决写 effect 且有足够证据时关闭/标记中断；不把全部历史 running 直接改 completed，也不重放不确定写操作。缺证据时给出明确恢复提示。

### 文件与验收

- 修改：S04 同组文件、`application/assistant_requests/dispatch.py`/恢复入口；以已有 `ExecutionDispatch` 为权威，必要的新字段保持旧记录可读。
- 已有证据：第二步 schema 拒绝后 `request.unsettled` 不再永久为 true，失败项恢复为可处理状态。
- 补充：首步拒绝、全拒绝、最后一步拒绝、批次中途停止、未知工具、终态重复/乱序送达、回执保存失败与重启、混合长任务和只读步骤。
- 依赖：S04 的稳定 step 身份。

## S06：远端术语连接由完整 AI 任务持有（P2，对应问题 6）

### 实施

1. 移除 worker finished 时因 `pending_confirmation` 而关闭 client 的行为。worker 是一次 attempt，连接属于可跨预览、部分应用和失败重试的整个 run。
2. 在真正完成且不可重试、取消并排空使用者、致命终止或 dispose 时幂等释放；关闭窗口若只是隐藏任务，不释放仍在使用的资源。
3. 重试复用冻结的术语依赖与一致性检查。连接损坏时按原配置明确重建或报可恢复错误，不能用 None/空术语静默降级，也不能重置术语基线绕过验证。

### 文件与验收

- 修改：`ui/tools/ai_translator/task_run.py`、必要的 `task_completion.py` 接线（以实际完成协调文件为准）；核验 `source_execution.py` 的依赖传入。
- 已有证据：开启预览后失败重试通过真实术语装配，不再误报术语内容变化；无预览对照通过。
- 补充：连续重试、预览取消、窗口隐藏/销毁、后台使用中取消、真实术语改变仍拒绝、连接 close 恰好一次。
- 依赖：与 S01 协调 run 终态和资源释放时机。

## S07：FOMOD 复用原文限定词条身份（P2，对应问题 7）

### 实施

1. 内部筛选和候选集合使用完整 `EntryKey`；向 AutoTranslator 传输目标标识时复用 `application/translation/entry_alias.py` 的 `ai_entry_key()`。
2. 移除本链路先降级为裸 key 再 `collection.get(key)` 的查找；不修改公开 key/id、不重命名源词条、不按遍历顺序选第一个匹配项。
3. 保持 StagePolicy、TM/迁移优先级、候选提交与 `_write_back` 的现有行为，局部修改 FOMOD adapter，不新建一套翻译流程。

### 文件与验收

- 修改：`fomod/pipeline.py` 的筛选和 AI 适配段；公共 alias 仅在确有缺口时修改。
- 已有证据：[FOMOD 闭环测试](../../tests/application/fomod/test_workflow_closure_audit.py) 的同 key 不同原文插件写回转绿，普通 key 对照通过。
- 补充：跨命名空间、原文空白不同、只翻译选中兄弟条目、hidden/locked 不进入 AI；同 key 同原文仍按现有歧义策略处理。保留真实插件 parse→AI 离线替身→commit→write→reparse 测试。
- 依赖：无；可独立交付。

## S08：FOMOD 装配也纳入任务终态管理（P2，对应问题 8）

### 实施

1. preflight 和无需 run_id 的参数校验在注册前完成。依赖 run_id 的 spec、工作目录、引擎装配放入由 `TaskRuntime.schedule()` 托管的 workload，复用其开始失败、异常和取消收尾。
2. 不通过直接改 `_jobs` 或把失败任务伪装为 cancelled 修补。UI 获得 JobRef 后，装配失败表现为可查询的 failed 任务及结构化诊断；注册前失败仍直接返回输入错误。
3. workload 在装配前后检查 cancellation，配置加载/引擎创建失败不启动 pipeline；只清理本 run 创建的资源。report 不存在时仍能从任务诊断说明失败原因。
4. 调度后端启动失败沿用 schedule 的终态处理；成功、失败、取消只产生一次终态，工作负载索引不遗留活跃引用。

### 文件与验收

- 修改：`application/fomod/runtime.py`；核验 `ui/operations/production.py` 的 engine factory 和任务观察器能呈现异步装配失败。
- 已有证据：引擎装配故障后无非终态任务。当前测试外层 `raises` 是旧同步行为；实施异步装配后改为断言返回 JobRef 的 failed 状态、诊断及无活跃任务，不能为了保留同步异常而放弃 runtime 托管。
- 补充：spec 构造、目录创建、配置读取、engine factory、backend.start 故障，以及装配前/中取消；成功 run_id 一致，未发布输出。
- 依赖：无；GUI 错误反馈与合同测试同步调整。

## 实施顺序与交付批次

1. S01 + S06：同一 AI run 的取消、提交、重试与资源生命周期。
2. S02 + S03：先冻结版本发布事务合同，再切换所有相关调用方，联合验证并发和崩溃恢复。优先级与第 1 批同为 P1。
3. S04 + S05：先定义逻辑步骤/attempt 和最终回执，再迁移 handler/gate；不要分两次留下两套结束规则。
4. S07：小范围身份修复，可在不冲突的独立分支提前完成。
5. S08：初始化托管及 UI 失败反馈。

不以代码存在或旧单元测试通过代替闭环验收；各批要让对应复现转绿，再跑相关原有成功/失败/取消链。

## 验证策略

- 先运行对应新增测试文件：`uv run pytest tests/<对应文件> -q`；S08 的用例按明确的新提交合同同步调整。
- AI：`tests/ui/tools` 对应 runtime/progress/session/async apply/completion/resume 测试、`tests/application/translation`、`tests/ai_translator/post_processor`。
- 项目：`tests/persistence/v2/test_lifecycle_save_transactions.py`、`test_project_provisioning.py`、`tests/application/projects/test_lifecycle.py`、版本 GUI 与重开集成；增加激活 journal 故障恢复专用测试。
- 助手：`tests/smart_assistant/test_request_execution.py`、`test_retry_executor.py`、请求调度/恢复/取消及入口 parity。
- FOMOD：`tests/application/fomod/test_operation_task_entrypoint.py`、`tests/test_fomod_pipeline.py`、`test_fomod_typed_pipeline.py`、`test_fomod_staging_publish.py` 与原文限定 I/O 回归。
- 最终 `uv run ruff check src tests`、`uv run ruff format --check src tests`、差异检查；记录准确命令和结果，清理仅本批创建的临时目录。
- 已更新的受控 HTTP 成功链夹具使用 Responses 结构化输出；保留其路径/schema/业务结果断言，不为新修复退回旧协议。

## 最终验证结果

- 原 17 项审查场景的 13 个失败全部修复，保留 4 个对照；各组进一步补充了并发、故障、恢复和连续重试边界。
- 全项目常规离线命令 `uv run pytest tests -q -m "not llm and not integration and not slow" --ignore=tests/performance -p no:cacheprovider --basetemp .tmp-pilot-closure-full`：5535 passed、7 skipped、22 deselected、3 failed。失败来自旧插件发布断言及 2 条过期 AI 权威写入源码合同，已核验根因并更新。
- 最终代码组合：212 项中 211 passed、1 skipped，包含全部上述合同修正、8 项流程边界以及最终确认/回执故障组合。跳过源于 Windows 缺少符号链接创建权限；未在这些局部修正后重复整套常规集合。准确命令和各分组结果见增量记录。
- `uv run ruff check src tests`、`uv run ruff format --check src tests` 和 `git diff --check` 均通过。检查了 1563 个 Python 文件的格式。
- 本轮测试、缓存和实施前归属基线目录已清理；既有用户改动保留，未 commit/push。
- 未运行真实 LLM/ParaTranz 写入、正式性能基准、重新构建安装包或人工桌面验收；POSIX 租约分支未在 Windows 本机执行。

## 兼容、迁移与风险

- 不改变翻译文件格式、key/id 和用户现有译文；取消后保留此前正式提交的结果。
- 新 activation journal 必须带独立版本和严格校验，启动恢复需同时识别现有 save journal；带未完成新 journal 时不能直接降级到不识别它的旧版本。
- 请求步骤若增加持久化字段，使用默认值和明确 codec 升级；历史无证据状态不可假造成功。自动恢复仅处理证据充分且没有不确定副作用的情况。
- 多锁并用应明确 runtime、项目 mutation、session 序列化和 GUI 回调顺序；不在持锁期间做网络 I/O、长时间合并或等待 Qt 线程。
- 回退以实现切片为单位保留数据和诊断；不能用恢复旧直接写入旁路作为“回退开关”。损坏或外部变化的 journal 保留并阻止写入，交由明确恢复流程处理。

## 实施决策与维护边界

- 本轮缺陷修复不改变产品范围；不需要重新批准取消保留已应用结果、只读有界重试等已有行为。
- S03 通过共享 root OS 租约拒绝跨进程并发写，Windows 已有实际子进程测试；POSIX 分支复用 Session 的 `flock` 实现，本机未执行。
- baseline 注册在 activation committed 标记前参与，异常时精确恢复内存前像；重启从来源描述重建，不把进程内 registry 当作持久化事务文件。
- `gui_facade.py` 从 544 行降至 410 行，目录命令提取为完整职责；独立 activation 协议约 345 行，普通保存协议缩至 684 行。
- `lifecycle.py` 1072 行保留既有状态机，只加入目录命令和 AI mutation guard 的薄接线，避免暴露生命周期锁或复制事件/终态处理。它仍有职责集中风险；下次增加生命周期操作前，必须先整体提取 transition 状态机。`repository.py` 约 595 行仅接入既有读写边界，共享锁和协议主体已独立。
- FOMOD 初始化失败现在通过 JobRef 的 failed 终态和结构化诊断交付；注册前校验仍直接拒绝。公开词条 key/id 和翻译文件格式不变。
- 确认卡片补齐许可释放竞态：释放不阻塞 GUI，锁忙时保留点击并在释放事件后重新验证；回执持久化失败先撤销卡片权限、显示诊断，再清理等待队列，禁止排队批准变成业务执行。
- 完成声明以各 Story 的回归结果和最终检查为准；审查时新增失败测试仅作为验收起点。
