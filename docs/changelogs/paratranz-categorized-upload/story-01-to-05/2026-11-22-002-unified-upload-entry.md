# S04 统一 ParaTranz 上传入口

2026-11-22 · paratranz-categorized-upload / S04 · [使用说明](../../../dev/paratranz-categorized-upload.md)

## 操作效果

ParaTranz 菜单与工作台共用「上传…」。窗口默认使用普通上传，只勾选当前插件。工程只有一个插件时直接显示插件名。用户可以在该窗口勾选其他插件，切换普通或分类布局，并在提交前查看目标项目、文件路径及所选数量。

## 实现

- `action_catalog.py`、`menu_builder.py`、`intent_composition.py`、`context_help.py` 和 `messages.pot` 合并菜单命令、帮助入口与文案。原命令名保留为统一命令的搜索别名。
- `operation_coordinator.py`、`upload_card.py`、`cards/base.py` 和 `step3.py` 路由到一个上传命令，移除批量按钮接线。删除独立的 `upload_mode_dialog.py`。
- `facade.py`、`production.py` 与 `categorized_upload_bridge.py` 把统一窗口绑定到主窗口，并保留调用方指定固定上传范围的方式。
- `categorized_upload_dialog.py` 显示插件范围、布局、目标和文件选择。后台先读来源目录，再按已勾插件生成预览；选项变化后刷新预览。提交只捕获勾选文件所属来源。窗口继续负责取消、进度和回执，工程读取与文件发布由会话处理。
- `project_upload.py` 复用已有目录和快照命令。文件名按全工程来源名称消歧，未选插件只参与命名。补传沿用发起时的来源、布局及原文或译文策略。
- `test_upload_mode_routing.py`、`test_file_upload_production.py` 及菜单回归验证单一入口和窗口归属。`test_categorized_upload_dialog.py` 覆盖默认选择、切换布局、多插件勾选和空选保护。`test_file_upload_input_scope.py` 使用本地工程验证来源隔离与切换布局后的补传。同步更新 [FR3.4](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 和 [计划](../../../../plans/paratranz-categorized-upload/plan.md)。

## 验证

最终 281 项测试通过。首次组合运行有一次菜单悬停用例失败，单独复验和最终组合复跑均通过。使用 Qt 离屏渲染检查内置浅色主题下 125% 缩放的普通单插件和分类多插件窗口。全库 Ruff 检查和格式检查通过，覆盖 2771 个 Python 文件。Windows 硬链接所需的临时工程测试在沙箱外执行。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$uploadTests = rg --files tests/ui/operations tests/paratranz -g 'test_categorized_upload*.py' -g 'test_file_upload*.py'
uv run --offline --no-cache --no-sync pytest tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_workbench_theme_migration.py tests/ui/ux/test_current_user_journeys.py $uploadTests tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_operation_intent_routing.py tests/ui/operations/test_force_upload_confirmation.py tests/ui/operations/test_batch_download_production.py tests/application/projects/test_categorized_upload.py -q --tb=short --basetemp '.tmp-unified-upload-final-8421'
uv run --offline --no-cache --no-sync ruff check src tests
uv run --offline --no-cache --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

使用模拟网关验证文件发布，未执行真实云端上传或全库 pytest。工程无需迁移。
