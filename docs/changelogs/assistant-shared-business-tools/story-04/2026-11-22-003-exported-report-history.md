# 助手与桌面共用历史 Excel 报告列表

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S4 第三批

- AI 翻译窗口底部的“任务记录”按钮调用 `on_open_history`，打开“历史报告”对话框。原先在对话框中查找 Excel、解析插件名和类型、按时间排序的代码已提取为 `list_exported_reports()`。[按钮绑定](../../../../src/transbridge/ui/tools/ai_translator/single_task_view.py) · [窗口回调](../../../../src/transbridge/ui/tools/ai_translator/ai_translator_window.py) · [历史报告对话框](../../../../src/transbridge/ui/tools/ai_translator/_report_history_dialog.py) · [共享查询](../../../../src/transbridge/ai_translator/report_history.py)
- `list_quality_reports` 调用相同接口，按桌面顺序截取前 `limit` 条，默认 50。每行的 `name`、`mode`、`esp_stem`、`timestamp`、`size` 对应文件名、报告类型、插件名、报告时间和字节数。已删除按会话枚举旧 `postprocess` 任务并扫描其附件的代码。文件列表使用桌面显示的报告时间，已移除旧返回字段 `modified_at` 和 `result_id`。查询可在未打开工程时使用。[助手工具](../../../../src/transbridge/smart_assistant/tools/tool_proofreader.py)
- 离线对照打开真实对话框并调用已注册的助手工具。带时间的旧文件按文件名时间排序，即使后来复制也保留原顺序。其他 Excel 使用修改时间。检查了跨插件排行、默认与显式上限、空目录、非 Excel 文件、同后缀目录和旧任务附件。[桌面对照](../../../../tests/smart_assistant/tools/test_list_quality_reports.py) · [任务报告归属测试](../../../../tests/smart_assistant/postprocess/test_report_system.py) · [桌面文件操作测试](../../../../tests/ui/tools/test_ai_secondary_dialog_safety.py) · [报告格式测试](../../../../tests/ui/tools/test_ai_translation_reporting.py)

## 验证

助手、桌面报告和 MCP 入口联合回归通过 1245 项，pytest 报告 15 条警告。对照使用临时目录中的报告文件，全仓 Ruff、格式和差异空白检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant tests/ui/tools/test_ai_secondary_dialog_safety.py tests/ui/tools/test_ai_translation_reporting.py tests/ui/tools/test_ai_translator_task_layout.py tests/ui/tools/test_ai_task_history_ui.py tests/integration/entrypoints/test_mcp_stdio.py -m 'not llm' -q --tb=short --disable-warnings
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

其余 S4 工具依照整改方案逐项验收。
