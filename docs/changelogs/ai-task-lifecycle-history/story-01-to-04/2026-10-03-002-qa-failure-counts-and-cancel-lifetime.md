# 独立 QA 修复：失败统计、批次原因与取消生命周期

- 日期：2026-10-03
- Epic/Story：ai-task-lifecycle-history / S01～S04
- 关联：[实施计划](../../../../plans/ai-task-lifecycle-history/plan.md)

## 结果

翻译失败在任务历史中显示为失败并保留原因，覆盖空译文和覆盖翻译失败后保留旧译文的情况。任务取消或后续校对异常时，已经成功的翻译仍保留成功统计；未开始条目单独统计。

校对调用失败、恢复失败及畸形响应均关联实际受影响条目，随后取消不会把这些失败改记为未处理。

翻译取消后等待真实请求线程退出，才允许工作线程结束和资源释放。移除了等待十秒后无条件返回的路径。进度视图仍可隐藏，主程序安全退出等待后台请求结束。

## 本轮文件变更

- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：新增 `_include_translation_failures`，将明确执行失败投影到历史条目；整来源失败与单条翻译失败分开，已有翻译结果优先；`build_task_record` 优先识别显式失败状态。
- 修改 `src/transbridge/ui/tools/ai_translator/task_record_writer.py`：冻结翻译失败原因和失败计数，保留无翻译结果的 `None` 语义，后台写入不受调用方后续修改影响。
- 修改 `src/transbridge/application/translation/_open_proofread_stage.py`：请求失败与恢复耗尽诊断增加实际批次或失败子集的 `entry_keys`。
- 修改 `src/transbridge/application/translation/proofread_response.py`：畸形响应诊断关联请求批次，支持取消后的正确状态归属和原因查看。
- 修改 `src/transbridge/ai_translator/translator.py`：`_monitored_chat` 取消分支在 `finally` 中等待实际请求线程退出，取消自身抛错时也保持资源所有权。该超大模块只做既有方法的局部生命周期修复，未增加职责；整体拆分不属于本轮范围。
- 扩展 `tests/application/translation/test_task_history.py`：空译文、旧译文、失败后取消、已完成与未开始、来源异常，以及混合任务后续校对异常的统计回归。
- 扩展 `tests/ui/tools/test_ai_task_history_ui.py`：真实后台记录写入保留冻结的失败原因。
- 扩展 `tests/application/translation/test_proofread_stage.py`：恢复子集、拆分子集的诊断归属。
- 新增 `tests/ui/tools/test_proofread_failure_history.py`：真实校对 Stage、Pipeline 与历史存储联动，三种错误后取消均验证失败 1、未处理 1，原因不串条目。
- 新增 `tests/ui/tools/test_ai_translation_cancel_lifetime.py`：真实 AiTaskWorker、请求预算和阻塞假客户端，覆盖取消无效及取消抛错；请求释放前资源存活，释放后预算归零。内存替换为旧逻辑时两项回归均按预期失败。
- 更新本计划与直接相关索引。既有历史记录、其他工作线和未提交变更保持原归属。

## 验证

```powershell
uv run pytest tests/ai_translator tests/application/translation tests/contracts/translation tests/infra/test_limited_llm_client.py -q
```

534 passed，3 条既有依赖弃用警告。

```powershell
$aiTaskTests = @(rg --files tests/ui/tools | Where-Object { $_ -match '(ai_|unified_|polish|report|mixed_completion|proofread_failure_history)' })
uv run pytest @aiTaskTests tests/ui/test_ai_task_shutdown.py tests/ui/test_background_gui_operations.py tests/ui/ux/test_current_user_journeys.py -q
```

277 passed，29 条既有依赖／兼容接口弃用警告。两组共 811 项。

独立只读子 Agent 复验三项修复，并复现混合任务后续校对异常误计数；补修后执行：

```powershell
uv run pytest tests/application/translation/test_task_history.py tests/ui/tools/test_ai_task_history_ui.py -q
```

23 passed，复验未发现本轮修复的新问题。

`uv run ruff check src tests`、`uv run ruff format --check src tests`、`git diff --check` 通过。

## 兼容与限制

- 无历史格式迁移；此前已写入的错误统计不会自动重算。整任务原子应用与整来源重试保持不变。
- 提供方忽略取消且始终不结束时，安全退出仍需等待该请求；界面隐藏不受影响。
- 未调用真实 LLM，未重跑用户完整项目，未执行发布构建，未提交或推送。
- 本轮没有创建需清理的临时目录；既有 `.tmp_tests`、`qa-tmp-s03` 未修改。
