# 独立导入译文与导出文件窗口

- 日期：2026-11-08
- Epic：file-io-dialogs；Story：01～03
- 计划：[独立文件窗口](../../../../plans/file-io-dialogs/plan.md)
- 基线：`a889d672`。任务开始仅既有 `.transbridge/` 未跟踪，未改动该目录；未提交 Git。

## 用户可见变化

当前内容旁的“导入译文…”打开独立导入窗口，选择一个来源及保留/替换策略；词条列表上方“导出文件”打开独立导出窗口，选择格式与保存位置。导入和导出均无匹配预览、填充/替换统计或手动检查面板。导出自动后台检查，覆盖已有文件才询问；失败显示原因，成功显示实际输出位置。

DSD JSON 与 ParaTranz JSON 分列，序列化不同。两种 XML、二进制 EET、SST 各自保持格式契约。DSD 需要明确插件定位，未知身份不从文件名推断。工程保存与文件导出仍是独立动作。

## 文件变更

以下生产文件路径相对 `src/transbridge/`，测试路径相对仓库根。

- 新增 `ui/workbench/translation_import_dialog.py`：目标文件/格式、单一来源、保留或替换、条件显示 Strings 语言/范围；文件选择保留明确格式。
- 修改 `ui/coordinators/parse_coordinator.py`：迁移入口委托新窗口，保留旧配置 API，仍捕获并验证目标上下文。
- 修改 `ui/coordinators/migration_coordinator.py`：后台调用新导入服务，捕获来源快照；保持工程/版本/词条变化检查和正式提交，完成消息去除匹配统计。
- 新增 `application/io/translation_import.py`：按内容识别单来源、显式格式消歧、默认填空或替换；保持锁定/隐藏保护。
- 新增 `application/io/xml_translation_import.py`：XML 唯一候选匹配，比较原文、字段、索引及已知标识，支持重复导入与 XML→二进制 EET。
- 新增 `application/io/dsd_projection.py`：从捕获插件快照恢复 DSD 定位，检查摘要/大小、完整身份及原文，导入导出共用。
- 修改 `application/io/migration_import.py`：DSD 使用明确插件名、FormID、字段及索引匹配，未知插件归属拒绝；保留需要比较原文的记录语义。
- 修改 `application/io/legacy_migration.py`：增加兼容默认值的 Strings 覆盖策略参数。
- 新增 `application/io/json_export.py`：独立 JSON 草稿、固定译名投影、目标指纹/重名检查、两种 JSON 序列化及统一原子发布；保留覆盖备份、取消和批量结果。
- 新增 `ui/operations/write_json_export.py`：JSON 与原格式草稿之间的界面映射、单个/批量路径和明确格式名称，不放宽原生 adapter 契约。
- 修改 `ui/operations/write_feature.py`：组合 JSON 草稿/检查/发布能力，沿用运行时来源权限与提交保护。
- 修改 `ui/operations/write_dialog.py`：当前来源、格式和路径；隐藏检查/统计，点击导出自动检查，等待后台检查释放后提交；覆盖弹窗、右侧主按钮、失败重试。
- 修改 `ui/operations/execution_dialog.py`、`execution_result.py`：增加默认开启的套用数量显示参数；仅导出窗口关闭，其他操作保持原行为。
- 修改 `ui/workbench/cards/write_card.py`：旧 DSD 独立直接写文件分支改走统一导出窗口，保留调用入口。
- 修改 `ui/shell/action_catalog.py`、`context_help.py`：统一导入/导出名称与帮助，旧意图 ID 和检索别名兼容。
- 修改 `ui/workbench/widget.py`、`workflow_actions_view.py`、`workflow_presenter.py`：当前内容导入入口和全文件导出入口，明确导出不受表格筛选/选择影响。
- 新增 `tests/contracts/io/test_translation_import_policy.py`：各格式填空/替换、保护、歧义/损坏、唯一匹配、XML→EET 与 DSD 摘要等回归。
- 新增 `tests/ui/test_translation_import_dialog.py`：窗口精简、格式区分、拖放预填、来源浏览和策略配置。
- 新增 `tests/ui/operations/test_export_json_dialog.py`：两种结构、单个/批量、真实自动导出、覆盖 Yes/No、目标变化、取消、DSD 快照与原文、部分完成，共 19 项。
- 修改 `tests/ui/test_main_window_coordinators.py`、`test_workbench_translation_import.py`、`test_interaction_filters.py`：新窗口委托、真实工程导入及入口文案。
- 修改 `tests/ui/operations/test_execution_result_ui.py`、`test_plugin_transfer_ui.py`、`test_strict_write_routes.py`、`test_target_plugin_workflow.py`、`test_write_binary_eet.py`、`test_write_execution_feedback.py`、`test_write_save_as.py`：调整手动检查/统计的旧交互断言，同时保留目标内容、旧确认失效、来源不变、失败不发布等断言。
- 修改 `docs/requirements.md` FR1.7/FR2.4/FR4.6 和 `docs/dev/ui.md`：更新真实入口、主工作台布局、独立窗口、JSON 限制与后台流程，旧 Step1 标为兼容面板。
- 新增 `plans/file-io-dialogs/plan.md`；更新 `plans/INDEX.md` 和本日志索引。

