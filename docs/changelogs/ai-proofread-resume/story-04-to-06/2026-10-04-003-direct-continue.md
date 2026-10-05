# 继续任务直接启动，重新开始进入配置

- 日期：2026-10-04
- Epic：ai-proofread-resume；Story：S06
- [计划](../../../../plans/ai-proofread-resume/plan.md)

## 变更

根据用户指定的交互，继续任务不再要求经过配置页和二次点击。仅重新开始进入 AI 翻译配置页。继续仍恢复原参数、核验原范围并沿用现有启动预检；失败明确提示且不发起请求。

- `src/transbridge/ui/tools/ai_translator/ai_translator_window.py::open_for_translation`：增加默认开启的窗口展示选项，普通入口不变，恢复入口先隐藏构建。
- `src/transbridge/ui/tools/ai_translator/task_recovery_binding.py::open_recovery`：集中处理恢复配置、直接启动或显示配置页；失败保留日志并弹出原因，隐藏的临时配置窗口关闭。
- `src/transbridge/ui/workbench/widget.py::open_tool`：注入运行依赖后执行恢复动作；继续直接启动，重新开始才展示配置。
- `src/transbridge/ui/tools/ai_translator/task_recovery_prompt.py`：提示说明同步新行为。
- `tests/ui/tools/test_task_recovery_discovery.py`：两个按钮分别验证启动/展示行为，补配置不匹配时不启动的回归。
- 同步计划中的当前交互说明，历史增量保持不变。

## 验证

- `.venv/Scripts/python.exe -m pytest tests/ui/tools/test_task_recovery_discovery.py tests/ui/tools/test_task_recovery_binding.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_translator_task_layout.py tests/ui/test_workbench_slices.py -q --tb=short --basetemp=.tmp-direct-resume`：80 项通过。
- `.venv/Scripts/python.exe -m ruff check src tests`、`.venv/Scripts/python.exe -m ruff format --check src tests`、`git -c core.safecrlf=false diff --check`：通过。
- 未调用真实 LLM、未执行整仓 pytest、未打包或提交；无需数据迁移。
