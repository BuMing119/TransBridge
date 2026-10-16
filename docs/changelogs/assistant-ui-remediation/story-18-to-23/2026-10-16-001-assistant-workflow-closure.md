# 智能助手会话、恢复与执行流程闭环

日期：2026-10-16  
Epic：assistant-ui-remediation  
Story：S18～S23  
关联：[计划](../../../../plans/assistant-ui-remediation/plan.md)、[ADR-040](../../../adr/040-assistant-user-request-lifecycle.md)、[ADR-041](../../../adr/041-assistant-context-compaction.md)、[ADR-043](../../../adr/043-assistant-turn-results-and-undo.md)

记录状态：S18～S23 本地实现、完整 1715 项离线回归、最后 315 项助手 UI 复验及六张 Qt 截图核对完成；不包含下文明确列出的外部服务或原生人工验收。

## 增量边界

用户在前轮界面精简后继续询问未闭合的交互流程，并调用 bm-pilot 授权修复。此次保留简洁默认界面，把恢复入口放到真实异常状态中，补齐输入接纳、会话清空、确认启动失败、待处理输入、技能上下文和服务就绪状态。

本轮以开工时 `.tmp-assistant-closure-baseline/files` 的逐文件快照及 `change-inventory.json` 为归属依据，并核对 Git 工作区、暂存区、实际文件和相关计划；不把全部未提交差异归入本轮。清单包含 40 个生产文件、22 个测试文件和 1 个计划文件；另单独核验路由语料 `corpus.json` 的一个预期字段。前轮助手改动、术语工作线、依赖锁及构建配置不属于本次增量。

以下逐文件条目中的“新增/修改”相对于本轮快照，而非相对于 Git HEAD。旧增量记录保持不变；本次新增记录，并最小更新对应计划与两个索引。

## 逐文件生产变化

### 应用层（12 个文件）

- 修改 `src/transbridge/application/assistant_context/projection.py`：`source_digest()` 纳入 `skill_context` 和显式重处理的 `skill_contexts`；`append_context()` 仅从当前请求授权来源读取技能说明，并附在稳定规则之后。避免普通请求继承同会话其他请求的技能，也使同 ID 技能材料改写能够被检测。
- 新增 `src/transbridge/application/assistant_requests/conversation_visibility.py`：`visible_history()` 按持久清空边界过滤展示；`clear_conversation()` 串行保存已接纳历史、累计隐藏消息 ID，并丢弃未处理 ingress/未闭合批次。清空改变可见对话，不删除审计原文、任务执行结果或撤销证据。
- 修改 `src/transbridge/application/assistant_requests/history.py`：`stage_transcript()` 在可见投影应用清空边界；已有消息的空 `tool_calls` 采用一致默认值比较。继续从不可变账本合并完整证据与 metadata，只将最新 system 版本放进当前后端投影，避免旧 UI 保存恢复被清空的气泡或抹去已接纳技能材料。
- 修改 `src/transbridge/application/assistant_requests/input_acceptance.py`：`accept_input()` 新增可选 `skill_context`，在事务前校验并复制，将其纳入命令摘要、ingress 和同条用户 transcript metadata。重复接纳必须保持同一技能内容，失败不修改全局 system。
- 修改 `src/transbridge/application/assistant_requests/management.py`：澄清只接受真正的目标歧义；已放弃批次不能再次处理。显式重处理创建关联新请求时继承原来源的冻结选区及有效技能列表，保留原请求证据；不把任意 FOLLOW_UP 当作技能继承。
- 修改 `src/transbridge/application/assistant_requests/result_review.py`：结果摘要增加保存的进度错误说明，并读取经过归属和摘要校验的任务诊断，去除重复展示。失败原因可以恢复查看，但不充当成功提交证明。
- 新增 `src/transbridge/application/assistant_requests/routing_recovery.py`：区分“选择目标任务”和“重新规划”；兼容读取旧版通用澄清回执。重新规划只复制未接纳的原始 span、附件、技能和冻结范围为新用户输入；放弃/重规划使用幂等命令并保留原诊断，不重放已接纳的同批要求。
- 修改 `src/transbridge/application/assistant_requests/routing.py`：只有 `REQUEST_TARGET_AMBIGUOUS` 生成 `needs_clarification`；结构、版本等验证失败生成 `needs_replan`。避免让用户用选择任务来解决无法通过选择修复的协议错误。
- 修改 `src/transbridge/application/assistant_requests/service.py`：薄委托传递 `skill_context`；`with_transcript()` 复用已有 `history.stage_transcript()`，删除 facade 内重复的账本追加实现；管理输入同步其技能 metadata。统一不可变证据与可见投影写入路径，减少综合服务职责。
- 新增 `src/transbridge/application/assistant_requests/skill_context.py`：校验和复制技能字典；根据当前请求 source/revision ID 提取授权用户来源，兼容单技能及显式重处理列表，同名技能以较后版本为准。投影不从关联父请求或全局 system 推断技能。
- 新增 `src/transbridge/application/assistant_requests/task_diagnostics.py`：保存独立 `task_diagnostic` 证据，核对 session/request/job/run/dispatch/effect 身份并脱敏；`complete=False` 且不满足事项。恢复投影再次核对摘要和原执行归属，确保诊断不会伪造提交凭据。
- 修改 `src/transbridge/application/assistant_requests/task_events.py`：后台失败和独立诊断进入可重试的持久化队列；诊断投递与终态结算分离，晚到说明仍可保存；订阅失败释放已建立订阅，关闭后不再接纳诊断。持久化重试不会重新执行业务工作。

