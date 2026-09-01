# 原生 Structured Outputs（native-structured-outputs）

**状态**：已完成
**日期**：2026-08-27
**对应需求**：FR5.15
**架构决策**：[ADR-032](../../docs/adr/032-native-structured-outputs-for-ai-translation.md)

## 目标

- 翻译、润色、混合与具名自定义入口的结构化业务 LLM 响应使用 OpenAI-compatible / Anthropic 原生 Structured Outputs。
- 用稳定 JSON Schema envelope 覆盖正式翻译、专有名词抽取、proofread 和 strict 后处理阶段。
- 保留翻译流式预览、缺项恢复、共享并发预算、推理控制、prompt cache、取消与工作流日志。
- 保持智能助手原生 function calling 的接口、协议适配和会话行为不变。

## 非目标

- 不改变 Provider 配置或模型选择 UI；普通文本和 function calling 请求不迁移协议。
- 不把 Structured Outputs 扩展到普通连接检查、纯文本智能助手回答或 embedding 请求。
- 不以 schema 校验替代翻译领域的 entry key、占位符、术语、质量和提交校验。
- 不为缺少 Structured Outputs 能力的兼容端点增加 prompt-only JSON 静默回退。

## 当前实现事实和约束

- `LLMClient.chat/chat_stream` 保持字符串返回兼容；携带内部 directive 时由 Provider 映射为原生结构化响应参数。
- 智能助手通过独立的 `chat_stream_with_tools()` 使用原生 function calling，相关实现已经完成，必须保留。
- 正式翻译当前返回动态 `{entry_id: translation}` 并对流式文本做增量键值解析；专有名词和 strict 批量阶段返回根数组；proofread 已使用 `{"results": [...]}`。
- 自定义入口根据 `base_mode` 复用 translate、polish 或 mixed 运行器，不需要第四套接线。
- `llm_client.py` 已接近 500 行责任审查阈值；Provider Structured Outputs 逻辑必须提取为独立模块。
- 提示词英文化任务已经完成；Structured Outputs 接线基于其最终英文模板更新 envelope 示例。
- 当前锁定 OpenAI 2.29.0、Anthropic 0.85.0 和 jsonschema 4.26.0 已支持计划接口，无需依赖升级。

## Story 01：Provider-neutral 结构化输出指令契约

**验收标准**：

- [x] 定义不可变 `LlmOutputSchema`，拒绝非法名称、非 object 根 schema 和无效 JSON Schema。
- [x] 定义 directive 的附加与剥离函数；非法或冲突 directive 在联网前失败，剥离后的 Provider messages 不含内部元数据。
- [x] 对原始文本执行 JSON object 解析和 Draft 2020-12 schema 复验，并保持现有字符串返回兼容。
- [x] 定义 unsupported、refusal、truncated、invalid response 等可区分异常，保留原始 cause。
- [x] `LLMClient` 的 text chat/stream 仅在消息携带 directive 时启用 Structured Outputs，text/tool 方法签名均不改变。

**文件落点**：

- 新增 `src/transbridge/infra/llm_structured_outputs.py`
- 最小更新 `src/transbridge/infra/__init__.py`
- 新增 `tests/infra/test_llm_structured_outputs.py`

**实施步骤**：

1. 建立 schema 名称、根对象和 `Draft202012Validator.check_schema()` 门禁。
2. 指令只包含稳定 schema，不包含 entry ID、用户译文或凭据；日志可诊断，Provider messages 必须剥离。
3. 统一解析和验证完整原始 JSON；错误中只放诊断摘要，不回显完整翻译内容。

**验证**：schema 构造、合法 object、非 JSON、根数组、schema mismatch 和异常分类单测。

## Story 02：OpenAI-compatible 原生适配

**验收标准**：

- [x] 普通与流式结构化请求通过 Responses API 发送命名 `text.format` JSON Schema，且不发送 tools。
- [x] prompt-cache 无缓存重试保留相同 schema、reasoning patch 和输出 token 上限。
- [x] 普通与流式响应检测 refusal、length/content filter、空内容和无效 JSON，并维护请求计数/取消语义。
- [x] 现有 text chat、stream 和 function-calling 测试保持通过。

