# S14：任务式术语界面与独立操作窗口

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S14
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 用户行为

首页展示提取设置、进度和结果状态，不再内嵌完整术语表。查看编辑、处理冲突、历史同步、报告分别在独立窗口打开。完成提取后，根据新刷新资产的未解决冲突数量自动打开冲突窗口；无冲突不弹窗，关闭后可继续处理，普通刷新不重复弹窗。

无差异项默认采用；首页提供“应用到项目术语…”和“丢弃本次结果…”。应用沿用完整差异预览与并发校验，只需统一确认；丢弃只移除当前待应用内容，保留生效术语及历史。保存或任务运行中禁用应用/丢弃。方案管理入口可见，删除采用已有归档机制，明确说明可恢复及既有翻译版本关联保留。

## 变更落点

- 新增 `ui/tools/terminology/task_dialog.py`：可重复打开的次级窗口；关闭父界面时隐藏；禁止页面按钮成为隐式默认按钮，避免搜索回车误关窗口。
- 修改 `object_views.py`：主界面任务操作、独立编辑和冲突窗口、显式丢弃入口；提取设置可折叠而入口始终保留。
- 修改 `workbench_shell.py`：历史和报告入口打开次级窗口，首页保持当前任务。
- 修改 `workflow_controller.py`、`window.py`、`asset_presentation.py`：刷新后弹出冲突、保存与任务互斥、结果丢弃后清理陈旧摘要、查看术语入口接入独立窗口。
- 修改 `workflow_bar.py`、`workflow_state.py`、`build_view.py`、`build_projection.py`、`extraction_options.py`：面向任务的“提取/处理/应用”提示与操作，新增/无变化/冲突数量。
- 修改 `bootstrap/terminology_workloads.py`、`asset_snapshot.py`：后台读取当前生效版本，为结果计数提供比较基线。
- 修改 `change_review_dialog.py`、`version_interactions.py`：应用和丢弃的明确确认措辞，沿用既有领域命令。
- 修改 `schemes_view.py`、`terminology_profiles/{manager_layout,manager_dialog,edit_prompts}.py`：可见管理入口、明确删除与恢复入口及影响说明。
- 新增 `test_task_first_workbench.py`；更新 workflow routes/guidance、filter interactions、window lifecycle、source browser、shell icon theme、workbench simplification、change review、version interactions、schemes view 和 profile edit protection 测试，验证新窗口路径及原有数据保护。

## 验证

使用既有 uv 环境，缓存和 basetemp 位于任务临时目录 `.tmp-terminology-redesign`。项目夹具需要 Windows 本地硬链接，沙箱首轮出现 WinError 5 后提升权限运行。

```powershell
uv run --no-sync --cache-dir .tmp-terminology-redesign/uv pytest tests/ui/tools/terminology tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/tools/test_terminology_profile_manager.py -q --basetemp .tmp-terminology-redesign/full --tb=short
```

307 项通过。最终修改使用同样的 uv 参数复验 `test_task_first_workbench.py`、`test_filter_interactions.py`、`test_window_lifecycle.py`、`test_shell_icon_theme.py`、`test_extraction_mode_restore.py` 和 `tests/integration/bootstrap/test_terminology_build_review.py`，43 项通过（包含与首轮重叠用例，不相加为独立覆盖数）。全仓 Ruff check、format check 和 git diff check 通过。使用 Qt 实际渲染检查浅色主题 920×620 主界面及独立编辑窗口；自动化覆盖浅色/深色主题、键盘搜索和窗口边界。

## 兼容与验证边界

无需数据迁移。项目提取结果应用到项目术语；独立译名方案沿用其管理、预览和发布流程，不混淆应用对象。方案删除为可恢复删除，保留历史引用，不永久清除。未删除真实用户内容，未调用付费模型，未制作安装包；本记录不替代既有 S12 性能验收。
