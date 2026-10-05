# 点击 AI 翻译时再次提醒暂缓任务

- 日期：2026-10-04
- Epic：ai-proofread-resume；Story：S06 交互补充
- [计划](../../../../plans/ai-proofread-resume/plan.md)

## 行为与变更

用户点击“稍后处理”后，下次点击“AI 翻译”再次弹出该任务的恢复提示，暂不打开新任务配置。再次选择稍后处理仍保留提醒；提示已显示时只激活现有窗口。继续或重新开始后移除此暂缓提醒。

- 修改 `src/transbridge/ui/tools/ai_translator/task_recovery_prompt.py`：显式记录暂缓操作，按项目目录和版本隔离；用户点击入口时后台重新读取状态，完成、取消或已运行任务不再误提示。退出清除待执行入口回调。
- 修改 `src/transbridge/ui/workbench/widget.py::open_tool`：普通 AI 入口先检查暂缓提醒，明确恢复入口保持原接线；过期任务检查完后仍可正常打开配置。
- 修改 `tests/ui/tools/test_task_recovery_discovery.py`：覆盖再次点击、连续暂缓、窗口去重、继续后清除、完成/取消状态变化及跨版本隔离。

## 验证

- `.venv/Scripts/python.exe -m pytest tests/ui/tools/test_task_recovery_discovery.py tests/ui/tools/test_ai_task_history_ui.py tests/ui/test_ai_task_shutdown.py tests/ui/test_workbench_slices.py -q --tb=short --basetemp=.tmp-recovery-reminder`：44 项通过。
- `.venv/Scripts/python.exe -m ruff check src tests`、`.venv/Scripts/python.exe -m ruff format --check src tests`、`git -c core.safecrlf=false diff --check`：通过。
- 无数据格式迁移，无真实 LLM 请求；未执行整仓 pytest 或安装包构建。仅修改本地源码，未提交。
