# 原生 Function Calling（native-function-calling）

**状态**：已完成
**日期**：2026-08-27
**对应需求**：智能助手从文本 JSON 工具协议迁移到 OpenAI-compatible / Anthropic 原生工具调用
**架构决策**：[ADR-031](../../docs/adr/031-native-llm-function-calling.md)

## 目标

- 两种 Provider 协议都能发送原生工具定义、聚合流式工具调用并回传关联结果。
- 智能助手不再从普通文本解析 `steps` 并执行。
- 保留现有确认卡、自动模式、权限与输入护栏、ReAct 循环和 Plan DAG。
- 保留 FR11 分层工具发现，避免全量工具 Schema 常驻每轮请求。
- 保持 `LLMClient.chat/chat_stream` 和非智能助手调用方兼容。

## 非目标

- 不引入新的 Agent 框架。
- 不把 Structured Outputs/strict 作为所有 Provider 的硬要求。
- 不修改工具业务实现或放宽运行时安全校验。
- 不迁移翻译、术语抽取和后处理的纯文本 LLM 调用。

## 当前实现事实与约束

- `prompts.py` 要求模型输出 JSON，`ConversationOrchestrator` 通过 `PromptBuilder.parse_hybrid_response()` 解析。
- `LLMClient` 只有文本返回接口；OpenAI 与 Anthropic 均未发送 `tools`。
- `ConversationManager.add_observation()` 把结果存为普通 user 文本，无法表达 Provider 调用 ID。
- ToolSpec 已持有规范 JSON Schema；ToolExecutionHandler 已有输入校验、权限和确认闭环，应继续作为权威执行边界。
- Anthropic 智能助手当前传入 `max_tokens=0`，必须在迁移中修复。

## Story 01：统一工具轮次与消息契约

**验收标准**：
- [x] 定义可序列化的工具定义、工具调用和模型轮次类型。
- [x] 工具参数必须解析为对象；非法 JSON 产生明确协议错误，不进入执行器。
- [x] ConversationManager 能保存 assistant tool calls 和限长 JSON tool results。
- [x] 保存/加载后调用 ID 不丢失；悬空调用会被合成取消结果闭合。

**文件落点**：`src/transbridge/infra/llm_tool_calling.py`、`conversation_manager.py`、对应测试。

## Story 02：OpenAI-compatible 原生适配

**验收标准**：
- [x] 请求使用 Chat Completions `tools` 与 `tool_choice=auto`。
- [x] 流式文本继续回调，`delta.tool_calls[index]` 的 ID、名称和参数可跨 chunk 聚合。
- [x] assistant/tool 内部历史正确转换为 OpenAI 消息。
- [x] 纯文本旧接口与 prompt cache 降级行为保持不变。

**文件落点**：`src/transbridge/infra/openai_tool_calling.py`、`llm_client.py`、`tests/infra/test_openai_tool_calling.py`。

## Story 03：Anthropic 原生适配

**验收标准**：
- [x] 请求使用 Messages `tools`，system 与普通消息转换保持现有缓存语义。
- [x] text/tool_use 内容块转换为统一轮次，多个工具调用及参数分片不丢失。
- [x] assistant tool_use 后的内部 tool 消息合并为 user tool_result blocks，结果先于同条用户文本。
- [x] 智能助手对 Anthropic 总是传入正数输出上限。

**文件落点**：`src/transbridge/infra/anthropic_tool_calling.py`、`llm_client.py`、`tests/infra/test_anthropic_tool_calling.py`。

## Story 04：工具目录、Prompt 与原生编排迁移

**验收标准**：
- [x] ToolRegistry 能导出 Provider-neutral 工具定义并按 namespace 选择。
- [x] 核心工具、`get_tool_help` 与 `propose_plan` 首轮可用；help 调用后加载 namespace。
- [x] Prompt 不再要求输出 JSON/thought；普通 JSON 文本绝不触发工具执行。
- [x] ChatWorker 返回统一模型轮次；Orchestrator 归一化为现有 SessionController 输入。
- [x] tool-only 响应不留下空白气泡。

**文件落点**：`tool_registry.py`、`native_tools.py`、`prompts.py`、`chat_worker.py`、`conversation_orchestrator.py`、对应测试。

## Story 05：调用闭环、确认、取消与 Plan 兼容

**验收标准**：
- [x] ToolExecutionHandler 为每个业务 tool call 写入一个关联结果。
- [x] 未知工具、权限拒绝、用户忽略/取消和 abort 都关闭悬空调用。
- [x] `propose_plan` 继续使用 PlanCard 与 GraphExecutor；计划完成或取消后关闭 plan call。
- [x] 旧会话仍可显示，新的 role=tool 消息只展示摘要。

**文件落点**：`tool_execution_handler.py`、`session_controller.py`、`src/transbridge/ui/tools/smart_assistant/confirmation_view.py`、`message_list_view.py`、`session_binding.py`、对应测试。

## 依赖顺序

Story 01 是公共契约；Story 02 与 Story 03 可并行；Story 04 依赖 01-03；Story 05 依赖 01 与 04。Provider 测试不访问真实网络。

## 验证策略

- Provider 契约测试：单工具、并行工具、分片参数、纯文本、工具结果历史和错误参数。
- 智能助手测试：Worker 流式、原生轮次归一化、自动执行、确认/拒绝、plan、取消、保存恢复。
- 安全回归：工具 Schema、权限和结果脱敏/限长。
- 最终运行相关 pytest，以及 `uv run ruff check src tests`、`uv run ruff format --check src tests`。

## 风险与回退

- 兼容端点可能声明 OpenAI 协议但不实现 tools：保留文本 API，原生助手请求失败时给出明确 Provider 能力错误，不回退到可执行文本 JSON。
- strict 能力差异：首版不把 strict 作为执行前提，继续依赖应用内权威校验。
- 分层加载会增加一次 help 轮次：核心工具预加载，多 namespace help 支持一次加载；必要时可扩大核心集合，但不默认全量。
- Provider 调用已产生而用户取消：写入合成 error result 后再允许下一轮，避免非法历史。

## 明确假设

- OpenAI SDK 支持 Chat Completions tools；Anthropic 最低版本已提升为 0.85.0，以保证 Messages tools/stream API 可用。
- 工具名在 ToolRegistry 中全局唯一；既有重复注册检查继续生效。
- UI 会话持久化接受消息字典新增字段，不需要数据库迁移。

## 完成验证

- 相关智能助手、UI 与 Provider 回归集：602 passed。
- 本次涉及文件：`ruff check` 与 `ruff format --check` 通过。
- `uv lock --check` 通过。
- 仓库级 Ruff 仍有本任务外的既有问题；未批量修改无关文件。
