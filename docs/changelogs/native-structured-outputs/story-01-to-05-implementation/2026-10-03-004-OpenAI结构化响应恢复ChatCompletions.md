# Story 01～05：OpenAI 结构化响应恢复 Chat Completions

- 日期：2026-10-03
- Epic：`native-structured-outputs`
- Story：`story-01-to-05-implementation`
- 关联：[实施计划](../../../../plans/native-structured-outputs/plan.md) · [ADR-032](../../../adr/032-native-structured-outputs-for-ai-translation.md) · [需求 FR5.15](../../../requirements.md)

## 原因与范围

2026-09-02 因一个兼容端点拒绝 `response_format`，OpenAI-compatible 结构化请求被整体切到 Responses API。此次按用户要求撤销该全局协议切换，恢复 Chat Completions 的原生 `response_format.type=json_schema` 和 `strict=true`。仍拒绝无效 JSON、Markdown 代码块和不符合 schema 的响应，也不降级为仅靠提示词约束的 JSON。

## 本次变更

- `src/transbridge/infra/llm_client.py`：结构化普通与流式请求均走 `chat.completions.create()`；保留 schema、reasoning、token 上限、prompt-cache 无缓存重试以及本地响应校验。流式请求从 Chat delta 和 `finish_reason` 判断拒答、截断及完成状态；移除 Responses 请求与事件消费代码。
- `src/transbridge/infra/llm_structured_outputs.py`：移除仅供 Responses 使用的参数构造和终态分类，unsupported 识别收回到 Chat Completions 的 `response_format`。保留此前工作区已有的 Markdown 代码块诊断；Chat 请求同样明确发送 `strict=true`。
- `tests/infra/test_openai_structured_outputs.py`：回归断言改为 Chat Completions 请求形状、普通与流式响应、缓存重试、推理参数、token 上限及失败分类；覆盖当时 DeepSeek 的 `response_format` 拒绝文案，并明确断言不调用 Responses API。
- `docs/requirements.md`：FR5.15 的原生协议、strict 参数及失败语义恢复为 Chat Completions 契约，并标注修订日期。
- `docs/adr/032-native-structured-outputs-for-ai-translation.md`：Provider 映射、流式结束状态和迁移说明改回 Chat Completions；保留 2026-09-02 协议切换的历史来源。
- `plans/native-structured-outputs/plan.md`：Story 02 的验收标准和响应消费步骤改回 Chat Completions。
- `docs/changelogs/INDEX.md`：只更新本 Epic 的当前状态并链接本记录。

对 `src/`、`tests/`、`scripts/` 和 `data/` 的调用点搜索确认：恢复后生产代码没有 `responses.create()` 调用。独立 HTTP 后处理端口与智能助手 function calling 已使用 Chat Completions；Anthropic 仍使用 Messages。旧增量记录保留为历史事实。

## 验证

- `.venv\Scripts\python.exe -m pytest tests/infra/test_openai_structured_outputs.py tests/infra/test_llm_structured_outputs.py -q -p no:cacheprovider`：46 passed。
- `.venv\Scripts\python.exe -m pytest tests/infra/test_llm_client_prompt_cache.py tests/infra/test_llm_reasoning_protocols.py tests/infra/test_anthropic_structured_outputs.py tests/ai_translator/test_reasoning_routing.py tests/ai_translator/test_structured_output_contracts.py tests/application/translation/test_proofread_stage.py tests/application/translation/test_postprocess_structured_outputs.py -q -p no:cacheprovider`：101 passed；另有 2 项因本机默认 pytest 临时目录拒绝访问而未完成 setup。
- `.venv\Scripts\python.exe -m pytest tests/ai_translator/test_reasoning_routing.py tests/ui/tools/test_workflow_logging_client.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-chatcompletions-qa-20261003`：13 passed；任务专用临时目录已清理。
- `.venv\Scripts\python.exe -m pytest tests/infra/test_openai_tool_calling.py -q -p no:cacheprovider`：21 passed，智能助手工具调用协议保持可用。
- `.venv\Scripts\ruff.exe check src tests`：通过。
- `.venv\Scripts\ruff.exe format --check src tests`：通过。
- `git diff --check`：通过。

## 遗留项

- 未用真实 Provider 凭据联网验证。拒绝 Chat Completions `response_format` 的模型或兼容端点仍会明确失败；本次不增加协议自动切换或 prompt-only 降级。
- `uv run` 因本机 uv 缓存目录权限问题不可用，测试改用仓库既有 `.venv`。