## 验证证据

首轮扩大验证命令：

```powershell
uv run --offline --no-sync pytest tests/contracts/io tests/ui/operations tests/ui/test_translation_import_dialog.py tests/ui/test_workbench_translation_import.py tests/ui/test_main_window_coordinators.py tests/ui/test_interaction_filters.py tests/ui/test_command_palette_help.py -q --basetemp=.tmp-file-io-dialogs-regression --tb=short --deselect=tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_commits_initial_states_without_second_variant_command --deselect=tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_preserves_each_same_key_initial_state
```

结果：1072 passed / 10 failed / 2 deselected。八项旧 UI 断言与新交互不一致，已修正并保留安全语义；其余两项和原先排除的两项均用 `git archive HEAD` 原始副本及现有 Python 环境复现：

- `test_main_window_coordinators.py::test_authoritative_source_import_commits_initial_states_without_second_variant_command`
- `test_main_window_coordinators.py::test_authoritative_source_import_preserves_each_same_key_initial_state`
- `test_batch_download_production.py::test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads`
- `test_operation_intent_routing.py::test_upload_menu_uses_card_mode_selection_while_other_intents_use_plan_facade`

分别为模拟结果缺 `source_id`（前两项）、模拟 slot 缺 `collection`、模拟 host 缺 `workbench`。未改无关夹具或放宽生产约束。

最终联合命令：

```powershell
uv run --offline --no-sync pytest tests/ui/operations tests/contracts/io/test_translation_import_policy.py tests/contracts/io/test_migration_import.py tests/contracts/io/test_original_match_migration.py tests/contracts/io/test_stage_policy.py tests/ui/test_translation_import_dialog.py tests/ui/test_workbench_translation_import.py tests/ui/test_main_window_coordinators.py tests/ui/test_interaction_filters.py tests/ui/test_command_palette_help.py -q --basetemp=.tmp-file-io-dialogs-final2 --tb=short --deselect=tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_commits_initial_states_without_second_variant_command --deselect=tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_preserves_each_same_key_initial_state --deselect=tests/ui/operations/test_batch_download_production.py::test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads --deselect=tests/ui/operations/test_operation_intent_routing.py::test_upload_menu_uses_card_mode_selection_while_other_intents_use_plan_facade
```

结果：605 passed / 4 deselected，46.73 秒。首次相同范围（basetemp 为 `.tmp-file-io-dialogs-final`）在 `ThemeService.close()` 设置样式表时原生 access violation 中断；单独运行 `uv run --offline --no-sync pytest tests/ui/operations/test_operation_plan_layout_stability.py -q --basetemp=.tmp-file-io-layout --tb=short`，7 passed，随后上述完整命令通过。偶发原因未定位，未将中断轮计为通过。

- `uv run --offline --no-sync ruff check src tests`：通过。
- `uv run --offline --no-sync ruff format --check src tests`：2436 文件已格式化。
- `git diff --check`：通过（仅 Windows 换行提示）。
- Qt 离屏实际窗口截图已逐张检查：`out/file-io-dialogs-20261108/import.png`、`export.png`，合成来源与示例路径；截图目录已被 Git 忽略。

## 兼容、清理及遗留

无需工程 schema 迁移；CLI/助手契约和旧 ParseConfigDialog API 保留。ParseCoordinator 超过 500 行只做局部委托，新增责任置于独立服务。临时脚本、原始副本及测试目录在交付前按确切绝对路径清理；保留截图，不修改真实样本和既有 `.transbridge/`。

遗留：上述 4 项既有测试夹具问题及一次无法稳定复现的 Qt 清理异常。未跑全仓库测试、发行构建或外部翻译器人工验收；本轮不使用 computer-use。
