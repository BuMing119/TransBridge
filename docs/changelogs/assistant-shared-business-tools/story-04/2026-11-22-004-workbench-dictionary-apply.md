# 助手与桌面共用词典匹配、冲突仲裁和写回

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S4 第四批

- 工作台的“套用词典”按钮通过 `prepare_dictionary()` 加载磁盘词典，在当前来源的词条副本上调用 `TranslationMemoryManager.apply_to_collection()`。来源文件名决定优先使用的 mod 词典。已有译文、隐藏或锁定的词条会跳过，填入译文时保留原阶段。普通键命中但原文变化的条目按桌面规则填入，并计入需复核数量。[词典面板](../../../../src/transbridge/ui/tools/dictionary_panel.py) · [共享操作](../../../../src/transbridge/application/projects/dictionary_apply.py) · [词典匹配](../../../../src/transbridge/translation_memory/manager.py)
- 有多个译法时，桌面仲裁窗口默认选中自动胜出的译文。用户可改选其他候选，直接关闭窗口会保留自动选择。每个候选以完整词条身份关联到目标行。工程命令只收到本轮实际变更的译文和阶段，并校验工程、版本及修订。[仲裁窗口](../../../../src/transbridge/ui/tools/conflict_dialog.py) · [窗口上下文](../../../../src/transbridge/ui/context.py) · [工程写回命令](../../../../src/transbridge/application/projects/gui_facade.py)
- `apply_dictionary` 在请求上下文中固定用于匹配的 mod 名，通过同一接口准备候选、应用用户的选择并提交。请求被取消、目标来源或修订变化时中止。无法展示冲突窗口时整批保持原状。成功写回计入助手本轮撤销。已删除助手逐条查询、单独改阶段和生成待合入词典成果的流程。工具现在无参数，带旧 `overwrite`、`source_locale`、`target_locale` 的请求会报错。[工具注册](../../../../src/transbridge/smart_assistant/tools/tool_migrator.py) · [请求上下文](../../../../src/transbridge/smart_assistant/tools/types.py) · [请求执行](../../../../src/transbridge/smart_assistant/tools/_dictionary_application.py)
- 手动套用按当前 mod、词条键和原文匹配。桌面入口不按语言对、词典条目的 `enabled` 标志或来源指纹过滤；此前被助手按这三个条件排除的译文，现在也可能被填入。FOMOD 流水线继续使用带语言和来源指纹的查询。[桌面匹配逻辑](../../../../src/transbridge/translation_memory/manager.py) · [FOMOD 候选查询](../../../../src/transbridge/application/fomod/candidate_planner.py)
- FR16.5.1 和 ADR-049 已按桌面实际行为，将手动套用词典明确为一次即时编辑。独立任务的保存、查询和合入有各自的契约。[现行需求](../../../requirements.md) · [任务成果决策](../../../adr/049-task-result-lifecycle.md)
- 助手提示现在说明命中统计和桌面仲裁。回归测试对照工作台按钮与助手调用，覆盖同一来源优先、键命中与文本命中、复核计数、已有译文和阶段、改选候选、请求中止、来源切换及重复执行。跨命名空间或同键不同原文的冲突按完整词条身份分开处理。本轮撤销只恢复实际被填入的词条。[助手提示](../../../../src/transbridge/smart_assistant/prompts.py) · [双入口对照](../../../../tests/smart_assistant/tools/test_dictionary_upload_authority.py) · [同键词条回归](../../../../tests/application/projects/test_dictionary_apply.py) · [本轮撤销](../../../../tests/smart_assistant/test_undo_capture.py)

## 验证

覆盖下列词典、工程提交、助手撤销和桌面窗口的聚焦回归通过 72 项。加入任务成果、请求执行、工具安全、提示词与 Agent 集成用例后，265 项通过，1 项因 Windows 环境缺少创建符号链接的权限而跳过。全仓 `src`、`tests` 的 Ruff 静态及格式检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/projects/test_dictionary_apply.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/smart_assistant/test_undo_capture.py tests/ui/tools/test_dictionary_conflict_choices.py tests/ui/tools/test_dictionary_apply_feedback.py tests/test_translation_memory.py tests/test_translation_memory_gui.py -q --tb=short --disable-warnings
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/smart_assistant/test_undo_capture.py tests/smart_assistant/tools/test_entry_result_lifecycle.py tests/ui/tools/test_dictionary_conflict_choices.py tests/ui/tools/test_dictionary_apply_feedback.py tests/application/projects/test_dictionary_apply.py tests/test_translation_memory.py tests/test_translation_memory_gui.py tests/contracts/security/test_tool_schema_security.py tests/smart_assistant/test_request_execution.py tests/smart_assistant/test_tool_prompt_layering.py tests/smart_assistant/test_english_prompt_surface.py tests/smart_assistant/test_agent_tool_integration.py -q -rs --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
```

其余 S4 工具按整改方案逐项验收。