### 助手运行时（8 个文件）

- 修改 `src/transbridge/smart_assistant/context_runtime.py`：两次读取权威决策状态时保留本轮主线程捕获的 `workspace_snapshot`。项目统计变化作为新状态材料追加，原消息、摘要链和冻结 selection 不被重写。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`：客户端创建使用统一就绪投影，并反馈具体配置缺项；请求轮次重新解析配置与凭据，客户端仍按完整配置摘要复用。每轮调用独立上下文模块生成项目快照，向请求准备链透传；无 request binding 的 legacy 路径使用临时材料，不将快照写回原文。
- 修改 `src/transbridge/smart_assistant/plan_task_runtime.py`：runtime 注册、订阅和线程启动纳入失败收尾；`fail_startup()` 结束已接纳但未能启动的工作并保存可解释原因，订阅存在时才关闭。避免启动异常留下假运行态。
- 修改 `src/transbridge/smart_assistant/request_execution.py`：`bind_job()` 先建立可用的任务事件路由，再保存 running 分派，之后绑定具体 job/run。防止订阅失败后仍留下无法结算的执行记录。
- 新增 `src/transbridge/smart_assistant/round_context.py`：仅迁移生成 system 中的旧 workspace 段，保留静态规则和显式手工说明；每轮生成当前项目观察快照。请求路径通过状态序号追加，legacy 路径使用不持久化的 `material_only` 消息，不据此扩大执行范围。
- 新增 `src/transbridge/smart_assistant/service_readiness.py`：提供不可变 `ServiceAvailability`，统一验证模型名、有效 HTTP(S) 地址及凭据，并返回具体缺项说明。凭据解析留在服务边界，只向 UI 返回安全字段；配置读取失败提供设置恢复提示，不声称网络连接已验证。
- 修改 `src/transbridge/smart_assistant/skills/skill_executor.py`：只通过 `send_user_message(..., skill_context=...)` 提交技能；不再替换会话全局 system。接纳返回失败时保留重试机会，成功提示改为“已提交技能请求”，不把异步接纳意图称为执行已完成。
- 修改 `src/transbridge/smart_assistant/tools/task_manager.py`：task bridge 构造及注册失败时撤销 runtime 并释放已创建资源；兼容终态通知中的失败原因转交独立诊断持久化。晚到诊断不改变成功提交证据语义。

### Qt 交互与呈现（20 个文件）

- 修改 `src/transbridge/ui/tools/smart_assistant/chat_composition.py`：为计划执行器接入原请求的失败回调，将原卡反馈与请求 lease/状态收尾连到同一失败路径。
- 修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`：发送前刷新服务可用性并显示具体缺项；公共发送接口透传请求级技能，返回真实接纳结果。legacy 会话无法保存技能范围时明确拒绝并保留预览；清空委托独立模块，避免把新协调职责继续堆入 facade。
- 新增 `src/transbridge/ui/tools/smart_assistant/conversation_clear.py`：先结束当前可见轮次、确认及输入代际，再在后台提交持久清空边界；成功且仍为原会话/代际时清除视图和附件。存储失败保留原对话并提示重试，不能伪报成功；已有后台计划不因清空气泡被误称已取消。
- 修改 `src/transbridge/ui/tools/smart_assistant/conversation_presentation.py`：空态读取缓存的服务可用性及缺项说明，避免定时刷新反复读取配置；待处理提示改为“要求尚未接纳”，不再把全部异常引导为选择任务。
- 修改 `src/transbridge/ui/tools/smart_assistant/onboarding_view.py`：`set_state()` 接收并保存具体服务提示，模型有名字但缺地址或 Key 时仍保留设置入口；缺少宿主导航时继续给出真实手动路径。
- 修改 `src/transbridge/ui/tools/smart_assistant/panel_header.py`：模型名称与可用性分离，单独设置名字不能使服务变为 ready；缺项显示“待配置”并提供无障碍说明/tooltip。真实服务状态通过 `set_service_availability()` 更新。
- 修改 `src/transbridge/ui/tools/smart_assistant/panel.py`：注入缓存服务投影及显式刷新回调；发送前刷新同时更新标题栏，窗口展示/激活仍刷新配置。不新增高频配置轮询。
- 修改 `src/transbridge/ui/tools/smart_assistant/plan_execution_binding.py`：尊重控制器拒绝确认；runtime/engine/启动均进入统一失败收尾。原卡显示脱敏原因、结束观察、释放执行资源并中断当前轮次；异常不会通过空成功结果触发模型自动续跑，清理或展示失败也不保留执行态。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_background.py`：后台完成与失败投递同时检查原 context 和输入代际，切走再切回或清空后，旧回调不能更新新视图。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_binding.py`：接纳逻辑委托 `RequestInputBinding`；停止意图按 session 保存，加载时失效旧输入代际并过滤可见历史。透传技能和 workspace 快照，路由显式重处理/重试上下文操作，不放宽持久暂停和执行资格。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_context_preparation.py`：准备接口新增可选 `workspace_snapshot` 并透传，不在后台读取活的 Qt 项目对象。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_context_status.py`：上下文等待提示指向真实的“重试上下文”按钮，移除不能保证存在的泛化“继续”指导。
- 新增 `src/transbridge/ui/tools/smart_assistant/request_input_binding.py`：每次提交捕获 context/generation/command ID、附件、技能和冻结选区；失败内容留在所属会话，提供恢复文本与同 ID 重试，不覆盖后来编辑的草稿。最终交叉复核补充成功重试去重：已落盘但交付失败、随后切回恢复过的消息，不再重复 `add_user` 或生成气泡，仍正常 `wake()`；此最后边界由 315 项 UI 复验覆盖。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_list_view.py`：新增只在相应状态可见的“重新处理”和“重试上下文”；列表保留真实等待原因，并区分目标歧义和需要重新规划的未接纳输入。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_management_binding.py`：显式重处理先说明全部要求会重做、已完成事项可能再次修改；后台幂等创建关联新请求。上下文重试只清除上下文等待，不解除其他暂停；同步管理输入时跳过清空边界，澄清交互交给独立模块。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_progress_view.py`：失败/取消且无未结算执行时，折叠卡外保留“重新处理”；上下文等待展示原因及专用重试，待确认时可查看原确认操作。未知结果继续优先要求核对，不允许直接重做。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_view_refresh.py`：后台统一投影未接纳输入类型、上下文等待和冻结范围，再更新列表与卡片；UI 文案不自行推测错误类型。
- 新增 `src/transbridge/ui/tools/smart_assistant/routing_recovery_binding.py`：按真实错误类型展示选目标、重规划、放弃选项；弹窗后再次检查会话/关闭状态，仅对原输入同范围任务提供目标选择。后台执行恢复命令，已接纳的兄弟要求不重放。
- 修改 `src/transbridge/ui/tools/smart_assistant/session_binding.py`：恢复可见 messages 与本地 backend 时应用持久清空边界，切换或重开不会重新显示隐藏历史；完整规范账本仍由应用层保留。
- 修改 `src/transbridge/ui/tools/smart_assistant/window_presentation.py`：展示/激活时读取服务可用性而非仅模型名字；`refresh()` 同时更新 header 并返回投影供发送前检查使用。原有最小化和窗口事件行为保留。

