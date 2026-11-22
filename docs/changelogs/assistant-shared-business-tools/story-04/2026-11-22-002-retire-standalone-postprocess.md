# 助手撤下独立后处理任务

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S4 第二批

## 桌面入口

AI 翻译窗口的“质量处理”页设置校对策略和严格模式的阶段开关。点击开始后，窗口启动翻译、润色或混合任务。具名自定义工作流也按其基础模式启动。翻译任务在处理译文时按配置运行后处理，已有译文的校对由来源执行器的润色阶段处理。[质量处理页](../../../../src/transbridge/ui/tools/ai_translator/single_task_view.py) · [窗口启动方法](../../../../src/transbridge/ui/tools/ai_translator/ai_translator_window.py) · [来源执行器](../../../../src/transbridge/ui/tools/ai_translator/source_execution.py) · [翻译内后处理](../../../../src/transbridge/ai_translator/translator.py)

## 助手变更

- 撤下 `run_postprocess` 的工具注册、角色指令和变更预览。旧计划引用此工具时按无效步骤中止。独立执行和请求组装模块 `postprocess_task_run.py`、`_postprocess_tool_runtime.py` 及其专用模型运行时 `_workflow_llm_runtime.py` 一并删除。[工具目录](../../../../src/transbridge/smart_assistant/tools/tool_proofreader.py) · [校对 Agent](../../../../src/transbridge/smart_assistant/agents/agent_registry.py)
- 对已有译文进行校对或严格质量检查使用 `start_polish`。翻译或混合任务中的质量处理由 `start_translation` 发起。两者可用 `profile` 指定桌面保存的工作流名称或 ID，按桌面配置仓库及 `CustomWorkflowProfile.apply_to()` 应用到本次任务。配置的基础模式须与请求一致，避免润色请求意外启动翻译。[任务启动](../../../../src/transbridge/smart_assistant/tools/tool_translator.py) · [配置解析](../../../../src/transbridge/smart_assistant/tools/translation_options.py)
- `get_quality_report` 从当前会话、工程及版本已结束的助手 AI 任务中读取最近一份报告，复用桌面任务中心的报告及候选投影。移除依赖独立运行器写入的进程内“最近报告”缓存。原会话可查阅已保存的旧任务成果，并通过既有成果合入流程应用候选。已导出的 Excel 可继续查询。[报告工具](../../../../src/transbridge/smart_assistant/tools/tool_proofreader.py) · [任务中心报告读取](../../../../src/transbridge/application/tasks/history_service.py) · [已有成果合入回归](../../../../tests/smart_assistant/tools/test_translation_task_lifecycle.py)

## 验证

工具注册、提示、旧计划、报告归属和启动范围的离线回归通过 70 项。旧工具已从原生目录和角色指令撤下。测试将其放在写入步骤之后，整份历史计划在启动前被拒绝，写入次数为零。另验证了未知或模式不符的工作流无法启动，以及助手与桌面从同一份成果读取报告。[工具目录测试](../../../../tests/smart_assistant/test_tool_consolidation.py) · [计划校验](../../../../tests/smart_assistant/test_execution_engine.py) · [报告对照](../../../../tests/smart_assistant/postprocess/test_report_system.py)

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/test_tool_consolidation.py tests/smart_assistant/test_execution_engine.py tests/smart_assistant/test_english_prompt_surface.py tests/smart_assistant/postprocess/test_report_system.py tests/smart_assistant/test_desktop_translation_scope.py -q --tb=short --disable-warnings
```

最终联合回归包含助手、翻译、桌面任务历史、配置模式、翻译契约和 MCP stdio，2515 项通过，pytest 报告 20 条警告。使用离线模型响应。全仓 Ruff、格式和 Git 差异空白检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ai_translator tests/ui/tools/test_ai_task_history_ui.py tests/application/translation tests/contracts/translation tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_custom_profile_presenter.py tests/ui/tools/test_ai_mode_switching.py tests/integration/entrypoints/test_mcp_stdio.py -m 'not llm' --tb=short -q --disable-warnings
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

其余 S4 工作按[整改方案](../../../../plans/assistant-shared-business-tools/plan.md)逐项验收。
