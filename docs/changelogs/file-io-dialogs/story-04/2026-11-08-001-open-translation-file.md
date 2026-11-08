# 固定布局打开翻译文件入口

- 日期：2026-11-08
- Epic / Story：[file-io-dialogs / S04](../../../../plans/file-io-dialogs/plan.md)
- 范围：用户确认固定布局原型后，替换旧“解析插件”入口；本记录仅包含 S04 本轮差异。S01～S03、软件更新和性能实验已有未提交内容不计入本轮；共享文件已与本轮开始时副本核对。

## 用户可见行为

“翻译 → 打开翻译文件…”和当前翻译内容的“管理 → 打开翻译文件…”打开同一个新窗口。
支持多文件、内容识别及手动格式选择，明确区分插件、ESP-ESM Translator EET/XML、xTranslator XML/SST、DSD JSON 和 ParaTranz JSON。
可选已有译文区域一直占位，未勾选时禁用并淡化，启用后默认保留已有译文、仅填空值，也可替换。
Strings 语言、跳过空原文和错误区位置固定，不显示预览、统计或检查清单。

完整解析与可选译文准备在 worker 内完成，再一次登记不可变来源和初始状态。
损坏、歧义或重复文件拒绝登记；批量保留其他文件的成功。来源保存后可以从工程内快照恢复。
每次登记核对工程版本及修订，避免旧任务发布到新的活动目标；批量后续文件使用本批已提交的修订。

## 文件与具体变更

- 新增 `src/transbridge/application/io/open_file_options.py`：不可变打开选项，隔离 UI 与应用逻辑。
- 新增 `src/transbridge/application/io/open_translation_file.py`：内容探测、显式格式、完整解析、原文件变化检查、可选译文复用和 prepared source 构建；不修改 codec 或直接发布。
- 新增 `src/transbridge/ui/workbench/open_file_dialog.py`：固定布局、文件多选、两类 JSON 明确选择、禁用区淡化、固定错误行、明显下拉箭头。
- 新增 `src/transbridge/ui/coordinators/open_file_coordinator.py`：持有非模态窗口和后台 worker，逐文件准备/提交、失败反馈、上下文及修订保护。Qt 投影异步刷新不作为批量下一个文件的提交依据，最终约束由权威命令核对。
- 修改 `src/transbridge/application/projects/gui_facade.py` 的 `add_prepared_source`：向已有 source mutation 服务透传可选 project/variant revision 参数，旧调用保持兼容。
- 修改 `src/transbridge/ui/coordinators/parse_coordinator.py` 的 `parse_plugin`：兼容方法名保留，入口改为委托独立 coordinator；原大类减少职责，未加入新的格式解析逻辑。
- 修改 `src/transbridge/ui/workbench/widget.py` 的管理菜单、`src/transbridge/ui/shell/action_catalog.py` 的名称/搜索别名，以及 `src/transbridge/ui/shell/context_help.py` 的入口帮助；intent ID 不变。
- 修改 `src/transbridge/ui/workbench/_parse_config_dialog.py` 的模块注释：明确旧表单仅供兼容，主入口不再使用。
- 新增 `tests/contracts/io/test_open_translation_file.py`：33 项应用层测试，覆盖八种格式的自动/显式识别、已有译文策略、损坏/歧义/格式不符、读入期间变化及跳过空原文。
- 新增 `tests/ui/test_open_file_dialog.py`：14 项 UI 测试，覆盖格式、取消、多选、手改路径、可选区及 9/12/16pt 字体下的窗口/按钮几何。
- 新增 `tests/ui/test_open_file_workflow.py`：15 项真实 worker/工程测试，覆盖八种格式保存后移除原文件再恢复、批量部分失败和重复、可选译文初始持久化、准备中切换上下文/修订拒绝、提交不重读原文件、取消及旧窗口提交。
- 修改 `tests/ui/test_main_window_coordinators.py` 的入口委托测试，及 `tests/ui/test_main_window_shell.py` 的新菜单名称断言。
- 修改 `docs/requirements.md` 的 FR1.10、FR28.1，`docs/dev/ui.md` 的现行打开流程和旧 Step1 说明，以及计划和两个索引；既有增量记录未改写。

## 验证证据

1. 聚焦：`uv run --offline --no-sync pytest tests/ui/test_open_file_workflow.py tests/ui/test_open_file_dialog.py tests/contracts/io/test_open_translation_file.py -q --basetemp=.tmp-open-file-focused --tb=short` — **62 passed**。
2. 扩大回归：以下命令 **1183 passed / 4 deselected**，耗时 62.80s。

```powershell
uv run --offline --no-sync pytest tests/contracts/io tests/ui/operations tests/ui/test_open_file_workflow.py tests/ui/test_open_file_dialog.py tests/ui/test_translation_import_dialog.py tests/ui/test_workbench_translation_import.py tests/ui/test_binary_eet_sources.py tests/ui/test_main_window_coordinators.py tests/ui/test_main_window_shell.py tests/ui/test_modern_workbench_visual_shell.py tests/smart_assistant/tools/test_source_import_authority.py -q --basetemp=.tmp-open-file-regression --tb=short -k "not test_authoritative_source_import_commits_initial_states_without_second_variant_command and not test_authoritative_source_import_preserves_each_same_key_initial_state and not test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads and not test_upload_menu_uses_card_mode_selection_while_other_intents_use_plan_facade"
```

3. 最后仅增加可选区淡化显示后的复验：`uv run --offline --no-sync pytest tests/ui/test_open_file_dialog.py tests/ui/test_open_file_workflow.py -q --basetemp=.tmp-open-file-final-ui --tb=short` — **29 passed**。
4. `uv run --offline --no-sync ruff check src tests` — **All checks passed**；`uv run --offline --no-sync ruff format --check src tests` — **2450 files already formatted**。最后的 UI 调整后再次执行均通过。
5. 真实 Qt 离屏截图：`out/file-io-dialogs-20261108/open-file.png` 和 `open-file-with-translations.png`；620×565，两种状态底部按钮均为 `(11, 522, 598, 32)`，已查看图像。使用合成路径，没有操作桌面或用户翻译器。

集成测试曾暴露批量第二个文件因 GUI 投影刷新滞后被误判工程变化，已修正并通过上述回归。
最初 Qt 测试进程曾在第一项后退出且无堆栈；测试模块保留 QApplication 生命周期后完成运行，该次中断不计为通过。
排除的 4 项是 S01～S03 已在 HEAD 副本确认的既有夹具失败，详见[此前记录](../story-01-to-03/2026-11-08-001-independent-import-export.md)；本轮没有把这些项目记作通过。

## 兼容性与限制

无持久化 schema 迁移，无 CLI/MCP/助手契约变化，旧 ParseConfig 及内部解析方法保留兼容。
新 UI 只接通现有格式能力，EET/SST/DSD 的实验性边界不因此扩大。
未运行整个仓库测试、发行构建或外部翻译器人工验收；本轮不修改用户样本、不提交 Git。
临时测试目录、截图脚本和基线副本在完成后清理；保留上述截图。
