# S04 上传方式、批量勾选与进度

2026-11-23 · paratranz-categorized-upload / S04

## 用户操作

ParaTranz 菜单的「上传…」先打开普通或分类选择。点「下一步」进入文件窗口，插件和当前显示的文件各有「全选」与「全部不选」。窗口从本地生成预览，点最终上传按钮后才读取并比较云端文件。进度显示已处理的文件数，结果分别显示已更新和无变化。 [使用说明](../../../dev/paratranz-categorized-upload.md)

## 实现与文档

- `upload_mode_dialog.py` 恢复独立的方式选择。`categorized_upload_bridge.py` 在用户确认方式后才创建上传会话及文件窗口，取消时退出。
- `categorized_upload_dialog.py` 用已选布局生成文件列表，插件批量勾选只触发一次预览刷新，文件批量勾选只修改当前显示的文件。预览和上传期间锁定选择，新任务仍需显式提交。
- `file_upload.py` 将串行比较阶段标为「正在核对文件」。`project_upload.py` 从现有文件回执区分已更新与无变化，兼容计数 `success_count` 仍包含两种结果。
- `test_upload_mode_routing.py` 覆盖菜单及卡片的普通、分类、取消路径。`test_categorized_upload_dialog.py` 覆盖批量勾选、局部文件范围和回执显示。`test_categorized_upload_bridge.py` 验证重复上传时可核对已有文件且零写入。
- 对齐需求、ADR、GUI 开发说明与文件 API 入口，移除现行文档中指向已删除上传类的用法，将早期批量方案标为历史。 [需求](../../../requirements.md#fr3-paratranz-平台集成) · [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) · [GUI 开发说明](../../../dev/ui.md) · [ParaTranz 开发说明](../../../dev/paratranz.md) · [计划](../../../../plans/paratranz-categorized-upload/plan.md)

`CategorizedUploadDialog` 当前 532 行、27 个方法。批量勾选与现有预览、进度共用窗口的可用状态，仍属于同一界面职责。若后续增加独立的选区或预览功能，可先将文件选择视图与后台执行反馈拆开，现有路由与回执测试可用于校验行为。

## 验证

离屏回归 300 项通过，覆盖上传方式、批量勾选、本地预览、差异写入、目标保护及补传。Ruff 检查和格式检查通过，格式检查覆盖 2772 个 Python 文件。测试工程创建使用 Windows 硬链接，相关用例在沙箱外执行。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$uploadTests = rg --files tests/ui/operations tests/paratranz -g 'test_categorized_upload*.py' -g 'test_file_upload*.py'
uv run --offline --no-cache --no-sync pytest tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_workbench_theme_migration.py tests/ui/ux/test_current_user_journeys.py $uploadTests tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_operation_intent_routing.py tests/ui/operations/test_force_upload_confirmation.py tests/ui/operations/test_batch_download_production.py tests/application/projects/test_categorized_upload.py tests/paratranz/test_concurrent_file_publication.py -q --tb=short --basetemp '.tmp-upload-docs-qa-2ce8'
uv run --offline --no-cache --no-sync ruff check src tests
uv run --offline --no-cache --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

使用模拟网关验证文件发布，未执行真实云端上传或全库 pytest。工程无需迁移。