**文件落点**：

- 更新 `src/transbridge/infra/llm_structured_outputs.py`
- 最小更新 `src/transbridge/infra/llm_client.py`
- 新增 `tests/infra/test_openai_structured_outputs.py`

**实施步骤**：

1. 复用现有 prompt-cache 消息转换和 reasoning patch。
2. 对普通响应读取 output text、refusal 和 status；对流式响应聚合 `response.output_text.delta` 并读取最终终态事件。
3. 将完整文本交给 Story 01 的本地解析器，不在 Provider 模块做翻译领域判断。

**验证**：fake SDK 精确参数断言、缓存拒绝重试、refusal/truncation/invalid、流式 callback 与 active request 测试。

## Story 03：Anthropic 原生适配

**验收标准**：

- [x] 普通与流式 Messages 请求发送 `output_config.format` JSON Schema，且不发送 tools。
- [x] prompt-cache 关闭重试和 system blocks 字符串兼容重试保留相同 output_config。
- [x] 聚合所有 text blocks，检测 refusal、max_tokens、空内容和无效 JSON；max_tokens 继续要求有效正数。
- [x] 现有 text chat、stream 和 function-calling 测试保持通过。

**文件落点**：

- 更新 `src/transbridge/infra/llm_structured_outputs.py`
- 最小更新 `src/transbridge/infra/llm_client.py`
- 新增 `tests/infra/test_anthropic_structured_outputs.py`

**实施步骤**：

1. 复用 `build_anthropic_system_blocks()` 和现有重试判定。
2. 普通响应从所有 text blocks 构造原始 JSON；流式完成后读取 final message 的 stop reason。
3. 将完整文本交给 Story 01 的本地解析器。

**验证**：请求参数、缓存/system 降级、refusal/max_tokens/invalid、多 text block、流式和请求计数单测。

## Story 04：稳定业务 Schema 与解析契约

**验收标准**：

- [x] 正式翻译使用稳定 `{"results":[{"entry_id","translation"}]}` schema；领域层拒绝 unknown、duplicate、missing 和 empty。
- [x] 流式增量 parser 只产出已经完整闭合的 result item，重复/截断 salvage 不把半个 item 当成功。
- [x] 专有名词抽取从根数组迁移为 `results` envelope。
- [x] proofread 沿用已有 results envelope，并由 schema 约束 entry_key/final_translation。
- [x] strict 单条和批量检测、修复、润色、裁决均有稳定 schema；批量根数组迁移为 results envelope。

**文件落点**：

- 新增 `src/transbridge/ai_translator/structured_schemas.py`
- 更新 `src/transbridge/ai_translator/prompt_builder.py`、`translator.py`、`noun_extractor.py`
- 更新 `src/transbridge/application/translation/proofread_stage.py`、`postprocess_stages.py` 与响应 parser
- 更新 `src/transbridge/ai_translator/post_processor/{quality_gate,llm_refiner,polisher,llm_arbiter}.py`
- 更新相关 AI 翻译、proofread 和 strict 后处理测试

**实施步骤**：

1. 先定义两家 Provider 共同子集内的稳定 schema 常量/构造器。
2. 更新 Prompt 输出示例和 parser，使根 object 与 schema 一致；保留必要的旧 parser 仅用于明确的测试 double/迁移兼容。
3. 用完整 result item 的流式扫描替代动态对象键值扫描，并复用既有领域校验和拆批策略。

**验证**：translation/parser、noun extraction、proofread response、四个 strict 阶段的单条/批量/缺项/重复/未知/空值/截断测试。

## Story 05：包装链透明性、生产接线与四入口回归

**验收标准**：

- [x] `LimitedLLMClient` 无需新方法即可让 directive 请求受共享 `AiRequestBudget` 约束，prepared 调用在获准后才构建 prompt。
- [x] `ReasoningScopedLLMClient` 无需新方法即可为 directive 请求应用相同 request-scoped reasoning patch。
- [x] `WorkflowLoggingLLMClient` 通过现有普通/流式接口记录 directive 请求且不重复构建 prompt，不改变取消和脱敏行为。
- [x] translate、polish、mixed serial/parallel，以及 custom base=translate/polish/mixed 均走 structured 方法。
- [x] 智能助手原生 function calling 和普通 text chat 不受影响。

