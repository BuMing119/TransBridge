# Story 01～05：原生 Structured Outputs 双协议实现与终验

- 日期：2026-08-27
- 状态：已完成，相关 QA 通过
- Epic：`native-structured-outputs`
- 关联：[plan](../../../../plans/native-structured-outputs/plan.md) · [ADR-032](../../../adr/032-native-structured-outputs-for-ai-translation.md) · [FR5.15](../../../requirements.md)

## 目标与边界

将翻译、润色、混合及具名自定义入口中由应用按 JSON 消费的 LLM 响应升级为 Provider 原生 Structured Outputs，同时保留流式展示、缺项恢复、并发预算、推理控制、prompt cache、取消、日志和领域校验。OpenAI-compatible 使用 Chat Completions `response_format` JSON Schema，Anthropic 使用 Messages `output_config.format` JSON Schema；不提供 prompt-only JSON 静默回退。

本记录不归档同一工作区内的英文提示词迁移、智能助手原生 function calling、Embedding 管理、翻译版本快照等并行任务。提示词文件仅记录本任务可明确归属的 results-envelope/结构化输出契约同步。

## 修改文件

### 需求、架构与计划

- `docs/requirements.md`（改）
  - 新增并完成 FR5.15 及 FR5.15.1～FR5.15.7，明确四入口覆盖、Provider 原生协议、领域复验、流式护栏、失败语义及 function calling 隔离。
  - 原因：把用户要求转成可验证的 P0 行为合同。
- `docs/adr/032-native-structured-outputs-for-ai-translation.md`（增）
  - 记录 Provider-neutral directive、双协议映射、稳定根对象 envelope、本地 Draft 2020-12 复验、无静默回退和模块体积审查结论。
  - 原因：固定跨 Provider 接口边界与迁移策略。
- `plans/native-structured-outputs/plan.md`（增）
  - 将实现拆为五个 Story，登记验收条件、文件落点、并行分工、风险、回退和最终验证证据。
  - 原因：为端到端实现和多 Agent 协作提供可追踪计划。
- `plans/INDEX.md`（改）
  - 增加 `native-structured-outputs` 已完成 5/5 的索引条目。
  - 原因：让计划入口可发现并反映真实完成状态。

### Provider-neutral 契约与协议适配

- `src/transbridge/infra/llm_structured_outputs.py`（增）
  - 定义不可变 `LlmOutputSchema`、内部 directive 附加/剥离、OpenAI/Anthropic 原生参数构造、本地 JSON Schema 复验，以及 unsupported/refusal/truncated/invalid 等异常。
  - 原因：把结构化输出协议细节从业务层和 Provider 客户端中提取为独立责任。
- `src/transbridge/infra/__init__.py`（改）
  - 导出结构化输出公共类型、辅助函数和异常。
  - 原因：提供稳定 infra 公共入口。
- `src/transbridge/infra/llm_client.py`（改）
  - OpenAI-compatible 普通/流式请求映射为命名 `response_format.json_schema` 且 `strict=true`。
  - Anthropic 普通/流式请求映射为 `output_config.format` JSON Schema。
  - 两协议均在请求前剥离内部元数据，保留缓存降级、reasoning patch、token 上限、请求计数和取消语义；拒绝把 structured directive 送入 tool-calling 方法。
  - 原因：在不改变现有 `chat/chat_stream` 字符串返回接口的前提下启用原生协议，并保护已完成的 function calling 链路。

### 业务 Schema、翻译与后处理接线

- `src/transbridge/ai_translator/structured_schemas.py`（增）
  - 定义正式翻译、术语抽取、proofread、通用后处理，以及质量检测、修复、润色、裁决单条/批量的稳定根对象 Schema。
  - 原因：让两家 Provider 与领域 parser 使用同一份可审查输出合同。
- `src/transbridge/ai_translator/prompt_builder.py`（改）
  - 正式翻译迁移为 `{"results":[{"entry_id","translation"}]}` envelope 并附加翻译 Schema；术语抽取迁移为 results envelope 并附加抽取 Schema。
  - 流式扫描只暴露完整闭合且字段精确的 result item，重复 ID 不作为成功结果。
  - 原因：消除动态对象根结构，使流式预览与原生 Schema 同时成立。
- `src/transbridge/ai_translator/noun_extractor.py`（改）
  - 使用结构化抽取消息和 results parser，并转发可配置输出 token 上限。
  - 原因：让翻译内术语抽取也走原生 Structured Outputs，兼容 Anthropic 正数上限要求。
- `src/transbridge/ai_translator/translator.py`（改）
  - 完整响应验证通过后才接纳流式暂存项；仅对明确截断或重复输出执行完整项 salvage 与缺项拆批重试。
  - 未知、重复、缺失和空译文继续由领域层拒绝；checkpoint 持久化失败不计成功、不更新动态术语，并允许后续运行重试。
  - 原因：避免半结构响应或未持久化候选被误提交。
- `src/transbridge/ai_translator/post_processor/prompt_contract.py`（改）
  - 集中映射四个 strict 阶段的单条/批量 Schema，并在构建消息时附加 directive。
  - 原因：避免各后处理器复制协议接线。
- `src/transbridge/ai_translator/post_processor/quality_gate.py`（改）
  - 单条/批量质量检测使用对应 Schema，批量 parser 读取 results envelope 并保守处理重复/缺项。
  - 原因：质量检测响应由原生 Schema 约束且仍保留业务判定。
- `src/transbridge/ai_translator/post_processor/llm_refiner.py`（改）
  - 单条/批量修复使用对应 Schema，批量响应迁移到 results envelope。
  - 原因：修复阶段不再依赖提示词约定根数组。
