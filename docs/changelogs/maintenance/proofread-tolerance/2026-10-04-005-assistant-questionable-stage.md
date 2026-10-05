# 助手润色写回校对疑问状态

- 日期：2026-10-04
- Epic/Story：maintenance/proofread-tolerance
- 关联：[校对容错计划](../../../../plans/ai-proofread-tolerance/plan.md)
- 原因：共享校对逻辑在保护语法重试失败后保留原译文并返回 stage=2，但助手 start_polish 仅按译文变化写回，导致条目仍保持检查通过状态。

## 文件与符号变化

- 修改 `src/transbridge/smart_assistant/tools/_polish_execution.py` 的 `execute_polish`：已接受结果同时比较译文和目标状态；任一变化即写回。未提供目标状态时保留旧状态，拒绝结果仍不应用。通过 `dataclasses.replace` 保留条目其余字段；仅状态变化不计入 polished_count，保持原有计数含义。
- 修改 `tests/smart_assistant/tools/test_polish_llm_runtime.py`：新增四种参数组合，以真实共享校对流水线和模拟模型响应覆盖 stage=1/2、保护语法正常/损坏。验证保留译文、疑问状态写回、成功结果、计数及无变化时版本不变。
- 修改 `docs/changelogs/INDEX.md`：增加本记录链接。其他未提交修改不属于本次增量。

## 验证

- 修复前运行 `uv run pytest tests/smart_assistant/tools/test_polish_llm_runtime.py -q`：1 failed、8 passed；新增测试准确复现目标状态为 2、实际仍为 1。
- 修复后运行 `uv run pytest tests/smart_assistant/tools/test_polish_llm_runtime.py tests/smart_assistant/tools/test_run_postprocess.py tests/smart_assistant/test_agent_tool_integration.py tests/ai_translator/post_processor/test_proofread_pipeline.py -q --disable-warnings`：145 passed，15 项弃用警告。
- `uv run ruff format tests/smart_assistant/tools/test_polish_llm_runtime.py`：格式化新增测试。
- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：1508 files already formatted。

## 兼容及遗留项

工具名称、参数和返回字段不变，无需配置或数据迁移。本次未调用真实模型、未运行全库 GUI 测试或打包；验证集中于助手润色和共享校对链路。未创建临时文件，未提交代码。授权范围内无遗留项。
