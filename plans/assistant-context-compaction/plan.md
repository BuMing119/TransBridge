# 助手稳定上下文与按预算压缩实施计划

- Epic：`assistant-context-compaction`。
- 状态：2026-09-13 S01～S08、S10 本地实现与离线验证完成；S09 离线部分完成，真实模型收益与重复性能样本验收尚未完成。
- 用户授权：按当前计划开始实现并验证；未授权本轮调用付费模型。
- 需求：[FR30.16～FR30.22](../../docs/requirements.md#fr30-stable-context)。
- 架构：[ADR-041](../../docs/adr/041-assistant-context-compaction.md)。
- 证据：[实施验证报告](../../docs/test-reports/assistant-context-compaction.md)。

## 1. 当前阶段

本轮修复（2026-09-13，S10；bm-pilot 授权本地实施，代码及离线验收完成）：

- [x] 定位内部控制回合空回复误报、固定 32K 默认值和字节上界估算。
- [x] S10 实现：有效控制回合不报空回复；容量支持自动解析和手动覆盖；统一离线估算；等待提示列出预算组成。
- [x] S10 验证：控制回合/真正空响应、容量保存和端点隔离、工具规模、摘要及 UI 回归 1,141 项通过；Ruff 检查通过。
- [x] S10 记录：审查 diff，补[实现增量](../../docs/changelogs/assistant-context-compaction/story-10-capacity/2026-09-13-001-capacity-and-control-feedback.md)、[默认 128K 调整及 165 项复验](../../docs/changelogs/assistant-context-compaction/story-10-capacity/2026-09-13-002-default-128k.md)和索引。

S10 落点：`context_budget.py`、独立 `context_capacity.py`/`context_estimation.py`、配置/AI 设置页、
请求接线、`budget_policy.py`/`compaction.py` 和相关测试。现有 orchestrator 超过体量阈值，
只保留局部条件修正和预算工厂调用，容量与估算职责独立实现。

验收与边界：新配置以 0 表示自动容量，仅对核验的官方端点和精确模型 ID 应用规格；未知端点
使用用户指定的默认 128K（131,072），用户保存的正数容量不被自动改写。设置展示实际生效容量及来源，旧 32K 可显式切换自动。
估算优先使用进程内已加载 tokenizer，否则采用带余量的字符估算，明确不是计费 token 或严格上界；
不下载编码、不请求模型、不删除摘要链和必需材料。所有准备入口复用工厂；摘要继承同一估算器。
超限显示消息、工具定义、输出预留、协议余量和窗口，并区分硬超限与摘要空间不足。
依赖顺序：容量/估算 → 接线和提示 → 回归 → 记录。回退可手动配置容量；现有会话格式不变。

- [x] 核对需求、架构和原有请求生命周期。
- [x] 实现实际用量与助手专属缓存适配。
- [x] 实现上下文版本、普通追加、历史回查及迁移。
- [x] 实现独立语义摘要、全部旧段保留、预算与条件发布。
- [x] 接入后台准备、输入优先、取消、等待和设置。
- [x] 完成相关离线回归、静态检查和合成性能测量。
- [ ] S09：真实模型语义/缓存/总成本评估，以及固定硬件上的重复性能样本验收。

最后一项不会以 mock 用量或本地前缀相同率标为通过。普通会话已接入新流程，不将未测收益作为产品承诺。

## 2. 用户可见行为与边界

普通续跑保留已发送模型材料，只追加新来源和实质变化的状态；不按 20 轮滚动淘汰。接近 token 容量时，只总结未被摘要覆盖的旧原文，形成独立新段；S1、S2、S3 原文按序继续发送，重启也恢复全部段。超大首次历史可能分块生成多段，不递归总结旧摘要。

活动输入为：固定规则 → 已有全部摘要 → 本版本状态快照及保留原文 → 后续新消息/状态事件。压缩新版本把当前状态快照放在摘要之后；普通续跑不回写旧状态。旧系统提示版本仍在 transcript 中，模型使用最新固定规则。

UserRequest、目标、计划、checkpoint、TaskRuntime、授权、取消、结果证据继续沿既有权威系统运行。摘要不授予许可、改变计划进度或证明任务完成。完整对话保留，原文可按当前归属分页查询。

新摘要及必需内容无法容纳、模型生成失败或旧材料无法核验时进入上下文等待，不失败整个业务请求。显式继续只清除上下文等待；有效新材料或配置变化可重新接纳。全部旧摘要最终仍受模型窗口限制，系统不会自动删段或改写旧段。

设置提供“自动整理助手上下文”和“助手提示缓存”两个开关；关闭摘要仍检查硬容量，关闭显式缓存不改变模型材料。未知代理能力不发送猜测缓存参数，也不伪造命中率。

## 3. Story 交付与验证

### S01：实际调用用量与可复现基线

状态：已实现、离线验证通过。

落点：`infra/llm_usage.py`、原生工具响应/两个供应商适配器、ChatWorker/AsyncWorker、observability models/collector、`smart_assistant/context_usage.py`。

每个 attempt 的输入、输出、缓存读写、完整性、耗时和用途独立记录。零与未知分开；OpenAI 缓存是输入子集，Anthropic 普通输入与缓存读写按累计快照归一化。SDK 隐式重试在助手原生调用中关闭，明确参数不支持时最多一次兼容降级，两个 attempt 均记录。

捕获的 usage 回调绑定原会话/轮次；取消、过期响应、切换会话、UI 桥关闭不把消费记到新会话。独立诊断账本不复活已删除业务 Session。旧客户端无统计能力时报告 unknown，旧估算回调保留兼容且不重复累计。

证据：`test_llm_usage.py`、两个 adapter 测试、`test_context_usage.py`、`test_observability.py`。合成旧策略夹具位于 `tests/fixtures/assistant_context_compaction/legacy_usage_baseline.json`。

### S02：可恢复的请求上下文版本

状态：已实现、离线验证通过。

落点：`application/assistant_context/models.py`、`ports.py`、`persistence/assistant_context_store.py`、`admission.py`。

ContextHead 是 assistant_state 中带 schema、epoch、revision 和 artifact 的小指针；ContextEpoch 保存冻结材料、摘要链和来源摘要。每段摘要单独存不可变附件。普通追加写 append artifact 和父引用，不重复保存全量消息；新版本引用原摘要附件。发布把引用纳入原 transcript manifest 的备份/清理可达图。

验证：`test_context_store.py`、`test_activation.py`、`test_context_review.py`，含 50 次追加、重开、双候选竞争、孤儿不激活、缺失/损坏附件及未知版本拒绝。

### S03：普通续跑稳定追加与状态同步

状态：已实现、离线验证通过。

落点：`projection.py`、`state_projection.py`、`smart_assistant/context_runtime.py`、RequestModelInput 与准备薄接线。

只为新来源生成模型投影；经不可变 transcript 完整性验证的旧来源复用已存摘要值。归属仍按当前权限检查，不能因为已有引用而绕过纠正。状态从权威请求构建且仅在实质变化时追加，避免元组/JSON 列表差异造成伪变化。FOLLOW_UP 引用明确父请求的原始意图和最近公共解释，不复制许可。

同步兼容入口保留，但超出容量时明确拒绝，不退回滚动裁剪。独立执行上下文与路由材料分离。

验证：`test_context_store.py`、`test_context_review.py`、`test_migration.py`、完整 Qt 请求生命周期及旧请求 reducer/scheduler/graph 回归。

### S04：稳定工具结果和受限历史回查

状态：已实现、离线验证通过。

落点：`projection.py`、`history_queries.py`、request_protocol/native_tools、`request_history_binding.py` 与 RequestBinding 薄分派。

大业务工具结果冻结为保留状态/错误/身份的结构化引用；原结果仍可回查。所有控制工具回执保持完整，包括合法的 8000 字查询页，避免“读取页面又被变成摘要引用”的循环。工具调用和结果按完整组处理。

新增 read_request_history，只接受当前 admission 的请求与 source ID，不接受路径；后台分页查询在 GUI 交付前再次核验归属和 lease。查询材料不变成新用户指令。

验证：历史查询、独立 UI 查询、控制协议、现有 read_result，以及大查询页不再摘要化的回归。

<a id="s05"></a>

### S05：按预算生成可验证摘要候选

状态：已实现、离线结构/协议验收通过；真实语义质量待 S09。

落点：`budget_policy.py`、`compaction.py`、`context_summary.py`；[详设](stories/story-05-budgeted-compaction.md)。

默认 H=0.80B、L=0.45B，新段预算 min(2048, 0.15B)，包含包装和来源开销。旧摘要全链、最新用户输入、当前状态及协议边界被保留。摘要用独立客户端、无工具的四字段 JSON 合同，模型不生成权威状态。

正常最多一次生成加一次结构修复；超窗首次整理最多三次生成/修复调用，未完成的已验证分块保存为未发布候选，显式继续后再核验。供应商明确参数降级属于单独记账的 wire attempt，不能把这些额外消费漏算为一次。候选不能作为活动 head 发送。

验收：未达 H 不调用摘要；旧段 ID/正文/顺序跨压缩及重启不变，新段只覆盖新来源；全链超限零自动删改；当前状态精确保留；格式失败和取消不会无限调用。见 budget/compaction/context_summary/long_sessions 测试。

<a id="s06"></a>

### S06：并发、取消与原子激活闭环

状态：已实现、离线验证通过。

落点：`admission.py`、ContextRuntime、`request_context_preparation.py`；[详设](stories/story-06-atomic-activation.md)。

后台生成不持 Session 事务锁。stage 后发布校验 expected head、请求 revision/lease/scope/待决状态、来源及完整旧摘要链；CAS 重试不调用模型。生成期间允许重新读取尾部证据并合并；若后续仍竞争或容量不足则保留进度等待。

未发布分块额外绑定原 request revision、expected head 和归属摘要。AMEND/配置/来源变化后不复用过期候选。分派点再次比较配置，不能拿旧窗口材料发送给新模型。

上下文准备使用独立队列；输入持久化不排在摘要网络调用之后。取消立即置位并后台关闭独立摘要连接。等待状态与业务失败分开，重试不清除用户暂停或待确认。

验证：activation、context_review、完整 Qt lifecycle，包括暂停/资源等待/取消/关闭、独立输入队列、过期候选和双窗口竞争。

### S07：供应商最终消息与缓存布局

状态：已实现、离线协议验收通过；真实命中与收益待 S09。

落点：`infra/assistant_prompt_cache.py`、prompt_cache、两个 adapter、ContextRuntime 末端装饰。

专属 assistant profile 不放宽翻译拓扑。namespace 来自会话、请求和稳定配置，不含 turn/head/时间戳/增长历史摘要。动态状态是普通材料，不被 Anthropic system 提取。

已核验官方端点/模型才开启明确缓存参数；Anthropic 助手使用顶层自动缓存且不把新 user 合并进旧 tool_result；OpenAI 保留消息与稳定 key。元数据在供应商 wire 层剥离，不进入原始对话存储。

验证：`test_assistant_prompt_cache.py`、两供应商测试及全部既有翻译缓存测试。

### S08：旧会话迁移、恢复和用户反馈

状态：已实现、离线验证通过。

落点：`migration.py`、context store/runtime、原 attachment cleanup/archival、LLMConfig、AI service settings、`request_context_status.py`。

旧摘录校验格式、来源和当前权限后原样导入 legacy-excerpt，覆盖集合为空；历史 revision 不需要与当前相同，不再通过重新运行旧滚动算法判断有效性。未知/失去权限/丢失来源保持原文并报告。归档不再删除旧摘要。损坏的摘要不会重新生成不同正文冒充原段。

助手已移除逐轮 refresh 接线。关闭缓存和暂停自动整理的配置可读写、默认开启；失败/容量不足通过现有请求继续入口重试。

验证：migration、context_review、Session migration、attachment cleanup/CLI、归档恢复、设置与请求生命周期测试。

### S09：长任务正确性、性能与真实收益验收

状态：离线功能及单次端到端测量完成；重复性能样本和真实模型评估未完成。

已执行：相关 1232 项测试全部通过；最终迁移/固定规则修订后另有 40 项聚焦复验通过；UI 列表后台刷新及回答提交顺序复验 185 项通过，数字有重叠。三次压缩+真实附件重开后的最终模型材料保留全部原摘要；超大首次整理可在同次接纳产生多段。

`scripts/evaluate_assistant_context.py --messages 10000` 提供纯合成离线基准。此次旧选材约 674ms、新首次投影约 557ms、普通追加约 228ms；追加附件 732 字节、约 6.6ms，旧前缀完全保留。该脚本仅覆盖投影/附件，不代表供应商费用下降。另用真实 Qt/Session 和 fake 模型测量一万条消息：整轮约 1235ms、最大心跳间隔约 188ms，业务调用 1 次、摘要 0 次。请求列表读取已移到后台并合并刷新；回答接纳保持落盘与 GUI 回执的连续顺序，单次同步落盘约 140ms。样本不构成 P95 验收。

尚未执行：同一模型下至少 12 个标注长任务、每策略至少三次的冷/热/压缩后缓存和语义保真评估；固定硬件多样本 UI/请求持久化 P95 预算。真实模型部分需要授权和服务配置后执行，未知或无支持字段记录 not_run/unsupported。

## 4. 实际接口与迁移边界

没有新增数据库或修改外层 Session schema；新增上下文子文档为 schema_version=1，未知版本拒绝读取/清理。旧程序与旧清理器不认识新 artifact，回退应使用升级前的独立完整备份。

已超过责任阈值的 llm_client 和 conversation_orchestrator 仅增加参数转发、回调接线和分派检查；新策略在独立模块。RequestBinding 仅增加查询/等待委托并提取状态构造、回答接纳及列表刷新，不新增第二套控制器。

旧确定性摘要生成和 RequestContextAssembler 仍供兼容调用与旧基线测试使用，不再决定生产请求的历史窗口。迁移依靠单独 import_legacy_summary，未长期双写两套策略。

## 5. 验证命令与记录

完整命令和运行结果见[验证报告](../../docs/test-reports/assistant-context-compaction.md)。使用现有 uv 环境、--offline --no-sync --no-cache；沙箱内 Python 无法启动，获准在沙箱外运行，未安装依赖或修改锁文件。

规划历史：2026-09-12 从 `9d7afe2` 开始；最初只完成 8 个 Markdown 文件与 26 个相关链接检查。随后用户明确授权实现，本页以实际交付状态替换原待开发清单。尚未完成的联机/重复性能样本验收保持未完成，不把文档、mock 或静态检查当作模型质量证明。