- `src/transbridge/ai_translator/post_processor/polisher.py`（改）
  - 单条/批量润色使用对应 Schema，批量响应迁移到 results envelope。
  - 原因：独立润色、混合润色和自定义 polish 基础模式共享同一结构化合同。
- `src/transbridge/ai_translator/post_processor/llm_arbiter.py`（改）
  - 单条/批量裁决使用对应 Schema，批量响应迁移到 results envelope，并继续约束裁决枚举和候选归属。
  - 原因：Provider 结构校验不替代裁决领域语义。
- `src/transbridge/application/translation/proofread_stage.py`（改）
  - 默认 proofread 消息附加稳定 proofread Schema，保持有界恢复、拆批和候选映射。
  - 原因：覆盖默认一次校对/润色链路，而不只覆盖 strict 后处理。
- `src/transbridge/application/translation/postprocess_stages.py`（改）
  - 配置化 LLM 端口附加通用后处理 Schema；原始 OpenAI HTTP 端口发送 `response_format`，检查 finish/refusal 并执行本地复验。
  - 原因：覆盖应用层直接 HTTP 和包装客户端两种生产接线。

### 提示词结构契约同步

- `data/prompts/langs/zh_CN.toml`（改）
  - 正式翻译和术语抽取示例同步为 results envelope。
- `data/prompts/quality_gate/zh_CN.toml`（改）
  - 批量质量检测输出示例同步为 results envelope。
- `data/prompts/refinement/zh_CN.toml`（改）
  - 批量修复输出示例同步为 results envelope。
- `data/prompts/polish/zh_CN.toml`（改）
  - 批量润色输出示例同步为 results envelope。
- `data/prompts/arbitration/zh_CN.toml`（改）
  - 批量裁决输出示例同步为 results envelope。

以上文件的英文措辞迁移归属于独立 `english-llm-prompts` Epic；本增量只认领与结构化输出形状一致性直接相关的变化。

### 测试

- `tests/infra/test_llm_structured_outputs.py`（增）
  - 覆盖 Schema 构造、directive 生命周期、本地解析验证和异常分类。
- `tests/infra/test_openai_structured_outputs.py`（增）
  - 覆盖普通/流式请求参数、缓存重试、refusal、截断、无效响应和请求计数。
- `tests/infra/test_anthropic_structured_outputs.py`（增）
  - 覆盖 output_config、缓存/system 降级、多 text block、refusal、max_tokens、流式和请求计数。
- `tests/ai_translator/test_structured_output_contracts.py`（增）
  - 覆盖全部业务 Schema 的合法/非法样本与根对象约束。
- `tests/ai_translator/post_processor/test_structured_output_wiring.py`（增）
  - 覆盖质量、修复、润色、裁决单条/批量消息均携带正确 Schema。
- `tests/application/translation/test_postprocess_structured_outputs.py`（增）
  - 覆盖应用后处理端口的原生参数和本地验证。
- `tests/ai_translator/test_prompt_builder.py`（改）
  - 更新 results-envelope、流式完整项和 directive 断言。
- `tests/ai_translator/test_noun_extractor.py`（改）
  - 更新术语抽取 envelope 与输出 token 转发回归。
- `tests/ai_translator/test_translator_term_conflicts.py`（改）
  - 更新翻译 envelope、截断/重复 salvage 和缺项重试回归。
- `tests/ai_translator/post_processor/test_quality_gate_prompt.py`（改）
  - 更新质量检测单条/批量结构化输出形状与解析断言。
- `tests/ai_translator/post_processor/test_refiner_prompt.py`（改）
  - 更新修复单条/批量结构化输出形状与解析断言。
- `tests/ai_translator/post_processor/test_polisher_prompt.py`（改）
  - 更新润色单条/批量结构化输出形状与解析断言。
- `tests/ai_translator/post_processor/test_arbiter_prompt.py`（改）
  - 更新裁决单条/批量结构化输出形状与解析断言。
- `tests/application/translation/test_proofread_stage.py`（改）
  - 验证 proofread directive、响应复验和恢复行为。
- `tests/ui/tools/test_workflow_logging_client.py`（改）
  - 验证工作流日志包装器透明转发 structured directive。

## 实际验证

- `uv run pytest tests/infra tests/ai_translator tests/application/translation tests/contracts/translation/test_workload_commit.py tests/integration/translation/test_http_postprocess_chain.py tests/ui/tools/test_workflow_progress.py tests/ui/tools/test_workflow_logging_client.py tests/ui/tools/test_polish_preview_dialog.py tests/ui/tools/test_custom_profile_presenter.py tests/ui/tools/test_ai_translator_story08.py tests/ui/tools/test_ai_translator_slices.py tests/ui/tools/test_ai_translation_reporting.py tests/ui/characterization/test_ai_translator_contract.py -q`
  - 结果：573 passed。
- 本功能 24 个实现与测试文件执行 `uv run ruff check` 与 `uv run ruff format --check`。
  - 结果：全部通过。
- `uv run pytest -q`
  - 最终 checkpoint 修复前结果：2551 passed、6 skipped、4 failed；其中唯一位于翻译持久化边界的失败随后已修复，并通过定向测试与上述 573 项回归。

## 遗留项

- 全仓仍有两个 Qt 性能子进程失败和一个智能助手面板测试桩失败，与本 Epic 无直接关系。
- 全仓 Ruff 基线仍包含 422 项 lint 和 130 个待格式化文件；本功能文件独立检查通过，未批量改写并行任务文件。
- 当前目标语言 UI 只提供 `zh_CN`；多目标语言发现、缺失语言配置的显式失败和提示词目录重组不属于本次 Structured Outputs Epic，需作为后续独立需求处理。
