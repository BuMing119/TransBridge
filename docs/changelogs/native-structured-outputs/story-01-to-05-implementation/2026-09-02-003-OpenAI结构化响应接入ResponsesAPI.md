# Story 01～05：OpenAI 结构化响应接入 Responses API

- 日期：2026-09-02
- 状态：已完成，相关 QA 通过
- Epic：`native-structured-outputs`
- Story：`story-01-to-05-implementation`
- 关联：[实施计划](../../../../plans/native-structured-outputs/plan.md) · [ADR-032](../../../adr/032-native-structured-outputs-for-ai-translation.md) · [需求 FR5.15](../../../requirements.md) · [前序增量](2026-08-28-002-提示词输出Schema去重.md)

## 目标与边界

修复 OpenAI-compatible 结构化业务请求已经携带 `LlmOutputSchema` directive、但 Provider 适配器仍把它映射到 Chat Completions `response_format.type=json_schema` 的漏接问题。实际 DeepSeek `deepseek-v4-flash` 运行返回 HTTP 400：`This response_format type is unavailable now`，导致翻译和 proofread 结构化调用失败。

本增量仅让 `OpenAICompatibleClient` 在检测到现有结构化 directive 时使用 Responses API。普通文本 chat/stream、原生 function calling、Anthropic Messages、业务 Schema、翻译/校对调用点和独立 HTTP Port 均不迁移，也不增加 Chat Completions 回退。

## 变更明细

### Provider 结构化协议适配

- `src/transbridge/infra/llm_client.py`
  - 操作：修改。
  - 变更：结构化非流式请求改用 `client.responses.create()`，传递 `input`、`text.format`、`max_output_tokens`、`store=false`、reasoning 和既有缓存参数；结构化流式请求消费 `response.output_text.delta`，并读取 `response.completed`、`response.incomplete` 或 `response.failed` 的终态响应。
  - 变更：缓存参数被拒绝时只移除缓存参数并重试相同 Responses 请求，不回退 Chat Completions；没有 directive 的普通 chat/stream 继续使用原路径。
  - 原因：业务侧结构化契约已经正确接线，故障点仅位于 OpenAI-compatible Provider 的最终协议映射。
- `src/transbridge/infra/llm_structured_outputs.py`
  - 操作：修改。
  - 变更：新增 Responses API `text.format` 构造器和终态分类；`max_output_tokens` 截断归类为 truncated，refusal、failed、content filter 和缺失终态继续形成可诊断错误；unsupported 识别补充 `text.format`、`/responses` 和 `unavailable` 信号。
  - 原因：集中维护 Provider 私有结构和错误语义，同时保留本地 Draft 2020-12 Schema 复验。

### 协议合同同步

- `docs/requirements.md`
  - 操作：修改。
  - 变更：FR5.15、FR5.15.3 和 FR5.15.6 将 OpenAI-compatible 结构化协议改为 Responses API `text.format.type=json_schema`，并明确不得回退 Chat Completions。
  - 原因：消除需求合同与生产实现之间的旧协议矛盾。
- `docs/adr/032-native-structured-outputs-for-ai-translation.md`
  - 操作：修改。
  - 变更：OpenAI 决策改为 directive 请求调用 Responses API；同步请求示例、流式事件、终态和本地复验描述。Anthropic 继续使用 Messages `output_config.format`。
  - 原因：更新本 Epic 的权威 Provider 边界。
- `plans/native-structured-outputs/plan.md`
  - 操作：修改。
  - 变更：Story 02 的验收和实施步骤改为 Responses API 请求及语义流事件；非目标明确普通文本和 function calling 不迁移。
  - 原因：让已完成计划反映本次纠正后的真实实现。

### 防回归测试

- `tests/infra/test_openai_structured_outputs.py`
  - 操作：修改。
  - 变更：fake SDK 断言从 `chat.completions.create(response_format=...)` 更新为 `responses.create(text.format=...)`；覆盖非流式、流式 delta、终态、refusal、截断、无效 JSON、缓存重试、reasoning、token 上限、unsupported 分类和 active request 计数。
  - 变更：显式断言结构化请求不会调用 Chat Completions；普通文本请求仍保持原行为。
  - 原因：让旧实现失败、新接线通过，并锁定“不做双协议兼容”的用户要求。

## Claude Provider 核验

Anthropic 生产链已经在普通与流式 Messages 请求中提交 `output_config.format.type=json_schema`，锁定的 Anthropic SDK 0.85.0 也在 `messages.create()` 与 `messages.stream()` 签名中支持该字段。Claude Provider 不存在本次 OpenAI-compatible 的漏接问题，因此没有修改 Anthropic 生产代码或测试。

## 实际验证

### OpenAI-compatible 结构化回归

执行：

```text
python -m pytest tests/infra tests/application/translation/test_proofread_stage.py tests/application/translation/test_postprocess_structured_outputs.py tests/ai_translator/test_structured_output_contracts.py tests/ai_translator/test_reasoning_routing.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-pytest-responses-regression
```

结果：`223 passed`。

最终格式化后复验：

```text
python -m pytest tests/infra/test_openai_structured_outputs.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-pytest-responses-final
```

结果：`14 passed`。

### Claude 结构化链路核验

执行：

```text
python -m pytest tests/infra/test_anthropic_structured_outputs.py tests/infra/test_llm_structured_outputs.py tests/application/translation/test_proofread_stage.py tests/ai_translator/test_structured_output_contracts.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-pytest-anthropic-audit
```

结果：`94 passed`。

### 静态检查与格式检查

执行：

```text
.venv\Scripts\ruff.exe check src/transbridge/infra/llm_client.py src/transbridge/infra/llm_structured_outputs.py tests/infra/test_openai_structured_outputs.py
.venv\Scripts\ruff.exe format --check src/transbridge/infra/llm_client.py src/transbridge/infra/llm_structured_outputs.py tests/infra/test_openai_structured_outputs.py
git diff --check
```

结果：Ruff lint 与格式检查通过；`git diff --check` 通过，仅有 Git 的预期 CRLF 转换警告。

## 遗留项

- 未使用真实 OpenAI-compatible 或 Claude 凭据执行联网请求，避免消耗用户额度；请求形状以官方文档、本地锁定 SDK 源码和 fake SDK 精确参数断言验证。
- 未执行完整项目测试套件；已执行 223 项相关回归、14 项最终核心复验及 94 项 Claude 相关核验。
- 仓库 `uv` 缓存目录在本机返回 Windows `os error 183`，本次 pytest 使用系统 Python 3.13.5；Ruff 使用仓库 `.venv\Scripts\ruff.exe` 独立可执行文件。
