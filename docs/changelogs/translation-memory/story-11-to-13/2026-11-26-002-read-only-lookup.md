# S13 词典查询改为只读

2026-11-26 · [实施计划](../../../../plans/translation-memory/plan.md) · [FR15.1](../../../requirements.md#fr151-翻译词典)

## 交付

- `ui/tools/dictionary_lookup.py` 取代 `dictionary_panel.py` 的混合操作窗口。菜单直接聚焦搜索框。使用已有后台任务和字面搜索组件处理双语检索，切换工程或语言分区时丢弃过期结果。异常写入日志。
- `translation_memory/library.py` 增加 `lookup_rows`，用于读取当前游戏和语言分区的全部主库、个人库和旧词典。查询使用只读连接。自动套用仍使用原有活动库规则。
- `ui/tools/dictionary_results.py` 显示原文、译文和已保存的来源文件标识。高亮随查询变化，长句直接换行。行高按可见范围计算。只有来源文件名主干的历史数据原样展示。
- `ui/dialogue/view.py` 的原文与译文草稿选区右键菜单通过 `ui/main_window.py` 和 `ui/shell/intent_composition.py` 传递查询词。`ui/shell/action_catalog.py` 和 `ui/i18n/messages.pot` 更新菜单文字；`ui/shell/tool_windows.py` 复用独立窗口并重读当前词典。
- `tests/test_translation_memory_gui.py`、`tests/ui/fixtures/dictionary.py` 和 `tests/ui/tools/test_dictionary_results.py` 覆盖只读查询、来源、异步切换、实际绘制与大结果集。`tests/ui/test_long_text_window_layout.py` 验证窄窗换行。`tests/ui/test_dialogue_editor.py`、`test_main_window_shell.py`、`test_command_palette_help.py` 验证选区和工具入口。
- 移除 `tests/ui/tools/test_dictionary_apply_feedback.py` 的旧窗口写操作用例；`tests/smart_assistant/tools/test_dictionary_upload_authority.py` 在现行助手写入入口验证工程提交、失败与阶段保留。`tests/ui/test_project_editing_boundary.py` 和 `tests/contracts/projects/test_authoritative_mutation_paths.py` 更新对应桌面边界。

## 验证

以下 Qt 离屏回归共 150 项通过。真实工程夹具创建硬链接需要沙箱外的 Windows 权限。另在 150% 缩放下运行结果表四项测试，并用正式浅色主题目检 700 × 360 查询窗口。全仓 Ruff 检查和格式检查通过。未运行全仓 pytest 或制作发行包。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --offline --no-sync --no-cache pytest tests/test_translation_memory_gui.py tests/test_dictionary_library.py tests/ui/tools/test_dictionary_results.py tests/ui/test_long_text_window_layout.py tests/ui/test_dialogue_editor.py tests/ui/test_main_window_shell.py tests/ui/test_command_palette_help.py tests/ui/test_project_editing_boundary.py tests/contracts/projects/test_authoritative_mutation_paths.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/application/projects/test_dictionary_apply.py tests/ui/ux/test_current_user_journeys.py -q
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git diff --check
```
