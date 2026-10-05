# 校对重试携带具体校验反馈

- 日期：2026-10-03
- 关联：[校对容错计划](../../../../plans/ai-proofread-tolerance/plan.md)、FR5.15.6
- 原因：自动恢复原先只提示遵守 JSON 契约，没有告诉模型上轮具体缺少译文或包含多余字段。用户确认继续 bm-pilot 实现。

## 文件与符号变化

- 新增 `src/transbridge/application/translation/proofread_feedback.py`：`recovery_feedback` 仅使用明确归属当前请求 entry key 的已知校验诊断，生成修复说明；支持缺字段、多余字段、类型错误、漏项、重复项、空译文、保护语法及整份结构错误。每条最多 8 项问题，每类字段最多 12 个、每个标识符最多 64 字符；路径使用已知 Schema 路径。忽略异常正文、未知诊断、无归属及其他条目诊断。未知格式字段仍给出按 Schema 删除额外字段的通用指导。
- 修改 `src/transbridge/application/translation/_open_proofread_stage.py`：首次自动恢复携带首轮诊断，拆批携带最近恢复轮次诊断；`_messages` 增加可选诊断参数，在输入 JSON 的 `retry_feedback` 中发送反馈。系统提示要求按条目修复、将字段名视为数据，并保持原输出契约。网络异常的重试不再声称“未通过响应校验”，也不发送网络异常原因。已有校验、成功结果隔离、取消和最大调用次数不变。
- 新增 `tests/application/translation/test_proofread_recovery_feedback.py`：11 项回归覆盖缺译文及多余字段同时反馈、6 类校验问题、拆批最新原因与身份、网络异常隔离、内容限长和并发隔离。模拟模型根据所收到的反馈返回合法结果，验证最终结果和成功条目保留。
- 修改 `docs/requirements.md` FR5.15.6、`plans/ai-proofread-tolerance/plan.md`、计划及日志索引：同步已确认的自动重试反馈契约与验证证据。

## 验证

```text
uv run pytest tests/application/translation/test_proofread_recovery_feedback.py tests/application/translation/test_proofread_schema_recovery.py tests/application/translation/test_proofread_response_isolation.py -q
29 passed

uv run pytest tests/application/translation tests/ai_translator/post_processor tests/infra/test_llm_structured_outputs.py tests/ui/tools/test_workflow_logging_client.py tests/ui/tools/test_proofread_failure_history.py tests/integration/translation -q
378 passed

uv run ruff check src tests
All checks passed

uv run ruff format --check src tests
1476 files already formatted

git -c core.safecrlf=false diff --check
通过
```

## 兼容及限制

本次仅修改校对运行内的自动恢复与拆批提示；手动“重试失败条目”仍发起新的执行轮次，不回灌历史任务诊断。术语修复已有的问题传递保持不变。无需数据或配置迁移，不增加依赖；新增反馈职责提取到独立模块，调度模块为 492 行。

未调用真实模型，因此未测服务商实际纠错率；未运行全库测试或打包，本次使用上述相关回归。既有 3 项弃用警告仍在。未修改用户缓存、译文和任务历史，未创建临时目录，未提交。授权范围内无遗留项。
