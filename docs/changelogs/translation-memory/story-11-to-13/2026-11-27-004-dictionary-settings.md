# 在设置菜单中控制词典的自动操作与当前主库

2026-11-27 · Epic `translation-memory` · S11 保存、S12 套用、S13 桌面入口 · [实施计划](../../../../plans/translation-memory/plan.md) · [FR15.1](../../../requirements.md#fr151-翻译词典)

「设置 → 翻译词典」提供两个默认开启的勾选项。译者可独立关闭保存工程时入库或打开插件时套用，仍可使用「翻译」菜单的手动命令。两项偏好跨工程和重启保留。有具名主库时，菜单还可选择当前游戏与语言分区的主库，或清除选择。

## 实现

以下源码路径相对 `src/transbridge/`。

- 新增 `config/dictionary_preferences.py`，通过现有 INI 配置仓库保存两个布尔值。点击时先更新运行中的偏好，后台持久化失败时恢复到上次保存值。已有配置缺少这两项时取默认值。
- 修改 `bootstrap/composition.py` 注册偏好服务，通过 `ui/app.py` 的 `create_workbench_context` 和 `ui/context.py` 的 `AppContext` 将同一服务传入菜单、工程保存及插件打开流程。
- 新增 `ui/shell/dictionary_settings.py`，实现两个勾选项及可选的「主库」子菜单。主库在后台读取和切换，选择前核对游戏、语言和工程。故障写入日志。修改 `ui/shell/menu_builder.py` 创建菜单，在 `ui/shell/intent_composition.py` 绑定上下文。`ui/shell/progressive_menu_bar.py` 检查各级子菜单的可见状态，使键盘进入主库列表时菜单栏保持展开。`ui/i18n/messages.pot` 收录新增菜单文字。
- 修改 `ui/shell/saved_dictionary.py` 的 `SavedDictionarySource.capture`，自动入库关闭时直接跳过词典快照，工程继续保存。
- 修改 `application/projects/dictionary_apply.py`，由 `automatic_dictionary_library` 读取自动套用偏好。`ui/coordinators/open_file_coordinator.py`、`ui/coordinators/parse_coordinator.py` 和 `smart_assistant/tools/_source_import.py` 共用该入口。`application/io/open_translation_file.py` 在关闭时直接返回原集合。手动套用继续按「未翻译且译文为空」的条件执行。
- 更新 [FR15.1.2、FR15.1.3、FR15.1.6](../../../requirements.md#fr151-翻译词典)，记录设置入口、默认值和适用范围。

## 验证

- 新增 `tests/config/test_dictionary_preferences.py` 验证独立持久化、失败恢复和待写入状态。新增 `tests/ui/test_dictionary_settings_menu.py` 验证多个菜单同步、后台写入期间立即生效、键盘进入主库菜单、清除选择和分区变化时丢弃旧操作。
- 修改 `tests/ui/test_main_window_shell.py` 的菜单结构断言。`tests/ui/test_project_creation_reuse.py` 的窗口夹具使用临时工程运行时提供的配置，关闭前等待后台任务结束。`tests/ui/test_project_save_dictionary.py` 等待分页就绪后验证关闭自动入库后仍能保存工程并手动存入词典。`tests/smart_assistant/tools/test_file_import_parity.py` 通过同一插件验证桌面与助手的开关行为及关闭后的手动套用。

最终分两组复验，覆盖配置、菜单、保存、桌面打开和助手打开等共 202 项。排查期间曾遇到临时工程写锁冲突和一次未给出诊断的 Qt 测试进程退出。保存用例现等待分页与后台任务就绪；两组单独运行。全仓 Ruff 静态及格式检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/config/test_dictionary_preferences.py tests/ui/test_dictionary_settings_menu.py tests/ui/test_project_save_dictionary.py tests/ui/test_dictionary_commands.py -q --tb=short -x
uv run --offline --no-sync --no-cache pytest tests/ui/test_main_window_shell.py tests/ui/test_open_file_workflow.py tests/ui/test_parse_hydration_authority.py tests/smart_assistant/tools/test_file_import_parity.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/contracts/io/test_open_translation_file.py tests/application/projects/test_dictionary_apply.py tests/ui/test_save_completion_state.py tests/test_dictionary_library.py tests/test_translation_memory_gui.py tests/ui/foundation/test_locale_service.py -q --tb=short
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
```

## 遗留项

无。
