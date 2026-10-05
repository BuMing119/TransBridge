# 结构化响应失败证据与有界拆批恢复

- 日期：2026-10-03
- 关联：[校对容错计划](../../../../plans/ai-proofread-tolerance/plan.md)
- 原因：用户任务中的 6 条和 2 条批次，首次和恢复请求都在 `additionalProperties` 校验处失败；底层异常丢弃原文，应用又将其当作调用失败，未进入既有拆批逻辑。

## 文件与符号变化

- `src/transbridge/infra/llm_structured_outputs.py`：`LlmStructuredOutputInvalidResponseError` 新增可选 `raw_response`、`validation_details` 属性，兼容原有单消息构造。`validate_structured_output` 为 JSON、根类型、代码块和 Schema 校验失败保留原文；Schema 错误记录位置、校验器、Schema 名称和多余字段名。异常消息不包含响应原文或字段值，严格校验规则保持不变。
- `src/transbridge/ui/tools/ai_translator/workflow_logging_client.py`：`_write_error` 将失败原文写入独立日志标记，复用现有脱敏；`_exception_details` 输出校验元数据。日志写入失败仍保留原异常与现有告警。
- `src/transbridge/application/translation/_open_proofread_stage.py`：`_attempt` 将结构化响应无效异常分类为 `PROOFREAD_RESPONSE_SCHEMA_INVALID`，进入既有结构错误恢复；每批上限仍为首次、恢复、两半各一次，共 4 次逻辑调用。单条最多 2 次；普通调用失败与取消沿用原行为。最终报告只含校验元数据，不复制原始响应。
- `src/transbridge/ai_translator/post_processor/proofread_diagnostics.py`：新增简短格式失败汇总文案。
- `tests/infra/test_llm_structured_outputs.py`：验证原文保留、字段定位和异常消息隔离。
- `tests/ui/tools/test_workflow_logging_client.py`：验证普通/准备后调用两条日志路径保留失败原文、脱敏并释放并发额度。
- 新增 `tests/application/translation/test_proofread_schema_recovery.py`：覆盖 6/2 条批次、两半部分成功、永久失败归属、单条上限、取消、首轮成功不重做；通过真实本地 Schema 校验器产生异常。
- `plans/native-structured-outputs/plan.md`：更新日志契约，区分简短错误提示和脱敏的工作流原始响应日志。
- `plans/ai-proofread-tolerance/plan.md`：记录已批准追加范围和验收结果；两个索引仅添加本增量链接。

## 验证

```text
uv run pytest tests/infra/test_llm_structured_outputs.py tests/ui/tools/test_workflow_logging_client.py tests/application/translation/test_proofread_schema_recovery.py tests/application/translation/test_proofread_stage.py -q
91 passed

uv run pytest tests/infra/test_llm_structured_outputs.py tests/infra/test_openai_structured_outputs.py tests/infra/test_anthropic_structured_outputs.py tests/application/translation tests/ai_translator/post_processor tests/ui/tools/test_workflow_logging_client.py tests/ui/tools/test_proofread_failure_history.py tests/ui/tools/test_ai_partial_task.py tests/integration/translation -q
399 passed

uv run pytest tests/application/translation/test_proofread_schema_recovery.py -q
7 passed（最后新增首轮成功保留测试后复验，与上面数量重叠）

uv run ruff check src tests
All checks passed

uv run ruff format --check src tests
1468 files already formatted

git -c core.safecrlf=false diff --check
通过
```

没有调用真实模型、修改历史任务或原有日志、提交代码或打包；本次没有创建需要手动清理的临时目录。旧失败原文此前未保存，无法恢复；新规则用于后续调用。真实服务商恢复率尚未验证，自动恢复仍可能耗尽次数并保留失败。既有弃用警告仍存在。
