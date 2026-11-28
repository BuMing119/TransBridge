# 工程保存时存入词典，自动与手动套用只填未翻译词条

2026-11-27 · Epic `translation-memory` · S11 保存、S12 套用、S13 桌面入口 · [实施计划](../../../../plans/translation-memory/plan.md) · [FR15.1](../../../requirements.md#fr151-翻译词典)

译者点击「保存」或按 Ctrl+S 后，当前来源已保存的可用译文会增量存入词典。重复保存同一词条时，修订译文更新原条目。后台定时保存只保存工程。「翻译」菜单保留手动存入与套用命令，查询窗口继续只读。

打开插件自动套用、从菜单手动套用以及助手套用共用同一条件：阶段为「未翻译」（0）且译文为空。已有草稿以及其他阶段的空译文均保留，命中后保持原阶段。

## 文件变更

以下源码路径相对 `src/transbridge/`。

- 修改 `ui/coordinators/variant_coordinator.py` 的 `manual_save` 和 `ui/main_window.py` 的 `save_current_project_async`，由普通工程保存入口显式传入 `include_dictionary=True`，使后台定时保存沿用默认行为。
- 修改 `ui/shell/project_save.py` 的 `ProjectSaveCoordinator`，合并保存请求时按工程版本和来源登记待入库文件。取得完整成功回执后启动词典写入，下一次工程保存等待读取完成，以免前一次保存的修订失效。
- 新增 `ui/shell/saved_dictionary.py`。`SavedDictionarySource` 将来源、游戏及语言分区绑定到保存请求，发布时核对工程及版本身份。`SavedDictionaryWriter` 在后台写入，入库故障写日志，工程保存结果仍有效。
- 修改 `ui/project_rows.py`，新增 `ProjectRowsCollection.saved_entries`，按回执中的工程和翻译版本修订读取整个当前来源，避免把保存后继续编辑的草稿写入词典。
- 修改 `ui/shell/action_catalog.py`、`ui/shell/menu_builder.py` 和 `ui/i18n/messages.pot`，分别注册两个命令、在「词典查询…」下方分组放置「存入词典」「套用词典」、补充命令文字。修改 `ui/shell/intent_composition.py`，绑定命令和菜单可用状态，由 `ui/main_window.py` 在菜单创建后接入。没有当前文件或命令执行期间，两项写命令置灰，仍可查词。
- 新增 `ui/shell/dictionary_commands.py`。`DictionaryCommands` 在后台复用已有的整文件保存和套用逻辑，防止重复启动，关闭时取消尚未提交的操作。修改 `ui/shell/tool_windows.py`，通过 `refresh_open_dictionary` 在词典实际变化后刷新已打开的查询窗口。
- 修改 `application/projects/dictionary_apply.py` 的 `prepare_library_application`，匹配前同时检查翻译阶段和译文内容。修改 `smart_assistant/prompts.py` 和 `smart_assistant/tools/tool_migrator.py` 的工具说明，使助手描述与执行条件一致。
- 修改 [需求 FR15.1.2、FR15.1.3](../../../requirements.md#fr151-翻译词典)，记录保存触发条件、手动入口和套用资格。

## 回归证据

- 新增 `tests/ui/test_project_save_dictionary.py`，用分页工程验证显式保存、后台定时保存、改译重存、连续保存的修订顺序，以及工程保存失败和词典写入失败两条路径。
- 新增 `tests/ui/test_dictionary_commands.py`，覆盖菜单命令使用的后台保存、重复执行、保留现译与关闭取消。修改 `tests/ui/test_main_window_shell.py`，验证菜单顺序、启用状态及实际路由。
- 修改 `tests/application/projects/test_dictionary_apply.py`，对同一组七种阶段和非空草稿分别运行插件打开与手动套用入口。修改 `tests/smart_assistant/tools/test_dictionary_upload_authority.py`，验证助手命中后工程及持久化条目都保留阶段 0。

实施时执行以下命令，189 项相关测试通过，全仓 Ruff 静态和格式检查通过。Windows 真实工程夹具在允许创建硬链接的环境中运行。此次归档复用该次验证结果。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/test_project_save_dictionary.py tests/application/projects/test_dictionary_apply.py tests/ui/test_dictionary_commands.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/smart_assistant/test_undo_capture.py tests/ui/test_save_completion_state.py tests/contracts/io/test_open_translation_file.py tests/test_dictionary_library.py tests/test_translation_memory_gui.py tests/ui/test_background_gui_operations.py tests/ui/test_project_open_guards.py tests/ui/test_open_file_workflow.py tests/ui/test_main_window_shell.py -q --tb=short
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
```

## 遗留项

无。
