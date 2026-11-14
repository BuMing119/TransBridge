# S01～S03：LLM 服务配置、推理与缓存兼容修复

- 日期：2026-11-12
- Epic/Story：[llm-provider-compatibility / S01～S03](../../../../plans/llm-provider-compatibility/plan.md)
- 触发：用户要求核验 Claude 给出的代码问题，随后授权 bm-pilot 端到端修复及多 Agent 编码。
- 状态：本地实现、相关回归及静态检查完成；未提交、未发布、未调用真实供应商 API。

## 行为变化

输出上限 0 统一显示为“自动”。Anthropic 翻译默认 8192，助手默认 4096；正数保持，负数/bool 在生产请求边界拒绝。助手预留使用有效预算，保留 OpenAI 已绑定助手原有的 4096 保护，OpenAI 普通自动请求仍省略输出上限。

Anthropic 地址按显式配置、环境变量、官方默认解析；可持久化空地址并撤销设置。旧 OpenAI 官方地址及 SDK 无法正确拼接的 query/fragment 提前报告修正建议，错误不含原地址。取消后重建继续使用已解析地址。

Anthropic 推理独立验证 direct/low，官方端点优先读取 Models API 的显式能力，缺失或非官方端点使用有限非法值/合法值握手。省略参数只表示继承；普通/流式请求保留 schema 与 effort，旧探测缓存因版本提升失效。模型名中出现 thinking/effort、schema 错误、认证/网络等异常不被误判为推理控制拒绝；已输出文本不重放。

官方 Anthropic 助手缓存不再依赖过时型号白名单；5.5 已知门槛为 512，本地估算加余量后为 640，未知型号的长度资格由官方服务判断。非官方端点不猜缓存协议。OpenAI 显式门槛仍为 1024，隐式路径不再受本地 2048 拦截。缓存重试要求确实发送了被拒绝字段，且工具流尚未接收事件；日志记录策略，命中仍依据 usage。

## 文件变更

以下均为本任务新增或修改；未把会话开始前已有的 ParaTranz、AI 结果审阅、release skill 变更计入。

### S01：地址与预算

