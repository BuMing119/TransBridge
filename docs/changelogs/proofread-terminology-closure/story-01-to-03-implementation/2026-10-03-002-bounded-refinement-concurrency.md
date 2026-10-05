# Proofread 术语修复批次并发

- 日期：2026-10-03
- Epic：`proofread-terminology-closure`
- Story：S02 运行控制兼容、S03 失败矩阵与回归验证
- 关联：[实施计划](../../../../plans/proofread-terminology-closure/plan.md)
- 状态：实现与相关验证完成

## 目的与范围

首轮校对已有并发能力，后续术语修复却逐批串行调用模型。本次让独立修复批次使用同一 max_workers，继续通过已配置的 LimitedLLMClient 共享任务请求预算、暂停和取消控制。本地术语检测仍串行，校验和候选提交规则不变。

本记录仅归属本轮并发实现及新增测试。前一轮的进度文案、日志汇总、窗口关闭修复，以及工作区原有的 Structured Outputs 等改动，不计入本轮实现。

## 逐文件变更

- 新增 `src/transbridge/application/translation/terminology_refinement.py`：`iter_refinement_results` 按完成顺序交付批次；最多保留 max_workers 个未消费 future，校验已返回结果后才补充任务；单线程配置直接执行。取消或提前关闭迭代器时取消未运行 future，等待运行中的任务退出后释放线程池。
- 修改 `src/transbridge/application/translation/terminology_closure.py`：`apply` 新增默认值为 1 的 max_workers；将独立模型调用交给有界调度器，结果校验、回退、进度通知仍在调用线程汇总。按实际完成条目计数并保留输入顺序；异常批次独立回退，取消回退所有待修复候选。覆盖最后一次进度回调恰好取消的边界。
- 修改 `src/transbridge/application/translation/proofread_stage.py`：`run` 将同一 max_workers 传入术语闭环；构造默认并发和显式 run 并发均生效。
- 新增 `tests/application/translation/test_terminology_refinement_concurrency.py`：用真实线程、Barrier/Event 和真实共享预算验证并发、乱序完成、输入顺序、进度汇总线程、单批失败、暂停、与翻译共享限额、等待中取消、有界提交、运行中任务排空、非法并发值和最终进度取消。
- 修改 `tests/application/translation/test_proofread_terminology_closure.py`：新增构造并发和 run 显式并发传入真实修复阶段的回归用例。
- 修改 `plans/proofread-terminology-closure/plan.md`：明确当前首轮/闭环模块分工与两阶段并发验收，关联本增量。
- 修改 `docs/changelogs/INDEX.md`：增加本记录入口，保留其他已有条目。

## 验证

最终联合回归命令（253 passed，8 条既有弃用警告）：

```powershell
uv run pytest tests/application/translation/test_proofread_stage.py tests/application/translation/test_proofread_terminology_closure.py tests/application/translation/test_terminology_refinement_concurrency.py tests/application/translation/test_ai_request_budget.py tests/infra/test_limited_llm_client.py tests/ai_translator/post_processor tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_unified_task_runtime.py -q
```

- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：通过。
- `git diff --check`：通过，仅存在 Git 的 LF/CRLF 转换提示。

测试证明修复请求真实重叠执行，并且与模拟翻译占用共用的预算峰值不超过配置上限；没有据此声称特定真实模型的加速倍数。

## 兼容性与限制

无需配置迁移或新增并发选项。并发数为 1 时保持串行。所有批次仍受现有条数与 Token 上限约束，失败候选不可提交。

取消不会强制杀死正在执行的 Python 线程；仍依赖已有客户端取消机制及请求返回/超时安全收尾。未调用真实付费 LLM、未进行真实 8277 条数据的耗时测量、未重新打包或发布。实现范围内无未完成项。
