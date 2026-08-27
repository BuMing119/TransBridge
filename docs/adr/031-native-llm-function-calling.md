# ADR-031：智能助手采用原生 LLM Function Calling

- **状态**：已采纳（2026-08-27）
- **日期**：2026-08-27
- **关联 ADR**：ADR-005、ADR-008、ADR-011、ADR-029

## 背景与约束

智能助手当前要求模型输出包含 `mode`、`thought` 与 `steps` 的 JSON 文本，再由应用解析并执行。这种伪工具协议无法获得 Provider 对工具调用的原生语义、调用 ID、流式参数聚合与工具结果关联，也会把格式修复和误执行风险留给应用。

项目必须同时支持 OpenAI-compatible Chat Completions 与 Anthropic Messages 协议，并继续复用既有工具注册表、输入校验、权限、用户确认、GraphExecutor 和取消机制。旧的 `LLMClient.chat()` / `chat_stream()` 仍被翻译和后处理工作流使用，不能因智能助手迁移而改变文本调用行为。

OpenAI 与 Anthropic 都支持原生工具调用，但其消息和流式事件格式不同。严格 Schema 输出不是两种协议及所有兼容端点的共同能力：Anthropic 对 strict 工具数量和 Schema 复杂度有限制，OpenAI-compatible 网关也可能只实现基础 tool calling。

## 决策

### 1. 增加 Provider-neutral 工具轮次契约

基础设施层定义统一的工具定义、工具调用和模型轮次：

- 工具定义包含名称、描述、JSON Schema 和可选 strict 意图。
- 工具调用包含 Provider 分配的调用 ID、工具名和已经解析的参数对象。
- 模型轮次包含可见文本、零到多个工具调用和结束原因。
- 内部历史使用可序列化消息：assistant 消息保存 `tool_calls`，工具结果使用 `role=tool` 并保存 `tool_call_id`、工具名、JSON 内容和 UI 摘要。

Provider 适配器负责把统一历史转换为各自协议，并把流式事件还原为统一轮次。OpenAI 适配器聚合 `delta.tool_calls[index]`；Anthropic 适配器保留 `tool_use` block，并把内部工具结果转换为紧随 assistant 的 user `tool_result` block。

### 2. 保留文本 API，新增工具流式 API

`LLMClient.chat()` 与 `chat_stream()` 的签名和返回值保持不变。智能助手改用新的 `chat_stream_with_tools()`；默认实现可退化为纯文本轮次，OpenAICompatibleClient 与 AnthropicClient 提供原生实现。

工具参数 delta 不进入 UI 文本流。工具专用响应允许可见文本为空，完成时直接进入确认或执行流程。

### 3. 原生调用接入现有执行管线

ConversationOrchestrator 不再解析模型输出的 JSON 文本。普通 Provider tool calls 被归一化为现有 ReAct `steps`，其中保留 `tool_call_id`，再交给 SessionController、确认卡和 ToolExecutionHandler。

计划模式通过保留控制工具 `propose_plan` 表达。该工具的参数携带既有 `steps + depends_on`，Orchestrator 将其映射回 PlanCard 与 GraphExecutor。单轮不得混合 `propose_plan` 与普通业务工具；混合时关闭全部调用并作为协议错误处理。

模型返回的普通文本即使看起来像旧 `steps` JSON，也只能作为文本显示，不得执行。

### 4. 每个调用 ID 必须闭环

工具成功、工具失败、未知工具、权限拒绝、用户取消和会话恢复时，都必须为每个未完成调用写入恰好一个工具结果。OpenAI 使用 `tool_call_id`，Anthropic 转换为 `tool_use_id`。

工具结果写回 LLM 的内容使用限长、脱敏、合法 JSON；完整结构化结果仍由现有观察收集器保存。UI 只展示摘要。

### 5. 保留分层工具发现与运行时护栏

首轮只暴露高频核心工具、`get_tool_help` 和 `propose_plan`。调用 `get_tool_help(namespace=...)` 后，会话记录已加载 namespace，后续轮次才向 Provider 暴露对应工具的完整 JSON Schema。这延续 FR11 的 token 优化，并规避 Anthropic strict 工具数量限制。

Provider Schema 是生成约束，不替代应用内 JSON Schema 校验、权限检查、确认授权和执行隔离。

### 6. Structured Outputs 作为能力增强而非统一依赖

原生 Function Calling 默认使用标准 JSON Schema 工具定义，但不要求所有工具统一开启 strict。未来可以基于 Provider 能力与当前动态工具子集有条件启用 strict；不得为了 strict 改变领域工具的可选参数语义，也不得对不支持的兼容端点发送未经验证的字段。

### 7. Anthropic 输出上限使用有效正数

Anthropic Messages 要求正数 `max_tokens`。智能助手优先使用配置中的 `max_output_tokens`；配置为 0 时使用助手专用安全默认值。OpenAI-compatible 保持 0 表示让 Provider 使用默认限制的现有语义。

## 备选方案

### 继续解析文本 JSON

无法获得原生调用 ID 和工具结果闭环，且模型格式偏差会进入执行面，拒绝。

### 向两家 Provider 一次发送全部工具并统一 strict

实现简单，但撤销 FR11 的上下文优化，并会碰到 Anthropic strict 工具数量/Schema 限制和兼容网关差异，拒绝。

### 为 OpenAI 与 Anthropic 维护两套智能助手编排器

会复制状态机、确认、取消和安全逻辑，拒绝；差异只保留在 Provider 适配层。

## 影响、迁移与回退

- 旧会话文本继续可读；旧 JSON assistant 消息不会被重新执行。
- 新会话持久化 Provider-neutral tool call/result 消息，不持久化 Provider SDK 对象。
- 含悬空调用的恢复会话自动补合成取消结果，避免下一轮 Provider 400。
- 回退时可让智能助手重新调用文本 API，但原生消息应先转换为只读摘要；不得直接丢弃未闭合调用。
- 不引入 LangChain 等第三方编排框架，不改变翻译与后处理的文本 LLM 接口。
