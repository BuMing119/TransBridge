# AI 任务暂停、窗口生命周期与持久记录

- 日期：2026-10-03
- Epic/Story：ai-task-lifecycle-history / S01～S04
- 关联：[实施计划](../../../../plans/ai-task-lifecycle-history/plan.md)

## 结果

暂停立即显示“正在暂停”，在途模型调用结束后显示“已暂停”。关闭进度窗口只隐藏视图，从工作台“AI 任务记录”可重新打开；隐藏期间需要预览确认的结果保留为“待确认”。

每个项目在 `ai-task-history` 下保存任务记录，包含版本、时间、状态、成功／失败／取消／未处理计数，以及原文、原译文、候选和诊断。Excel、CSV 由用户按需导出；报告导出不再阻塞任务结束、关闭或重试。

“保存项目”保存已应用结果；自动保存成功也同步到记录。快照独立跟踪，项目已保存时显示“创建快照”，快照失败后显示“重试快照”。任务仍采用整任务原子应用、整来源重试。

## 文件与职责

- 修改 `src/transbridge/ai_translator/translator.py`：暂停不再中断已发调用，移除对应暂停重试分支。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：增加暂停状态信号，利用已有共享请求预算确认在途调用排空。
- 新增 `src/transbridge/application/translation/proofread_batch_dispatch.py`，调整 `_open_proofread_stage.py`：有界调度首轮校对，取消后不再提交剩余批次，批次诊断保留条目归属。
- 修改 `src/transbridge/application/translation/terminology_closure.py`：取消后保留已生成候选用于查看，记录真实校验状态，所有结果仍禁止应用。
- 修改 `src/transbridge/ai_translator/post_processor/proofread_pipeline.py`、`proofread_diagnostics.py`：全局诊断不再复制到每条 note；区分已完成、失败、取消、未处理，缩短进度文案。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：保留结构化诊断；拆出 `build_source_snapshot` 与 `build_task_record`，补齐未处理输入及任务摘要。
- 新增 `src/transbridge/application/translation/task_history.py`：原子发布记录和轻量元数据，列表无需读取完整候选；可恢复 canonical 报告对象供导出。
- 修改 `src/transbridge/ui/tools/ai_translator/reporting.py`：新增单格式、原子写入的按需导出，取消或失败不会覆盖目标文件。
- 新增 `task_run.py`、`task_registry.py`（位于 `src/transbridge/ui/tools/ai_translator/`）：运行、资源与项目归属独立于视图；主窗退出停止任务并等待安全释放。
- 新增同目录 `task_record_writer.py`：后台保存记录，冻结所需条目，避免复制带锁的完整 collection；写入失败保留内存查看和重试入口。
- 新增同目录 `task_history_dialog.py`、`task_result_dialog.py`：项目记录列表、虚拟条目表、来源／文本／结果筛选、条目诊断及独立导出。
- 重构同目录 `task_progress.py`：收敛为可重开视图，终态、保存和记录状态分开显示；隐藏时不弹出预览。
- 修改同目录 `task_session.py`、`src/transbridge/ui/version_persistence.py`：保存与快照独立且幂等；仅在同一版本、输入状态仍匹配且 dirty 清除时确认外部自动保存。
- 修改同目录 `task_runtime.py`、`src/transbridge/ui/workbench/widget.py`、`src/transbridge/ui/main_window.py`：注入实际项目目录并提供永久记录入口。MainWindow 超过 500 行，本次仅增加两行委托接线，新增职责在独立模块。
- 修改 `src/transbridge/ui/shell/window_lifecycle.py`：主窗退出异步等待任务及导出，最终项目保存后再次等待记录写入；写入失败可取消退出或显式丢弃未保存记录。
- 新增测试：`test_translator_pause_control.py`、`test_proofread_diagnostics.py`、`test_proofread_dispatch_cancellation.py`、`test_task_history.py`、`test_ai_task_history_ui.py`、`test_ai_task_shutdown.py`。
- 更新测试：`test_proofread_entry_wiring.py`、`test_proofread_stage.py`、`test_proofread_terminology_closure.py`、`test_ai_task_session.py`、`test_ai_version_snapshots.py`、`test_mixed_completion_integrity.py`、`test_unified_ai_execution.py`、`test_unified_task_progress.py`、`test_unified_task_runtime.py`。覆盖暂停、重入预览取消、隐藏／重开、记录故障恢复、自动保存与快照重试、退出二次写入等待。
- 新增计划并最小更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`。

本记录仅覆盖本轮任务控制、记录和保存改动；工作区原有术语匹配、术语修复并发、结构化输出和打包配置等改动保留原归属。

## 验证

```powershell
uv run pytest tests/ai_translator tests/application/translation tests/contracts/translation tests/infra/test_limited_llm_client.py -q
```

525 passed，3 条既有依赖弃用警告。

```powershell
$aiTaskTests = @(rg --files tests/ui/tools | Where-Object { $_ -match '(ai_|unified_|polish|report|mixed_completion)' })
uv run pytest @aiTaskTests tests/ui/test_ai_task_shutdown.py tests/ui/test_background_gui_operations.py tests/ui/ux/test_current_user_journeys.py -q
```

271 passed，29 条依赖／兼容接口弃用警告。

`uv run ruff check src tests`、`uv run ruff format --check src tests`（1453 文件）和 `git diff --check` 通过。

离屏 Qt 渲染使用本机中文字体，检查暂停窗口、历史列表及 8277 条合成数据的结果表。结果表通过模型按需显示单元格，实测构建和首屏渲染约 0.04 秒；仅用于布局和规模冒烟，非真实模型运行性能结论。本次临时脚本、合成记录和截图在检查后清理。

## 兼容与限制

- 旧报告不迁移、不改写；新任务历史可重启查看，不恢复执行，也不从历史直接应用候选。
- 暂停等待已发逻辑调用（包括提供方内部重试）正常结束；Excel 打包无法强行中断内部函数，取消会阻止最终文件发布，界面仍可关闭。
- 同一运行保留已发布的记录代次，避免并发查看时删除文件；本轮不加入自动历史清理。
- 未调用真实 LLM、未重跑用户完整项目、未执行发布构建。没有本轮已知的未通过验证项；未提交或推送。