- 新增 `src/transbridge/infra/llm_request_config.py`：端点解析、官方端点识别、自动预算和非法值校验，供请求层共享。
- 修改 `src/transbridge/infra/llm_client.py`：两协议请求边界解析预算；Anthropic 工厂传地址、取消保留地址；普通/流式推理补丁和 schema 合并；缓存错误分类复用，流式兼容重试限于打开响应阶段；普通响应跳过 thinking block 读取文本。
- 修改 `src/transbridge/config/llm.py`：Anthropic 空地址保存/加载，执行快照保留空地址语义。
- 修改 `src/transbridge/config/repository.py`：集中原子端点更新校验，Anthropic 可空地址，None 仍支持撤销删除。
- 修改 `src/transbridge/application/assistant_requests/config_undo.py`：撤销前置检查复用相同配置合同。
- 修改 `src/transbridge/ai_translator/translator.py`：运行初始化解析输出预算，翻译、术语和内嵌后处理共享有效值。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`：有效端点参与既有客户端复用判定；助手预算委托公共解析函数。
- 修改 `src/transbridge/smart_assistant/context_budget.py`：按有效配置预留，自动未知 OpenAI 上限仍保留 4096，避免预留 0。
- 修改 `src/transbridge/smart_assistant/context_runtime.py`：配置身份包含输出上限，避免异步准备提交过时预算。
- 修改 `src/transbridge/smart_assistant/service_readiness.py`：与客户端一致的地址/预算就绪校验。
- 修改 `src/transbridge/ui/settings/ai_service_page.py`：自动文案、默认值说明、供应商切换的默认地址处理及可操作错误。
- 修改 `src/transbridge/ui/settings/draft.py`：保存草稿前验证最终 Anthropic 地址。
- 修改 `src/transbridge/ui/tools/ai_translator/single_task_view.py`：任务输出上限“自动”及 8192 说明。
- 修改 `src/transbridge/ui/tools/ai_translator/config_presenter.py`：连接失败使用既有安全诊断文本接口。

### S02：推理协议

- 修改 `src/transbridge/infra/llm_reasoning_protocols.py`：官方元数据与有限协议探测，独立 direct/low 机制，普通/流式请求接线，精确参数错误归类；格式错误或畸形成功响应不能证明支持。
- 修改 `src/transbridge/infra/llm_reasoning.py`：探测版本升级、最终生效地址能力键、后台诊断去除异常正文、流式已交付文本不再继承重试。

### S03：缓存策略

- 修改 `src/transbridge/infra/prompt_cache.py`：已知型号/合法日期后缀门槛，未知官方型号委托服务端；OpenAI 隐式 key 不受长度拦截；共享明确缓存参数拒绝判断与策略原因日志。
- 修改 `src/transbridge/infra/assistant_prompt_cache.py`：移除 Anthropic 型号白名单，保留官方端点、开关和拓扑约束，记录策略原因。
- 修改 `src/transbridge/infra/anthropic_tool_calling.py`：公共输出预算、真实 SDK 端点、移除缓存字段重试并保留其他参数，事件开始后不重放。
- 修改 `src/transbridge/infra/openai_tool_calling.py`：仅已发送的缓存字段被明确拒绝时重试；隐式请求只移除缓存参数，显式请求恢复标准消息。

### 回归测试

- 新增 `tests/infra/test_llm_request_config.py`：端点优先级、取消重建、URL 错误、预算值、两协议真实调用边界。
- 新增 `tests/infra/test_anthropic_reasoning.py`：元数据、代理握手、畸形响应、独立控制、schema/effort/fallback、真实 SDK 错误归类。
- 新增 `tests/infra/test_prompt_cache_policy.py`：已知/未知型号、日期后缀、官方/代理、640 边界、隐式短前缀和策略日志。
- 新增 `tests/infra/test_provider_compatibility_integration.py`：锁定 SDK + httpx.MockTransport，联合验证工厂地址、自动预算、schema+effort、真实 SDK 拒绝降级和取消地址稳定；完全离线。
- 修改 `tests/infra/test_anthropic_structured_outputs.py`：6000 输出 token 的模拟批次对比显式 4096 截断与自动完整响应，不把模拟计数当作真实 token 基准。
- 修改 `tests/infra/test_anthropic_tool_calling.py`：自动预算、已收到事件后不重放及精确拒绝。
- 修改 `tests/infra/test_assistant_prompt_cache.py`：官方未知型号可启用，开关/端点/布局与诊断理由。
- 修改 `tests/infra/test_llm_client_prompt_cache.py`：新的 0 自动合同、两种协议流式重试边界、未知模型缓存；tokenizer 不可用测试改为适用本地门槛的显式路径。
- 修改 `tests/infra/test_llm_reasoning.py`：旧缓存版本、最终地址和已交付文本不重放。
- 修改 `tests/infra/test_llm_reasoning_protocols.py`：移除旧省略即关闭断言，验证已证明的 direct/low。
- 修改 `tests/infra/test_openai_tool_calling.py`：未发送字段/无关缓存字样错误不重发，保留隐式重试布局与元数据清理。
- 修改 `tests/config/test_assistant_context_settings.py`：空 Anthropic 地址持久化及执行快照。
- 修改 `tests/application/assistant_requests/test_config_undo.py`：空地址恢复与原子校验一致。
- 修改 `tests/smart_assistant/test_conversation_orchestrator_lifecycle.py`：Anthropic 有效预算与预留一致，OpenAI 自动预留保护。
- 修改 `tests/smart_assistant/test_service_readiness.py`：空地址可用及旧 OpenAI 地址诊断。
- 修改 `tests/ui/test_ai_concurrency_settings.py`：供应商默认地址切换、自定义地址保留和自动文案。

### 文档

- 新增 `plans/llm-provider-compatibility/plan.md`；修改 `plans/INDEX.md` 对应条目。
- 修改 `docs/adr/029-request-scoped-llm-reasoning-control.md`、`plans/llm-reasoning-control/plan.md`：替换已过时的 Anthropic 省略即关闭契约，保留历史实施证据。
- 修改 `docs/adr/031-native-llm-function-calling.md`：明确生成请求的自动输出预算与零输出预热区别。
- 修改 `docs/adr/041-assistant-context-compaction.md`、`plans/assistant-context-compaction/plan.md`：官方协议缓存与实际命中分离。
- 修改 `plans/ai-translation/stories/story-15-prompt-cache-structure.md`、`plans/ai-post-process/stories/story-14-prompt-contract-stage-cache.md`：显式已知门槛、隐式服务端判定和未知型号策略。
- 新增本增量；修改 `docs/changelogs/INDEX.md` 顶部一条记录，保留已有未提交条目。

## 验证

使用仓库既有 Python 3.12.12 / uv 环境，未安装依赖、未修改锁文件。早期常规 `uv run` 访问共享缓存受限；本轮以下离线、不同步、不使用共享缓存的命令均可运行。各组之间独立，额外 Agent 聚焦验证与这些组有重叠，不重复累计。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ai_translator tests/application/translation -q -p no:cacheprovider
# 1097 passed，主会话及 S01 最终复验均通过。

uv run --offline --no-sync --no-cache pytest tests/smart_assistant -m "not llm and not integration" -q -p no:cacheprovider
# 1174 passed；后续新增预算/就绪用例在 S01 聚焦组验证。

uv run --offline --no-sync --no-cache pytest tests/infra tests/config tests/contracts/config tests/application/assistant_requests/test_config_undo.py -q -p no:cacheprovider
# 最终 362 passed。

uv run --offline --no-sync --no-cache pytest tests/ui/test_ai_concurrency_settings.py tests/ui/test_ui_settings_dialog.py tests/ui/test_settings_connection_feedback.py tests/ui/test_settings_responsive_layout.py tests/ui/test_settings_keyboard_navigation.py tests/ui/tools/test_llm_connection_controller.py tests/ui/tools/test_run_config_projection.py tests/ui/tools/test_unified_task_config.py tests/ui/tools/test_ai_launch_config_snapshot.py -q -p no:cacheprovider
# 67 passed；新增供应商切换用例另在 S01 聚焦组通过。

uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
# 均通过，格式检查 2547 个文件。
```