## 逐文件测试与语料变化

### 应用层测试（4 个文件）

- 修改 `tests/application/assistant_requests/test_journal.py`：版本冲突的审计预期改为 `needs_replan`，保留 `REQUEST_REVISION_CONFLICT` 原诊断。
- 新增 `tests/application/assistant_requests/test_recovery_actions.py`：覆盖上下文重试与其他暂停隔离、重复点击、重做全部事项的说明和幂等性、原选区继承、过期终态按钮、未知 effect 阻止重做及旧终态继续兼容入口。
- 新增 `tests/application/assistant_requests/test_routing_recovery.py`：覆盖结构错误/旧回执分类、仅重规划未接纳 span、保留已应用要求、重复命令、持久放弃、清空后不可复活、范围变化/写失败以及重处理技能继承。
- 新增 `tests/application/assistant_requests/test_skill_context.py`：覆盖技能接纳快照、重开恢复、命令冲突、请求/关联历史隔离、不可变来源检测、多技能重处理，以及真实 ContextRuntime 保留新项目观察和原冻结选区。

### 运行时测试（6 个文件）

- 修改 `tests/smart_assistant/test_english_prompt_surface.py`：断言技能模型输入继续使用稳定英文名，技能说明改走独立参数，且不再写全局 system。
- 新增 `tests/smart_assistant/test_failure_diagnostics.py`：覆盖失败原因重开可见、诊断写入重试不重放工作、外来 job 拒绝、晚到诊断不改状态及诊断不能替代 commit 证明。
- 新增 `tests/smart_assistant/test_plan_startup_failure.py`：覆盖 runtime/engine/启动失败反馈、资源释放、旧执行回调隔离，以及保存失败或资源清理异常时仍结束当前轮次。
- 修改 `tests/smart_assistant/test_request_evaluation.py`：重命名版本冲突用例并明确断言评估通过、回执为 `needs_replan`、原诊断及原请求保留。
- 新增 `tests/smart_assistant/test_round_context.py`：覆盖跨项目与统计变化、稳定前缀、摘要恢复、旧 workspace 段迁移/手工规则保留，以及 legacy 真实 `start_round()` 接收临时快照而不写回历史。
- 新增 `tests/smart_assistant/test_service_readiness.py`：覆盖模型/地址/Key 缺项、非法地址、配置读取失败、安全投影及 INI 时间戳不变时凭据更新仍影响实际客户端可用性。

