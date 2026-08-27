# 原生 Function Calling 双协议实现与终验

- **日期**：2026-08-27
- **Epic**：`native-function-calling`
- **Story**：Story 01～05（统一契约、OpenAI-compatible、Anthropic、原生编排、调用闭环）
- **Plan**：[plans/native-function-calling/plan.md](../../../../plans/native-function-calling/plan.md)
- **ADR**：[ADR-031](../../../adr/031-native-llm-function-calling.md)

## 目的

将智能助手从“模型输出文本 JSON、应用解析后执行”的伪工具协议迁移为 Provider 原生工具调用，同时支持 OpenAI-compatible Chat Completions 与 Anthropic Messages。保留现有 LLM 抽象层、动态工具发现、权限与确认、ReAct、Plan DAG、会话持久化和非智能助手文本调用接口。

## 文件级变更

### 设计与依赖

- **新增 `docs/adr/031-native-llm-function-calling.md`**：采纳 Provider-neutral 工具轮次、双 Provider 适配、调用 ID 闭环、动态 Schema 暴露和选择性 strict 策略；明确文本 JSON 不再进入执行面。
- **新增 `plans/native-function-calling/plan.md`**：按 Story 01～05 记录目标、验收标准、文件落点、依赖顺序、验证策略与回退条件，并在终验后标记完成。
- **修改 `pyproject.toml`**：仅将本任务关联的 Anthropic 最低版本从 `>=0.20` 提升到 `>=0.85.0`，保证 Messages tools/stream API 可用；文件中的其他依赖差异不归属本增量。
- **修改 `uv.lock`**：同步 Anthropic 最低版本元数据；锁文件中的其他依赖差异不归属本增量。

### Provider-neutral 契约与 Provider 适配

- **新增 `src/transbridge/infra/llm_tool_calling.py`**：增加 `LlmToolDefinition`、`LlmToolCall`、`LlmTurn` 与 `LlmToolProtocolError`；统一序列化格式，校验参数必须为 JSON object，并拒绝不完整及同轮重复调用 ID。
- **新增 `src/transbridge/infra/openai_tool_calling.py`**：将统一工具定义和历史转换为 Chat Completions `tools`/`tool_choice=auto`；聚合流式 `delta.tool_calls[index]`；转换 assistant/tool 历史；拒绝缺失结束原因、截断、结束原因不一致、非法参数与重复 ID。
- **新增 `src/transbridge/infra/anthropic_tool_calling.py`**：将统一工具定义和历史转换为 Messages tools、`tool_use` 与 `tool_result` blocks；保留 Provider content blocks；处理流式事件和 prompt-cache 降级；拒绝截断、stop reason 不一致、非法参数与重复 ID，并避免在已收到工具事件后重试请求。
- **修改 `src/transbridge/infra/llm_client.py`**：在 `LLMClient` 抽象层增加 `chat_stream_with_tools()`；OpenAICompatibleClient 与 AnthropicClient 分别委派到原生适配器；保留现有 `chat()`/`chat_stream()` 行为。
- **修改 `src/transbridge/infra/limited_llm_client.py`**：在共享并发预算装饰器中透传原生工具流，避免装饰后的客户端退化为文本调用。

### 智能助手编排与会话闭环

- **新增 `src/transbridge/smart_assistant/native_tools.py`**：从 ToolRegistry 构建首轮核心工具与动态 namespace 工具面；增加 `propose_plan` 控制工具；把完整原生轮次映射为现有 ReAct/Plan 输入；校验 plan ID、依赖引用、环、混合调用和长任务组合。
- **修改 `src/transbridge/smart_assistant/tool_registry.py`**：为 `ToolSpec` 增加 Provider-neutral JSON Schema 导出；既有业务 Schema 继续作为原生工具定义来源。
- **修改 `src/transbridge/smart_assistant/prompts.py`**：移除要求模型输出 `mode`/`thought`/`steps` 文本 JSON 的协议，改为说明原生工具调用、动态 namespace 路由与 `propose_plan` 使用约束；未把当前工作区中的语言本地化差异归入本增量。
- **修改 `src/transbridge/smart_assistant/chat_worker.py`**：工具轮次改用 `chat_stream_with_tools()` 并向完成回调返回 `LlmTurn`；文本 chunk 仍通过原有流式回调显示。
- **修改 `src/transbridge/smart_assistant/conversation_manager.py`**：持久化 assistant tool calls 与限长 JSON tool results；按调用 ID 幂等闭环；恢复会话时补全悬空调用；仅在成功的 `get_tool_help` 后恢复 namespace；拒绝跨轮复用历史调用 ID。
- **修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`**：每轮提供动态原生工具定义，不再调用旧文本 JSON 解析器；普通 JSON 文本只作为文本；处理 tool-only 空气泡、协议错误、旧 system prompt 迁移，并为 Anthropic 的零配置输出上限提供正数默认值。
- **修改 `src/transbridge/smart_assistant/tool_execution_handler.py`**：执行结果按 `tool_call_id` 写回；help namespace 仅在工具成功后加载；保留既有 Schema、权限、确认与结果安全处理。
- **修改 `src/transbridge/smart_assistant/session_controller.py`**：统一 plan 卡片路由；取消和 abort 时关闭未完成原生调用。

### UI 生命周期与计划执行

- **修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`**：新用户消息开始前先中止旧计划/轮次，保证合成取消结果位于新 user 消息之前。
- **修改 `src/transbridge/ui/tools/smart_assistant/chat_composition.py`**：会话中止同时停止计划绑定和对话控制器，防止旧计划回调污染新会话。
- **修改 `src/transbridge/ui/tools/smart_assistant/confirmation_view.py`**：为计划引擎增加 generation/identity 门禁和 future 异常回调；忽略过期完成事件；向 `propose_plan` 写回聚合成功/失败结果；单工具或批量长任务保持状态机安全。
- **修改 `src/transbridge/ui/tools/smart_assistant/message_list_view.py`**：新 `role=tool` 历史只显示用户可读摘要，不暴露完整内部 JSON。
- **修改 `src/transbridge/ui/tools/smart_assistant/session_binding.py`**：切换或恢复会话时同步中止仍在运行的计划，避免跨会话回调。