S01 另有 183 项聚焦验证，S02 91 项、S03 91 项联合验证通过。中间失败来自旧的 0 必须报错断言、隐式路径仍检查 tokenizer 的旧断言，以及新增代码格式；均按新行为修正并复验。测试仍有原有 SWIG / TranslationEntryCollection 兼容接口 DeprecationWarning。

责任复核：`llm_client.py` 629 行，较修改前减少，新增配置解析提取为 58 行模块；原有超阈值 facade 仅作局部接线。`llm_reasoning_protocols.py` 478 行、`llm_reasoning.py` 471 行、`prompt_cache.py` 500 行，各保留单一协议/缓存责任。无新增超 700 行模块或综合 Manager。

## 兼容、限制与遗留

- 旧正数配置不迁移，0 变为可工作的自动预算；8192 并不保证任意批次都不截断。未知服务输出容量仍由服务端验证，用户可手动设置。
- 含 OpenAI 默认地址的旧 Anthropic 配置需要在设置中清空或改正；自定义地址不被静默覆盖。带 query/fragment 的地址需改为 SDK 支持的服务根路径。
- 推理能力缓存通过版本失效，无需删除用户文件；未知能力保持继承，不能保证供应商内部绝对无隐藏推理。
- 未运行真实供应商 API、实际缓存收益/翻译质量评估、全仓库所有测试及安装包测试；本任务未提供独立真实服务测试配置，代码改动不涉及打包。
- 本任务没有遗留实现项；没有创建需清理的任务临时目录。会话前已有 `.transbridge/` 及用户工作区变更保留。