### Qt 测试（12 个文件）

- 修改 `tests/ui/tools/smart_assistant/test_attachment_composer.py`：清空附件断言等待持久清空完成，匹配异步成功边界。
- 修改 `tests/ui/tools/smart_assistant/test_chat_bindings.py`：布局夹具增加真实服务可用性投影 mock，无障碍状态断言改为“配置就绪”，不再用模型名推断 ready。
- 修改 `tests/ui/tools/smart_assistant/test_composer_design.py`：仅设置标题模型名时明确断言未就绪，保留窄屏标题和控件几何验证。
- 修改 `tests/ui/tools/smart_assistant/test_conversation_chrome.py`：先显示窗口，再禁用自动推进并等待保存会话恢复/刷新完成，之后才设置测试视图；防止异步初始化覆盖合成状态，未弱化菜单、任务或窄屏断言。
- 修改 `tests/ui/tools/smart_assistant/test_conversation_layout.py`：清空场景在持久边界提交后再刷新并验证布局/状态。最终捕图发现默认 offscreen 字体缺少中文字形，新增仅在设置 `TB_ASSISTANT_CAPTURE_DIR` 且系统字体存在时生效的 `capture_font` fixture，在控件创建前加载并设置微软雅黑、结束后恢复；移除测试体内过晚的字体加载，没有改动生产布局。
- 新增 `tests/ui/tools/smart_assistant/test_recovery_entrypoints.py`：验证终态卡折叠时也能明确重做、上下文等待显示真实原因且重试不解除暂停，以及列表恢复按钮与结构错误措辞可达。
- 修改 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py`：正式面板夹具注入服务可用性，等待初始保存会话恢复/刷新完成，避免合成离线场景与真实初始化竞态。
- 新增 `tests/ui/tools/smart_assistant/test_request_session_isolation.py`：覆盖迟到接纳失败不覆写草稿/执行态、按会话停止、原 ID/附件/技能重试、清空重开/旧保存/写失败/在途输入，以及离线 provider 实际收到请求级技能。最后增加 `restore_session=False/True` 重试变体，验证规范消息已恢复时仍只有一条消息/气泡且正常唤醒；文件由 9 项增至 10 项，已包含在最终 315 项 UI 复验中。
- 新增 `tests/ui/tools/smart_assistant/test_service_readiness_view.py`：模型有名但无 Key 时仍显示待配置、具体原因和设置入口；激活后配置变更更新标题/引导，不泄露凭据。
- 修改 `tests/ui/tools/smart_assistant/test_session_management_actions.py`：fixture 和显示/激活测试改用 `ServiceAvailability`、`_configured_service_availability`、`_read_availability`，保留模型刷新、未配置状态及无障碍提示断言。
- 修改 `tests/ui/tools/smart_assistant/test_skill_launch_design.py`：假 Chat 接收独立技能参数，启动不改全局 system，拒绝时不产生技能残留且保留编辑内容重试；正式请求路径仍要求显式启动。
- 修改 `tests/ui/tools/smart_assistant/test_v2_chat_recovery.py`：V2 恢复夹具增加服务可用性投影，避免从真实本机配置推断离线测试是否可发送。

### 语料、计划与索引

- 修改 `tests/fixtures/assistant_request_routing/corpus.json`：本轮仅将 `stale_revision.expected.status` 从 `needs_clarification` 改为 `needs_replan`。其他语料或此前未提交修改不归入本轮；该字段对应真实路由协议语义变化。
- 修改 `plans/assistant-ui-remediation/plan.md`：新增第四阶段 S18～S23 的交互语义、验收、文件边界、兼容策略及串行 Qt 验证安排；主会话在验证结束后更新完成勾选和最终结果。
- 修改 `docs/changelogs/INDEX.md`：主会话仅增加本记录入口和已确认结果，不改变其他工作线或旧增量。
- 修改 `plans/INDEX.md`：主会话仅更新 assistant-ui-remediation 的当前阶段与已确认的最终证据链接。

## 验证证据

使用仓库现有 Python 3.12.12 / uv 环境，离线运行；Qt 使用 offscreen。以下为主会话已执行并确认的命令和结果，文档子任务未重复运行。

### 已确认的聚焦与修正复验

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --offline --no-sync python -m pytest tests/ui/tools/smart_assistant/test_recovery_entrypoints.py tests/ui/tools/smart_assistant/test_service_readiness_view.py tests/ui/tools/smart_assistant/test_skill_launch_design.py tests/ui/tools/smart_assistant/test_composer_design.py tests/ui/tools/smart_assistant/test_confirmation_feedback.py tests/ui/tools/smart_assistant/test_confirmation_lifecycle.py tests/ui/tools/smart_assistant/test_v2_chat_recovery.py -q -m 'not llm'
uv run --offline --no-sync python -m pytest tests/ui/tools/smart_assistant/test_conversation_chrome.py tests/ui/tools/smart_assistant/test_chat_bindings.py tests/ui/tools/smart_assistant/test_session_management_actions.py -q -m 'not llm'
uv run --offline --no-sync pytest tests/smart_assistant/test_request_evaluation.py -q --tb=short
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
```

