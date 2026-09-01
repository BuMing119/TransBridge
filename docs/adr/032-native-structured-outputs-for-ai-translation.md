# ADR-032：AI 翻译采用原生 Structured Outputs

- **状态**：已接受
- **日期**：2026-08-27
- **关联需求**：FR5.15
- **关联 ADR**：ADR-005、ADR-029、ADR-031

## 背景与约束

AI 翻译的正式翻译、专有名词抽取、proofread，以及 strict 后处理的质量检测、修复、润色和裁决都要求模型返回 JSON。当前实现主要依靠 Prompt 约束，再以正则、`json.loads()`、截断修复和文本启发式解析响应。该方式无法由 Provider 保证字段、类型和枚举，格式错误会进入应用重试或保守回退。

项目同时支持 OpenAI-compatible Responses/Chat Completions 与 Anthropic Messages，并已有请求级推理控制、prompt cache、共享并发预算、取消和工作流日志包装器。结构化业务响应使用 Responses API，普通文本和智能助手 function calling 保持既有接口；翻译结果是数据，不是工具行为，不能复用工具调用轮次或调用 ID。

OpenAI Responses API 通过 `text.format.type=json_schema` 提交命名 schema，并以 `response.output_text.delta` 提供流式文本事件。Anthropic Messages 通过 `output_config.format.type=json_schema` 提交 schema。当前锁定的 OpenAI 2.29.0、Anthropic 0.85.0 和 jsonschema 4.26.0 已提供所需接口。

参考：

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

## 决策

### 1. 增加 Provider-neutral 结构化输出指令契约

基础设施层新增与工具调用无关的结构化输出类型：

- `LlmOutputSchema`：稳定名称和 JSON Schema；构造时验证名称、根 object 和 schema 本身。
- `LlmStructuredOutputError`：结构化输出错误基类，并区分 unsupported、refusal、truncated 和 invalid response。
- 内部 structured-output directive：附着在现有 message 字典上的 Provider-neutral 元数据，由最内层 Provider 客户端剥离并转换为原生请求参数。

该指令沿用 `infra/prompt_cache.py` 已建立的内部消息元数据模式。翻译调用仍经过 `chat()` / `chat_stream()`，但只要携带合法 directive，生产 OpenAI-compatible 和 Anthropic 客户端就必须使用原生 Structured Outputs；非法 directive 在联网前失败，不得降级为普通文本请求。`chat_stream_with_tools()` 不读取该指令。

这样现有 `ReasoningScopedLLMClient`、`LimitedLLMClient`、prepared prompt 和 `WorkflowLoggingLLMClient` 可原样透传消息，无需复制一组新方法，减少包装器漏传 schema 或绕过预算的风险。纯文本请求没有 directive 时行为保持不变。

### 2. Provider 适配器独立于主客户端和 function calling

OpenAI 适配器对携带 directive 的请求调用 Responses API，并添加：

```python
text = {
    "format": {
        "type": "json_schema",
        "name": output_schema.name,
        "schema": output_schema.schema,
    },
}
```

Anthropic 适配器为 Messages 请求添加：

```python
output_config = {
    "format": {
        "type": "json_schema",
        "schema": output_schema.schema,
    },
}
```

directive 解析、Provider 参数构造、结束原因分类和本地 schema 复验放入独立模块，避免继续扩大已接近责任阈值的 `infra/llm_client.py`。主客户端只负责在 text chat/stream 请求准备阶段调用该模块，并继续复用锁、请求计数、消息转换和取消机制。Structured Outputs 模块不得依赖 `LlmTurn`、工具定义或工具历史转换。

### 3. 使用稳定 object envelope

所有批量结构化响应使用稳定的根 object，例如：

```json
{
  "results": [
    {"entry_id": "...", "translation": "..."}
  ]
}
```

正式翻译不再使用每批动态 `{entry_id: translation}` schema。动态属性 schema 会随批次变化、降低 Provider 的 schema 编译与缓存复用，并把 entry ID 写入 schema。稳定的 `results` 数组只约束结构；请求集合与响应集合是否一一对应仍由应用校验。

专有名词抽取和原本返回根数组的 strict 批量阶段同样迁移到 `results` envelope。已有 proofread 已使用该 envelope，保持其外部形状。单条 strict 阶段可保留根 object，但使用稳定、完整的 schema。

Schema 只使用 OpenAI 与 Anthropic 共同支持的子集。所有 object 都声明 `additionalProperties: false`，业务必需字段列入 `required`；可选业务值优先使用明确的 nullable 类型或稳定默认值，并控制 union 与嵌套复杂度。

### 4. Provider 约束与领域校验分层

Provider 负责保证 JSON 语法和 schema 形状，基础设施层使用 `Draft202012Validator` 再次验证响应，以识别忽略 `text.format` 的 OpenAI-compatible 网关。

领域层继续负责：

- entry key 的存在、归属、完整性、唯一性和顺序无关匹配；
- 空译文、受保护占位符、术语和重复/回显检测；
- verdict、confidence 和候选提交规则；
- 缺项拆批、保留原译文、人工确认及报告诊断。

满足 schema 不等于业务结果可提交。

### 5. 保留流式与包装器语义

正式翻译继续使用 Provider 的流式 Structured Outputs，并从稳定 envelope 中增量识别已经完整闭合的 result item。只有完整 item 可进入实时预览和候选接收；整个响应完成后仍执行本地 schema 与领域完整性验证。

