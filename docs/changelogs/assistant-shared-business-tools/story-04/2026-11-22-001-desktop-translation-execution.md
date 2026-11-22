# 助手翻译与润色接入桌面任务执行器

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S4 第一批

- `start_translation` 和 `start_polish` 通过工作台上下文调用 `AiTaskWorker.execute()`。桌面线程入口也调用这个方法。两者共用请求准备、并发预算、名称准备、逐来源执行、术语终检和收尾。已删除助手自行创建翻译器和组装校对流程的代码，以及 `_polish_execution.py` 和 `_polish_llm_runtime.py`。[桌面任务入口](../../../../src/transbridge/ui/tools/ai_translator/task_worker.py) · [工作台与助手的连接](../../../../src/transbridge/ui/translation_task_execution.py)
- 范围预览与实际启动都读取桌面为该模式保存的预设。关闭全部润色阶段时，预览为零条，任务也不会启动。润色请求可以显式覆盖本次策略与强度。启动前按实际运行的阶段检查模型配置。助手继续负责会话归属、进度和停止，已验证的成果先保存再合入。取消时保留已验证的成果供查询。[工具入口](../../../../src/transbridge/smart_assistant/tools/tool_translator.py) · [预览与启动配置](../../../../src/transbridge/smart_assistant/tools/translation_options.py) · [任务收尾](../../../../src/transbridge/smart_assistant/tools/translation_task_run.py)
- 当前工程的术语归属在启动时捕获，后台准备使用该归属。共享参考快照的保存支持 Windows 长路径。[来源执行](../../../../src/transbridge/ui/tools/ai_translator/source_execution.py) · [参考快照回归](../../../../tests/ai_translator/test_task_reference_snapshot.py)

## 验证

用临时工程从注册工具启动任务，在模型边界注入离线响应。对照覆盖桌面和助手的混合模式译文与阶段、范围外词条、保存的润色策略和强度、显式覆盖、暂停或停止信号与成果合入。没有调用线上模型或 ParaTranz 服务。[桌面对照测试](../../../../tests/smart_assistant/tools/test_desktop_translation_execution.py) · [助手任务生命周期测试](../../../../tests/smart_assistant/tools/test_translation_task_lifecycle.py)

桌面启动测试在结束时处理 Qt 的延迟删除事件，避免模拟事件循环留下的进度对话框影响后续退出测试。启动、术语终检与窗口关闭的顺序回归通过 53 项。[桌面启动测试](../../../../tests/ui/tools/test_unified_task_runtime.py)

最终联合运行通过 2205 项。全仓 Ruff 检查、格式检查及 Git 差异空白检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ai_translator tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_project_proofread_terms.py tests/ui/tools/test_run_request_preparation.py tests/ui/tools/test_ai_launch_config_snapshot.py tests/ui/tools/test_ai_task_launch_background.py tests/ui/tools/test_ai_task_term_closure.py tests/ui/tools/test_ai_source_term_closure.py tests/ui/tools/test_ai_task_closure_progress.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_ai_task_frozen_references.py tests/ui/tools/test_ai_task_feedback.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_ai_runtime_control_order.py tests/ui/tools/test_ai_runtime_commit_boundary.py tests/ui/test_ai_task_shutdown.py -m 'not llm' -q --tb=short --disable-warnings
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

S4 其余工具待验收，下一项为 `run_postprocess`。