- Qt 集成组：**73 passed**，3 条既有 SWIG 弃用警告；对应本轮 `ui-integration.log`。
- 首轮完整回归发现的问题修正后，chrome/布局/模型刷新组合：**21 passed**。
- 路由语料与评估修正复验：**36 passed**。
- 最后修改完成后全库 Ruff check 通过，format --check 确认 **1786 个文件已格式化**；静态结果与下述行为回归分别记录。

### 首次完整回归及失败归因

```powershell
uv run --offline --no-sync python -m pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/assistant_requests tests/application/assistant_context tests/application/sessions tests/persistence/test_assistant_transcript_store.py tests/persistence/test_session_request_storage.py tests/persistence/v2/test_assistant_session_migration.py tests/infra/test_assistant_prompt_cache.py tests/ui/characterization/test_chat_widget_contract.py tests/integration/bootstrap/test_assistant_request_wiring.py -q -m 'not llm'
```

首次完整命令结果为 **1705 passed / 10 failed / 72 warnings**，108.55 秒，对应 `full-qa.log`。保留这次失败记录，不以之后聚焦通过掩盖中间验证过程。

1. 7 项评估失败由同一个 `stale_revision` 旧预期引起：路由已按新合同返回 `needs_replan`，语料仍预期 `needs_clarification`，并影响使用该语料的聚合/CLI 场景。修正唯一语料字段及专门断言后，36 项评估复验通过；没有更改真实 provider 调用或降低路由校验。
2. 2 项 UI 失败来自旧模型名 mock：`test_chat_bindings` 和 `test_session_management_actions` 没有提供新的服务可用性投影，后者还给已不再读取的 `_read_model` 赋值。改成真实公开状态合同并保留 ready/未配置、标题、无障碍和布局断言。
3. 1 项 chrome 测试失败来自初始保存会话异步恢复覆盖手动设置的测试视图。夹具及用例先等待真实初始化结束，再设置合成状态；没有为了让断言通过而改生产布局或取消刷新。

