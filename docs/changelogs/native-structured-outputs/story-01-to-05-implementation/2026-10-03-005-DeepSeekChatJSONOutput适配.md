# Story 01～05：DeepSeek Chat JSON Output 适配

- 日期：2026-10-03
- Epic：`native-structured-outputs`
- Story：`story-01-to-05-implementation`
- 关联：[实施计划](../../../../plans/native-structured-outputs/plan.md) · [ADR-032](../../../adr/032-native-structured-outputs-for-ai-translation.md) · [需求 FR5.15](../../../requirements.md)

## 原因与范围

恢复 Chat Completions 后仍向官方 DeepSeek Chat 发送 `response_format.type=json_schema`，实际 proofread 请求在生成前被 Provider 拒绝。DeepSeek 的 Chat Completions 文档只列出 `text` 和 `json_object`；Responses API 才列出 `json_schema`。用户要求继续使用 Chat Completions，因此对官方 DeepSeek 端点在首次请求时选择 Chat JSON Output，并保留本地 schema 与领域校验。不自动切换 Responses API。

## 本次变更

- `src/transbridge/infra/llm_structured_outputs.py`：新增按 HTTPS 主机名精确识别官方 DeepSeek 的 Chat 请求准备函数。DeepSeek 使用 `response_format.type=json_object`，系统消息携带稳定 JSON Schema 与仅返回 JSON 的要求；其他兼容端点继续使用命名 `json_schema` 和 `strict=true`。
- `src/transbridge/infra/llm_client.py`：普通、流式及缓存参数被拒绝后的重试共用上述格式选择，完整响应仍执行原有结束状态、本地 JSON Schema 校验。
- `src/transbridge/application/translation/postprocess_stages.py`：独立 Chat HTTP 后处理端口使用相同的 DeepSeek 格式选择，不产生协议分叉。
- `tests/infra/test_openai_structured_outputs.py`：新增官方 DeepSeek 两种 base URL、普通与流式 JSON mode、无效 schema 响应及相似主机名隔离回归。
- `tests/application/translation/test_postprocess_structured_outputs.py`：覆盖独立 HTTP 端口的 DeepSeek JSON mode 请求和结果解析。
- `docs/requirements.md`、`docs/adr/032-native-structured-outputs-for-ai-translation.md`、`plans/native-structured-outputs/plan.md`：修正“所有 OpenAI-compatible Chat 都支持 `json_schema`”的过宽契约，明确 DeepSeek JSON mode 仅保证 JSON 语法，本地 schema 校验失败不得提交。
- `docs/changelogs/INDEX.md`：只更新本 Epic 的最新记录链接。

## 验证

- `uv run pytest tests/infra/test_openai_structured_outputs.py tests/application/translation/test_postprocess_structured_outputs.py -q`：本机 uv 缓存目录拒绝访问，命令未进入 pytest。
- `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/infra/test_openai_structured_outputs.py tests/infra/test_llm_structured_outputs.py tests/infra/test_anthropic_structured_outputs.py tests/infra/test_llm_client_prompt_cache.py tests/infra/test_llm_reasoning_protocols.py tests/application/translation/test_postprocess_structured_outputs.py tests/application/translation/test_proofread_stage.py tests/integration/translation/test_http_postprocess_chain.py -q`：132 passed。
- `.venv\Scripts\ruff.exe check src tests`：通过。
- `.venv\Scripts\ruff.exe format --check src tests`：通过。
- `git diff --check`：通过。

## 遗留项

- 未使用真实 DeepSeek 凭据联网验证。当前模型别名与实际端点兼容性仍需真实请求确认。
- JSON mode 不保证 schema 形状；不符合 schema、空响应、截断和 Markdown 代码块继续按现有规则失败，不提交结果。
- 用户日志中的乱码输入属于独立问题，本次未改动编码处理。