因取消、连接中断、重复输出或 token 上限产生的部分 JSON不是成功的结构化响应。现有 salvage 只可作为显式恢复输入，用于保存已经完整且通过领域校验的条目，然后对缺失项执行拆批重试。

`LimitedLLMClient`、`ReasoningScopedLLMClient` 与 `WorkflowLoggingLLMClient` 通过现有 message 透传自然保留 schema。prepared 调用仍在预算获准后构建消息。工作流日志可记录内部 schema 指令用于诊断，但 Provider 请求 messages 中不得残留该元数据；日志不得记录 schema 中不存在的凭据或把 schema 当作用户数据。

### 6. 错误和重试契约

- OpenAI Responses 的 refusal content、Anthropic `stop_reason=refusal` 归类为 refusal。
- OpenAI `status=incomplete` 且原因为 `max_output_tokens`、Anthropic `stop_reason=max_tokens` 归类为 truncated。
- 空文本、非 JSON、schema 校验失败或不允许的结束原因归类为 invalid response。
- Provider 明确拒绝 Structured Outputs 参数时归类为 unsupported，并保留原始异常为 cause。
- prompt-cache 被拒绝后的无缓存重试，以及 Anthropic system blocks 到字符串的兼容重试，必须保留相同结构化 schema。

生产 OpenAI-compatible 与 Anthropic 客户端不得静默重试为 prompt-only JSON。上层仍可按既有策略重试相同结构化请求、拆批或保留原译文。

## 关键模块边界

- `infra/llm_structured_outputs.py`：directive 附加/剥离、通用类型、本地解析与 schema 验证、两家 Provider 参数和错误分类。
- `ai_translator/structured_schemas.py`：翻译、抽取和 strict 后处理的稳定业务 schema。
- 应用翻译与后处理调用方：选择 schema、附加内部 directive、执行领域语义校验和恢复，不拼装 Provider 私有字段。

## 备选方案

### 增加 `chat_structured()` / `chat_stream_structured()` 平行方法

接口语义清晰，但必须修改 reasoning、限流、prepared prompt 和日志的每一层包装器；任一层漏实现都可能静默回到文本路径或绕过运行护栏。仓库已经有经过验证的内部 prompt-cache directive 模式，因此拒绝额外复制方法族。

### 使用强制 function call 承载翻译结果

工具调用表示模型请求应用执行行为，翻译结果只是数据；该方案会污染助手工具历史与调用 ID，并把两个独立协议耦合，拒绝。

### 为每批 entry ID 生成动态 object schema

可保留现有映射输出和简单流式正则，但 schema 随批次变化、削弱 Provider schema 编译/缓存复用，也不利于统一其他批量阶段，拒绝。

### Provider 不支持时回退 Prompt JSON

可扩大旧端点兼容性，但会让用户无法判断当前请求是否真正使用原生 Structured Outputs，并恢复本次要消除的格式风险，拒绝。

## 影响与风险

- 现有 Prompt 输出示例、批量 parser 与流式增量 parser 需迁移到稳定 envelope；这是一次内部模型响应契约变更，不改变翻译文件和项目持久化格式。
- OpenAI-compatible 网关即使接受请求，也可能忽略或部分实现 schema；本地 jsonschema 复验会将其暴露为明确失败。
- Structured Outputs 首次编译某个 schema 可能增加延迟；稳定 schema 降低重复编译概率。
- 旧模型或兼容端点不支持原生参数时，相关 AI 工作流将明确失败而非隐式降级。连接检查仍只验证基础聊天，运行期错误需给出可行动诊断。
- Structured Outputs 保证结构，不保证翻译事实正确；术语、占位符、完整性和质量门禁仍不可删除。

## 代码规模责任复审

- `infra/llm_client.py` 集成后约 600 行，超过 500 行复审阈值，但新增 schema 构造、验证与错误分类均已提取到 `llm_structured_outputs.py`；主文件仍只承载两家既有 Provider 客户端及其请求生命周期。为避免在刚完成 function calling 后同时移动公共类，本次不拆文件。若再次增加 Provider、协议方法，或文件接近 700 行，必须先把 OpenAI/Anthropic 客户端拆为独立 Provider 模块并保留兼容导入。
- `quality_gate.py`、`llm_refiner.py`、`polisher.py` 和 `llm_arbiter.py` 是既有单阶段实现；本次把 schema 选择集中在 `prompt_contract.py`，只在各阶段内迁移批量 envelope 与领域 parser，没有加入新的跨阶段职责。`llm_arbiter.py` 已超过 700 行，本次暂不伴随协议迁移做高风险结构重构；下一次向该类增加行为前，必须先拆分 prompt 构造、领域快裁和响应解析责任。

## 迁移与回退

迁移先增加 directive 契约与 Provider 测试，再附加各业务 schema，最后更新 Prompt/解析器。包装器不增加新接口，但必须用回归测试证明预算、推理控制、prepared 构建、日志与取消均未被绕过。每一阶段保持 `chat_stream_with_tools()` 测试通过。

如需回退，可让业务调用点恢复使用 `chat()` / `chat_stream()`；新接口和 schema 模块不改变持久化数据，可独立移除。回退不得把已验证失败的部分结构化响应视为完整成功，也不得改变已经保存的翻译候选。