### 测试

- **新增 `tests/infra/test_openai_tool_calling.py`**：覆盖请求结构、跨 chunk 参数、并行调用、历史转换、缓存降级、strict 字段兼容、结束原因、截断及重复 ID。
- **新增 `tests/infra/test_anthropic_tool_calling.py`**：覆盖 tools/tool_use/tool_result 转换、内容块与 system cache、流式完成、非文本事件重试门禁、stop reason、截断及重复 ID。
- **修改 `tests/infra/test_limited_llm_client.py`**：验证并发限流装饰器原样转发 `LlmTurn`，不会调用文本接口。
- **修改 `tests/smart_assistant/test_chat_worker.py`**：验证 Worker 传递工具定义、保留文本流并返回统一工具轮次。
- **修改 `tests/smart_assistant/test_conversation_manager.py`**：覆盖原生消息序列化、结果幂等、悬空调用闭合、namespace 恢复、plan result 及跨轮 ID 复用拒绝。
- **修改 `tests/smart_assistant/test_conversation_orchestrator_lifecycle.py`**：覆盖原生轮次路由、JSON-looking 文本不执行、tool-only 轮次和 Anthropic 输出上限。
- **新增 `tests/smart_assistant/test_native_tools.py`**：覆盖动态工具集合、ReAct/Plan 映射、混合调用拒绝、长任务限制和无效 DAG。
- **修改 `tests/smart_assistant/test_tool_execution_security.py`**：覆盖原生工具结果关联以及失败 help 不加载 namespace。
- **修改 `tests/smart_assistant/test_tool_prompt_layering.py`**：验证 prompt 使用原生工具协议、不包含旧文本 JSON 格式，并保留分层工具发现。

## 动机与兼容性

- 原生协议提供 Provider 分配的调用 ID、结构化参数流和结果关联，避免从可见文本推断执行意图。
- OpenAI-compatible 与 Anthropic 的协议差异只存在于适配器，智能助手仍通过统一 `LLMClient` 抽象层调用。
- 未全局强制 Structured Outputs/strict；Provider Schema 用于生成约束，应用内 Schema 校验、权限与用户确认仍是权威安全边界。
- 旧会话文本仍可显示；恢复时未闭合调用会收到合成错误结果。翻译、术语抽取与后处理继续使用原文本 LLM API。

## 实际验证

- `uv run pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/infra/test_llm_client_prompt_cache.py tests/infra/test_openai_tool_calling.py tests/infra/test_anthropic_tool_calling.py tests/infra/test_llm_reasoning.py tests/infra/test_limited_llm_client.py -q`
  - 结果：`602 passed, 14 warnings`。
- 对本次 42 个相关源文件和测试执行 `uv run ruff check ...`。
  - 结果：`All checks passed!`。
- 对同一文件集合执行 `uv run ruff format --check ...`。
  - 结果：`42 files already formatted`。
- `uv lock --check`
  - 结果：成功解析 119 个包，锁文件有效。
- `uv run python -c "...inspect.signature(client.messages.stream)..."`
  - 结果：Anthropic `0.85.0`，Messages stream 签名包含 `tools`。

## 遗留项

- 未执行真实 OpenAI-compatible 或 Anthropic 网络集成测试；当前 Provider 协议测试使用 SDK 形状 mock。
- 仓库级 `uv run ruff check src tests` 与 `uv run ruff format --check src tests` 仍被本任务范围外的既有问题阻塞；格式检查报告 137 个旧文件待格式化。本增量未批量修改这些无关文件。
- Structured Outputs/strict 保留为后续按 Provider 能力选择性启用的增强项，本次不作为统一运行前提。