### 最终完整与末轮 UI 复验

相同完整命令最终结果为 **1715 passed / 72 warnings**，198.94 秒，对应 `final-qa.log`。该进程导入的是最后“重试接纳去重”修正前的模块，因此后续继续执行全部助手 UI 与关闭契约复验：

```powershell
uv run --offline --no-sync python -m pytest tests/ui/tools/smart_assistant tests/ui/characterization/test_chat_widget_contract.py -q -m 'not llm'
```

该组结果为 **315 passed / 3 warnings**，101.59 秒，对应 `final-ui-qa.log`，包含最后的会话隔离/幂等重试 10 项。它是与完整回归重叠的复验，不与 1715 相加作为不同测试数量。

随后仅修改捕图字体 fixture；`test_conversation_layout.py` 最终 **12 passed / 3 warnings**，3.78 秒。主会话已目视核对桌面、窄屏、失败、完成、详情及搜索六张截图，中文完整、无裁切，恢复按钮可见且不挤压输入。此阶段没有再改生产代码，不将 offscreen 截图称为原生 Windows 人工验收。

S20 的最后 18 项启动失败/诊断新增测试已包含在完整 1715 项中，不另列未经独立核实的中间组合。S18～S23 无已知未完成实现事项；未执行的人工或外部服务验证不写为通过。

## 兼容、职责与限制

清空采用可选 `assistant_state.conversation_clear` 状态及消息身份边界；原 transcript、任务、结果、诊断和撤销证据仍保留用于审计，不物理删除用户文件。新代码对没有该字段的旧会话按原可见历史读取，无需全量数据迁移；旧二进制不理解该可选状态时可能重新显示历史，因此清空不应被解释为安全擦除或保证旧版本也隐藏历史。失败输入恢复与普通未发送草稿一样仅在当前进程保留。

技能 metadata、独立任务诊断及服务就绪投影均通过现有契约扩展。旧全局手工/技能 system 说明不被猜测重写；新选技能只绑定新接纳输入。原始执行范围、授权、租约和成功提交证明继续由既有应用层核验；失败诊断、项目观察快照和重新规划建议都不能代替这些权限。

职责复核：新增生产模块均不超过 165 行，按接纳、清空、技能投影、路由恢复、诊断、工作区快照及服务就绪分别承担完整职责。本轮 `ChatWidget`（546 行、36 个方法）、`ConversationOrchestrator`（639 行）和 `TaskManager`（631 行）仍触发既有责任复核阈值；仅保留原责任内薄接线及局部失败收尾，新策略已提取。服务 facade 的重复历史写入已删除并复用既有独立模块。风险仍为大 facade 的生命周期耦合；今后增加新的交互/协调职责前，应沿这些边界继续提取并补充生命周期回归，不能继续堆入原类。

本轮验证使用离线客户端和真实 Qt offscreen 控件，不代表真实 LLM 效果、网络可达性或原生 Windows 人工验收。未运行真实模型、远端账户操作、原生 Windows/高 DPI/屏幕阅读器人工验收、安装包发布及无关全库业务测试。本轮授权范围内遗留：无。

已核验临时目录的绝对路径、OWNER 标记及无重解析链接，清理本轮 `.tmp-assistant-closure-baseline` 与其中日志；原有 `.tmp_tests`、`qa-tmp-s03` 保留。六张截图保存在工作区外的授权预览目录 `C:/Users/admin/.codex/visualizations/2026/10/15/01a14098-a6f0-71f3-9022-a8f39b106099/assistant-closure/`。没有提交或推送。
