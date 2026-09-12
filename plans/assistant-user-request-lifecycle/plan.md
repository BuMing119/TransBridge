# 用户请求生命周期与长会话上下文

- 状态：已实现（2026-09-12）；离线集成验证通过，真实模型语料评估未执行。
- 日期：2026-09-12
- 需求：[FR30](../../docs/requirements.md#fr30智能助手用户请求生命周期与长会话上下文)
- 架构：[ADR-040](../../docs/adr/040-assistant-user-request-lifecycle.md)
- 前置：[取消与生命周期快照闭环](../assistant-cancellation-lifecycle/plan.md)，已在当前工作区完成。
- 用户已确认：新问题回答结束后继续推进旧请求；需要确认时等待用户。

## 设计阶段进度

- [x] 现状核对：输入、上下文、任务、Session 存储与恢复。
- [x] 产品行为：多问题、补充、换题、自动继续和失败边界。
- [x] 架构契约与迁移方案。
- [x] 实施拆分与设计一致性检查。
- [x] 模型控制协议、完整回答提交及多窗口调度补充复核。

## 目标与非目标

让助手长期记住用户要完成什么，并把每轮回答、工具操作和异步结果归回正确目标。优先保证输入不丢失、结果可追溯、旧工作不误续跑，以及当前问题回答后按序继续旧请求。

本计划包含 8 个可独立验收的 Story，按下列实施进度跟踪。保持桌面模块化单体和既有 TaskRuntime，不引入第三方工作流框架；不把请求调度放入 Qt 控件或 GraphExecutor。不修改既有增量日志。

## 实施进度

- [x] S01：输入、完整历史与持久化迁移。
- [x] S02：请求核心、修订与应用保存边界。
- [x] S03：模型控制协议及多指令路由。
- [x] S04：执行接纳与后台结果归属。
- [x] S05：调度、确认与完整回答提交。
- [x] S06：上下文预算及结果回查。
- [x] S07：恢复、迁移与关闭集成。
- [x] S08：请求清单及综合验证。

## 当前实现

- 完整历史和预算投影分开；20 轮仅为兼容投影，原始工具结果按请求归属回查。
- 输入经后台队列保存后显示接纳；请求、执行与模型轮次分别拥有身份。
- RequestService 统一 Session 写入，schema 为 4；CAS 冲突重载一次归约，未保存本地修改不被丢弃。
- 请求清单接通继续、暂停、取消、澄清、事项调整和未知结果核对。恢复确认生成新调用身份。
- 重启重验审批，未知写入不重放；删除会话先保存停止意图，等待工作收敛。

## 实施 Story

以下记录实现范围。确认、管理 UI、执行范围及 Session 命令重试已按职责拆分；业务执行沿用既有取消与提交屏障。

### S01：输入与完整历史可靠保存

用户价值：连续发送和长对话不丢消息，旧会话不会因上下文裁剪缩水。

- 新增 `src/transbridge/application/assistant_requests/transcript.py`、`ports.py`：消息 ID、顺序、原生工具关联、输入消费位置及 artifact 引用契约。
- 新增 `src/transbridge/persistence/assistant_transcript_store.py`：在已有 persistence 包内提供不可变分段记录、校验和、Session manifest 引用的原子发布适配器。
- 修改 `src/transbridge/application/sessions/models.py`、`src/transbridge/persistence/v2/models.py`、`migration.py`、`schema.py`：采用 ADR 的版本 4 兼容规则，legacy 历史只迁移现存事实。旧 `migrate_v2_to_v3` 目标版本固定为 3，新增独立 3→4 步骤，避免它引用全局版本常量而跳过新迁移。
- 修改 `conversation_manager.py` 与 `panel.py` 的历史读写边界：完整账本为唯一权威，兼容 facade 只供旧调用过渡，禁止反向用裁剪结果覆盖账本。
- 接纳输入先落盘再显示已发送；同时捕获原项目/版本和不可变选择引用，批处理不得跳过待路由消息。失败保留输入草稿，不启动业务工具；发送后切项目不能改变排队消息的执行目标。
- 验收：连续 3 次输入全部可查；超过 20 轮仍保留起始消息；原生 call/result 顺序合法；发送“翻译选中项”后切到另一项目不会重读新选择；崩溃在 segment 写完但 manifest 未提交时，旧快照可读且不会凭孤儿数据启动操作。
- 测试：`tests/application/assistant_requests/test_transcript.py`、`tests/persistence/v2/test_assistant_session_migration.py`，并回归 Session 持久化与 UI 恢复测试。

### S02：可验证的请求、请求项与修订

用户价值：多个目标和未回答问题能够独立跟踪，完成有依据。

- 新增 `application/assistant_requests/models.py`、`reducer.py`、`service.py`：不可变请求、item、revision、等待原因、执行记录及状态归约。
- Session 保存请求快照、消费水位与事件序号，独立应用服务按原 SessionRef 保存；`GuiSessionCommandFacade` 委托同一写入边界，避免双写者。修改 `src/transbridge/persistence/session_lifecycle.py` 和 `src/transbridge/persistence/v2/repository.py`，将 Session 的 revision 比较与保存放入既有 root-shared mutation_lock 的同一临界区，补足跨进程写入租约/拒绝策略。
- 完成条件、停止收敛、重复事件和 expected_revision 校验采用 ADR 契约；请求意图版本与存储 revision 分开。
- 验收：3 项只完成 2 项时请求保持未完成；后台任务 completed-with-partial 不变成请求全成功；一项失败而另一项仍在提交时不提前进入 FAILED；未知副作用阻止所有终态；取消终态不被迟到事件改写；存储冲突重新加载并归约一次。
- 测试：`tests/application/assistant_requests/test_request_reducer.py`、`test_request_repository.py`，使用真实临时持久化适配器覆盖提交与读取。
- 依赖：S01。

### S03：多指令输入路由与纠正

用户价值：追问、补充和新问题不相互覆盖，取消目标不靠猜测。

- 新增 `smart_assistant/request_router.py`、`request_protocol.py` 作为模型及控制解析适配器；应用层新增 `routing.py` 校验 Proposal。修改 `native_tools.py` 提供阶段专用工具表和独立解析入口，路由阶段只接受 `submit_request_routing`。复用现有 LLM 端口，不扩大共享 provider 的 LlmTurn 业务语义，不将控制调用转成业务 steps。
- 修改 `submission_binding.py`、`session_binding.py`：排队启动消费完整 ingress 批次；UI generation 只控制显示和回调，不决定请求是否存在。
- 路由输出 directives[]、稳定 directive_id、原文位置、目标、expected_revision；程序检查 scope、循环依赖、歧义与关系前置条件。逐指令持久化消费状态与临时目标映射；分类失败持久化为待澄清，不丢输入。独立问答追问创建关联请求，不修改原请求验收项；增加原目标要求必须走 AMEND。
- 程序签发 batch、正式 request/directive ID 并先保存合法提案；模型只给局部编号与引用。协议独立版本，未知字段拒绝；同 ID 异内容冲突。普通重试消费已保存提案，澄清只更新未决项，不能通过重新编号再 CREATE。
- 纠正未执行的归属可以重新关联；已执行项通过新修订和显式证据关联处理，不篡改原执行身份。
- 验收：单消息混合三个操作；快速输入合并路由不漏项；不存在目标拒绝；取消 A 的歧义不阻止独立 C 的只读回答；CREATE 已成功而 CANCEL 待澄清时重启或模型更换局部编号均不重复创建；独立追问不使原请求确认失效；路由阶段混入业务工具零执行；伪造 ID/版本拒绝；引用材料中的取消示例不成为命令。
- 测试：`tests/application/assistant_requests/test_routing.py`、`tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`。自然语言固定语料单独报告，通过模型不能替代程序权限校验。
- 依赖：S02。

### S04：执行接纳、版本隔离与结果归属

用户价值：约束修改后旧工作不能冒充新结果，重启不会重复提交未知操作。

- 新增 `application/assistant_requests/admission.py`、`task_events.py`：持久化 effect intent、绑定 request/revision/item/attempt、接纳和正式提交前检查、应用级后台事件路由。
- 修改 `tools/types.py`、`tool_execution_handler.py`、`tools/task_runtime_bridge.py`、`plan_task_runtime.py`：传递类型化 AssistantExecutionRef，经 metadata 桥进入 TaskRuntime；RequestContext 的授权字段含义不变。
- 所有读取、namespace 加载、计划提议和嵌套执行先核对程序生成的 TurnAdmission，写操作再做 Effect admission。非法阶段、旧 epoch 或未路由请求不得走 legacy 回退；模型参数不能指定归属或扩大入场 item 集合。
- 请求原会话的结果落盘不依赖 TaskBinding；TaskBinding 改为纯 UI 投影。TaskRuntime 仍是任务终态权威；同一正式写入只保留一套 commit guard。
- 保留调用方 effect_id 的重复 submit 查询/去重契约；父计划用独立 dispatch_id，每个叶子副作用独立 effect_id。工具接纳先创建不可启动工作，再持久化 JobRef，最后激活。同步写工具同样经过接纳闸门。
- 对现有集合/发布提交守卫补充请求 lease 核验；无提交核对或幂等能力的外部调用遇到结果未知时停止自动重试。
- 验收：后台结束写回非活动会话；修订后旧结果只作证据；submit 前后崩溃、迟到事件、重复回调及取消早于 worker 入口；同一计划两次不同写入不互相去重，同一操作重投复用幂等身份；控制/业务混用、计划内嵌控制调用、过期 turn 和伪造归属被拒绝；无 request ref 的旧后台工作保留 legacy 归属、不自动升级。
- 测试：`tests/smart_assistant/test_request_execution.py`、`tests/integration/bootstrap/test_assistant_request_wiring.py`。
- 依赖：S02、S03。

### S05：有序自动继续与请求级确认

用户价值：新问题优先，旧请求随后自动继续，确认不会串到其他目标。

- 新增 `application/assistant_requests/scheduler.py`；修改 `session_controller.py`，仅暴露前台轮次完成/等待事件，不把请求状态并入其枚举。
- 选择 request 与 ready_item_ids，每次最多一个前台轮次；独立 item 不受其他 item 等待影响，请求级停止/暂停则阻塞全体。共享 TurnLease 覆盖路由和执行调用；多窗口同 Session 显式移交 holder/epoch，旧输出不能提交。轮次入场绑定 request/revision/turn_generation，维持累计轮次上限。
- 修改 `chat_composition.py`、`confirmation_view.py`、`plan_execution_binding.py`：前台焦点切换仅停用当前确认 UI；请求级确认需求保留。修订/取消使确认失效，重启后重新校验再生成新令牌。
- 请求级取消/暂停推进与直接 job 控制分层；新增 `application/assistant_requests/resource_admission.py` 以实际资源键协调跨 Session/窗口写入，纯问答不受此锁阻塞。主动停止生成保存 user_interrupted；任务中心 cancel/pause/resume 经应用入口记录 item 阻塞并核对请求，取消/查询保留紧急入口。新输入技术中断不等同于用户暂停。调度依靠事件唤醒，禁止固定轮询和 UI 阻塞。
- `request_protocol.py` 单独接收 `report_answer_coverage` 与同轮完整文本；修改 `conversation_orchestrator.py`/`ConversationBinding` 的响应接线，调用应用层 `commit_answer` 原子保存文本及覆盖证据，不进入会删除气泡的业务 steps 路径。覆盖声明只更新问答项；provider 截断/失败/旧 epoch 不完成 item，控制回执不触发普通 ReAct 自动续跑。
- 验收：B 回答中 A 完成不抢回答；B 结束后 A 接续一次；一个请求等确认时其他 ready 问题可回答；批准 A 不使自己或同请求无关 B 的确认过期；PAUSE 不被回调解开，RESUME 不解除审批/未知结果阻塞；会话切走记录结果但不自主发起旧会话新轮次。
- 追加验收：同请求上传等确认时独立问答可完成；双窗口同时唤醒只启动一个轮次；跨 Session 同目标写入不能并行绕锁；停止生成后不自动重启，任务中心取消后不自动重试，STOPPING 下 resume 被拒；截断、空回答、只有流式片段、缺覆盖声明、声明执行项成功均不能完成请求。
- 测试：`tests/application/assistant_requests/test_scheduler.py`、`tests/application/assistant_requests/test_request_reducer.py`、`tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`。
- 依赖：S04。

### S06：按请求组装上下文与结果回查

用户价值：长会话保留关键目标及约束，工具输出再大也不挤掉当前问题。

- 新增 `smart_assistant/request_context_assembler.py`、`context_budget.py`；修改 `conversation_orchestrator.py` 每轮组装动态请求信息和工具 schema 预算。
- 新增 `application/assistant_requests/summaries.py`：带来源和覆盖水位的派生摘要；目标、约束、状态仍读取权威请求记录。
- 复用已有 tokenizer 能力；未知模型预算需显式配置或采用有标识的保守上限，不把字符数当精确 token。无需联网探测模型规格。
- 大结果先写可校验 artifact，模型只读范围摘要和引用；新增受 session/request scope 校验的分页回查工具，不将检索到的原文重新作为用户控制指令。
- tools/messages/output/reserve 联合预算；原生调用链按完整组保留或整体移到摘要；必需部分超预算返回 CONTEXT_BUDGET_EXCEEDED，不静默丢约束。
- 验收：第 21 轮仍带最初约束；替换模型窗口后输入保持预算；schema 计入预算；当前输入去重且时序正确，自动续跑不重复旧 user 消息；长结果分页可追溯；跨会话检索拒绝；摘要失败仍能用结构化记录继续或明确阻塞。
- 测试：`tests/smart_assistant/test_request_context_budget.py`、`tests/application/assistant_requests/test_request_repository.py`。
- 依赖：S01、S02；集成验收依赖 S05。

### S07：恢复、迁移与关闭

用户价值：重启后可理解哪里能继续，未知写入不会自动重复，旧数据不会被覆盖丢失。

- 新增 `application/assistant_requests/recovery.py`；修改 Session 恢复协调器与生命周期组合根，采用按原归属恢复的服务。
- 按 intent、JobRef、checkpoint 与提交凭据核对；活跃 job 不存在不表示取消成功。未知副作用进入等待结果核对；显式继续产生新 attempt，不改写旧终态。修改 `src/transbridge/application/tasks/actions.py` 与 `retry.py` 的组合入口，使任务中心恢复/重试带请求关联的任务时也先经过请求 preflight/admission；终态请求创建合法后继，legacy 不自动归入当前请求。
- 补齐版本 4 manifest、历史 segment、artifact 的备份和引用验证；全局 envelope 升级时 Project/Variant 数据仅保留原结构，测试无意外重写。旧程序拒写未来版本，不要求旧版本继续编辑。
- 关闭前停止新接纳，保存输入和请求，按既有 shutdown 策略收敛 jobs；删除请求/会话先停工作并保留 tombstone，防止迟到事件复活记录。
- 验收：每个提交窗口的故障注入；旧格式迁移只保留真实历史，不猜出旧目标；损坏 segment 只读诊断；保存失败无新副作用；重开不会自动运行未知写入或沿用旧确认令牌；任务中心恢复已取消/已修订请求时不能绕过请求检查。
- 测试：`tests/application/assistant_requests/test_reconciliation_deletion.py`、`tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`，扩大 Session/TaskRuntime/持久化回归。
- 依赖：S04～S06。

### S08：用户可见请求清单与综合验收

用户价值：看得见所有未完成问题及自动继续的对象，并能纠正归属。

- 新增 `ui/tools/smart_assistant/request_list_view.py`、`request_binding.py`：紧凑显示当前目标、排队/等待原因、已回答项和结果入口；只展示有效操作。
- 修改 `panel.py`、`chat_composition.py` 接线；ChatWidget 维持薄门面，不继续扩充其职责。保留后台 Task Monitor，显示请求与实际任务的关联。
- 不将内部 ID/lease/schema 暴露为日常操作概念；消息显示必要的请求标题归属，部分结果不显示全完成。
- 验收：FR30.12 全部场景可从界面观察；键盘操作、切换会话、窗口关闭与重开一致；停止生成/暂停推进/取消请求文案与实际控制范围一致；记录应用状态事件与显示结果之间的对应证据。
- 测试：`tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`；受控集成包含真实 Session repository、TaskRuntime、提交屏障和 Qt，LLM 可替身。
- 依赖：S03～S07。

## 实施验证与限制

综合回归命令：

`uv run pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/sessions tests/application/assistant_requests tests/persistence/v2 tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/integration/bootstrap tests/config tests/ui/test_ui_settings_dialog.py -q -m "not llm"`

最终综合回归 1,258 项通过（32.82 秒），包含确认提交顺序、主动停止、窗口预算设置及迟到结果投影。`uv run ruff check src tests`、`uv run ruff format --check src tests` 均通过（1,301 个文件格式合规）；`git -c core.safecrlf=false diff --check` 通过。

`uv run python scripts/benchmark_assistant_requests.py --samples 20` 使用真实临时 repository、10,000 条消息、100 请求及长结果。最终 20 样本：上下文组装 P95 62.209 ms；后台接纳 P95 203.344 ms；历史保存 P95 63.667 ms。重复保存跳过无变化写入；Qt 保存相同历史样本最大心跳间隔 51.439 ms。这不是全部有变化保存、冷启动和磁盘故障的延迟保证。已减少历史反复解码、嵌套重复 JSON 验证及重复冻结；缓存命中仍每次读盘核验摘要。

`assistant_context_window` 默认 32768，可在 AI 服务设置按模型窗口调整。预算包含工具定义、输出预留及协议余量，使用离线保守估计，不声称等于供应商 tokenizer。

真实模型的自然语言归属准确率尚未测量。本次模型调用使用本地替身，未访问外部 API；程序状态测试不替代真实模型语料评估。第三方 Swig 和既有兼容 API 弃用警告仍存在。

## 结构与兼容性复核

请求核心、路由、接纳、事件、恢复、结果核对、删除及上下文分模块实现；RequestBinding 的确认和管理职责已抽离。SessionLifecycleService 约 500 行，新增内容只扩展现有命令和删除前置边界，CAS 保存逻辑已抽到 `application/sessions/commands.py`；后续请求业务仍放在 assistant_requests 包。

Schema 4 保留已有历史，不推断旧请求，不改变翻译项目快照语义；旧程序拒写新版数据。保留先前取消修复、术语界面及已有增量记录，没有提交或推送。设计历史见已有设计增量记录，本文描述当前实现。