**文件落点**：

- 更新生产调用点及相应 infra、application、UI workflow 测试；包装器生产代码原则上不修改

**实施步骤**：

1. 以 spy/fake Provider 证明三类包装器通过现有消息链透明转发 directive，并维持 prepared/streaming 调用顺序。
2. 把所有生产 JSON 消费调用点切换到 structured API，扫描确认无遗漏。
3. 以运行模式回归证明 custom 仅复用基础模式，无独立文本 JSON 分支。

**验证**：budget、reasoning、logging 单测；AI translation reporting/story、mixed、custom profile 与 function-calling 回归。

## 依赖顺序与多 Agent 分工

1. Story 01 与 Story 04 的 schema 定义部分可由两个 Agent 并行；主会话负责 directive 与业务 schema 接口一致性。
2. Story 02 与 Story 03 在 Story 01 类型稳定后由两个 Agent 并行实现。
3. Story 04 的 Prompt/解析接线等待提示词英文化任务停止写同名文件后进行。
4. Story 05 依赖 Story 01～04，主会话统一处理跨模块集成；包装器只补测试，不复制新接口。
5. QA 由独立只读审查 Agent 与主会话测试结果交叉验证。

同一文件只允许一个实现所有者；所有 Agent 共享工作树，不通过覆盖或回退解决冲突。

## 风险与回退

- **兼容端点不支持 schema**：抛出明确 unsupported/Provider 错误，保留原译文；不发起文本 JSON 回退。
- **Prompt 英文化并行冲突**：先实现独立模块，待对方完成后对最新文件做小补丁并重跑其 prompt contract 测试。
- **流式 envelope 改造损失实时性**：以完整 item 闭合作为最小提交单位；若无法安全解析，则仅延迟到整包结束，不提交半结构。
- **schema 首次编译延迟**：使用稳定 schema 名称和形状，entry ID 仅出现在 messages，不进入 schema。
- **回退**：业务调用点可恢复 text API；新接口不改变持久化格式，未完整验证的结构化响应不得发布。

## 明确假设与未决问题

- 用户要求“原生”意味着生产 OpenAI-compatible / Anthropic 请求不得静默降级；这是本计划的明确兼容边界。
- 当前 SDK 锁定版本足够，无需修改 `pyproject.toml` 或 `uv.lock`；若实现期类型签名与锁文件不符，再以官方文档和本地 SDK 源码为准调整最低版本。
- Prompt 英文化只改变指令语言，不改变 JSON 字段；Structured Outputs 接线将在其最终内容上更新 envelope 示例。

## 实施验证

- `uv run pytest tests/infra tests/ai_translator tests/application/translation tests/contracts/translation/test_workload_commit.py tests/integration/translation/test_http_postprocess_chain.py tests/ui/tools/test_workflow_progress.py tests/ui/tools/test_workflow_logging_client.py tests/ui/tools/test_polish_preview_dialog.py tests/ui/tools/test_custom_profile_presenter.py tests/ui/tools/test_ai_translator_story08.py tests/ui/tools/test_ai_translator_slices.py tests/ui/tools/test_ai_translation_reporting.py tests/ui/characterization/test_ai_translator_contract.py -q`：573 passed。
- 本功能 24 个实现与测试文件的 `uv run ruff check` 和 `uv run ruff format --check`：通过。
- 全仓 `uv run pytest -q`：在最终 checkpoint 修复前为 2551 passed、6 skipped、4 failed；其中唯一落在翻译持久化边界的失败已修复并经定向及上述 573 项回归验证通过。其余失败位于两个 Qt 性能子进程和智能助手面板测试桩，不属于本功能范围。
- 全仓 Ruff 基线仍不干净：`ruff check` 报告 422 项、`ruff format --check` 报告 130 个文件，均集中于既有或并行任务文件；本功能文件独立检查通过，未批量改写其他任务成果。
