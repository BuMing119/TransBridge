# Story 01～05：结构化翻译切回 Responses API

- 日期：2026-10-03
- Epic：`native-structured-outputs`
- Story：`story-01-to-05-implementation`
- 关联：[实施计划](../../../../plans/native-structured-outputs/plan.md) · [ADR-032](../../../adr/032-native-structured-outputs-for-ai-translation.md) · [需求 FR5.15](../../../requirements.md)

## 原因与范围

用户要求撤销刚完成的 Chat Completions 结构化输出适配，恢复 Responses API。DeepSeek 官方 Responses 文档列出 `text.format.type=json_schema`，而其 Chat Completions 文档仅列出 `text/json_object`。本次仅切换携带结构化输出指令的 OpenAI-compatible 翻译与后处理请求；普通文本和智能助手工具调用保留 Chat Completions，Anthropic 保留 Messages。

## 本次变更

- `src/transbridge/infra/llm_client.py`：携带 schema 的非流式和流式请求改用 `responses.create`；缓存参数被拒绝后的无缓存重试保留 schema、推理设置与输出上限。普通 Chat 和工具调用保持独立。
- `src/transbridge/infra/openai_responses_structured.py`：提取 Responses 参数构造、流事件聚合和终态校验，使主客户端低于 700 行责任阈值。
- `src/transbridge/infra/llm_structured_outputs.py`：移除本次不再使用的 Chat Schema / DeepSeek JSON mode 适配，恢复 `text.format` 与 Responses 终态分类；Markdown 代码块继续作为无效结构化响应报错。
- `src/transbridge/application/translation/postprocess_stages.py`：受控 HTTP 后处理端口改发 `/responses`，提交相同的 `text.format` Schema，并解析 Responses `output` 与 `status`。
- `tests/infra/test_openai_structured_outputs.py`、`tests/infra/test_llm_structured_outputs.py`、`tests/application/translation/test_postprocess_structured_outputs.py`、`tests/integration/translation/test_http_postprocess_chain.py`：覆盖 DeepSeek 与通用端点、流式、缓存重试、拒答/截断、代码块及 HTTP 链。
- `docs/requirements.md`、`docs/adr/032-native-structured-outputs-for-ai-translation.md`、`plans/native-structured-outputs/plan.md`：恢复当前协议契约并记录 2026-10-03 决策；旧 004/005 增量文件保持历史原样。
- `docs/changelogs/INDEX.md`：仅更新本 Epic 的最新记录。

## 验证

- `.venv\Scripts\python.exe -m pytest -p no:cacheprovider` 执行 14 个相关测试文件：176 passed。
- `.venv\Scripts\ruff.exe check src tests`：通过。
- `.venv\Scripts\ruff.exe format --check src tests`：通过。
- `git diff --check`：通过。
- `uv run pytest ...`：本机 uv 缓存目录拒绝访问，未进入 pytest；随后使用仓库现有 `.venv` 完成上述验证。

## 遗留项

- 未用真实 DeepSeek 凭据执行联网测试；所选模型是否支持 `/responses` 仍需实际端点确认。
- 兼容端点返回 Markdown 代码块时继续按无效结构化响应处理，不剥离围栏后提交译文。
