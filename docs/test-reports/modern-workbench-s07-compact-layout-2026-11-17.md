# 工作台结构精简 QA

- 日期为 2026-11-17，范围见 [modern-workbench-visual-shell S07](../../plans/modern-workbench-visual-shell/plan.md#s07-工作台结构精简)。
- 未发现阻止交付的行为回归。验证覆盖入口、筛选、保存反馈和工程分页。

## 自动化验证

使用 Windows 上仓库现有 uv 环境和 Python 3.12.12。执行时设置 `UV_NO_SYNC=1`、`QT_QPA_PLATFORM=offscreen` 和 `PYTHONIOENCODING=utf-8`，将 `UV_CACHE_DIR` 指向任务临时目录内的 `uv-cache`。

- `uv run pytest tests/ui/test_workbench_context_layout.py tests/ui/test_layout_stability.py tests/ui/test_workbench_filter_layout.py tests/ui/test_workbench_story07.py tests/ui/test_interaction_filters.py tests/ui/test_accessibility_contracts.py tests/ui/test_workbench_slices.py tests/ui/test_remote_target_layout.py tests/ui/test_remote_target_presenter.py tests/ui/workbench tests/ui/guidance tests/ui/foundation/test_visual_style.py tests/ui/test_v2_project_transfer.py::test_project_bar_snapshot_actions_dispatch_real_shell_intents -q --basetemp qa-tmp-workbench-s07-20261117/compact-final` 退出码 0，176 项通过。
- `uv run pytest tests/ui/test_workbench_translation_import.py tests/integration/gui/test_project_paged_workbench.py tests/integration/gui/test_ui_modularization_parity.py tests/integration/gui/test_app_context_projection.py -q --basetemp qa-tmp-workbench-s07-20261117/integration-final` 退出码 0，41 项通过。首轮在 Windows 沙箱内建测试工程时遇到 `os.link` 的 WinError 5。获准在沙箱外运行后，导入、分页、意图路由和工程投影用例通过。
- `uv run ruff check src tests` 退出码 0，检查通过。
- `uv run ruff format --check src tests` 退出码 0，2,695 个文件符合格式。

新增和调整的行为断言覆盖长名下保存反馈位于项目栏内、内容与操作同排、工程菜单中的历史任务和快照、内容菜单打开前隐藏及导入路由、配置收起时的读取和错误提示、筛选区键盘遍历及状态联动。测试保留了导出目标与筛选结果的独立性、分页滚动和编辑身份合同。

## 工作台重排首轮 Qt 截图

在 `offscreen` 平台用真实 `WorkspaceShell`、`WorkbenchWidget`、`ThemeService` 和默认 AUTO 的 `GuidanceBinding` 渲染合成测试工程。通过临时脚本运行 `uv run python qa-tmp-workbench-s07-20261117/capture.py <会话可视化目录>` 并复核四张 PNG 的像素和控件边界。截图保存在会话可视化目录。

- `workbench-s07-light.png` 为 1130×725 的浅色默认布局。保存状态可见，常驻一处 AI 翻译和导出，搜索旁只有筛选。
- `workbench-s07-dark-expanded.png` 为 900×725 的深色配置展开状态。长项目、版本和文件名称省略，配置仍能区分预览和实际应用，同时显示 ParaTranz 绑定。
- `workbench-s07-narrow-errors.png` 为 735×725 的浅色窄窗。保存失败、译名方案读取失败、ParaTranz 账号不一致均可读，配置面板收起。
- `workbench-s07-empty.png` 为 900×645 的浅色空工程。工程入口和“未打开工程”可见，未加载时无进度条残片。

本次检查使用本地合成数据和离屏 Qt，未用真实 ParaTranz 或 AI 服务进行外部写入，未运行全仓测试及其他 DPI 或平台的视觉检查。窄窗继续沿用表格列宽和滚动合同，截图中原文及译文列会随空间缩窄。

## 默认 AUTO 引导收缩复验

- `uv run pytest tests/ui/guidance tests/ui/test_layout_stability.py tests/ui/test_workbench_context_layout.py -q --basetemp qa-tmp-workbench-auto-guidance-20261117/pytest` 退出码 0，57 项通过。回归覆盖九种引导状态、显式模式的操作能力和常态到失败再返回常态的布局。
- `uv run ruff check src tests` 和 `uv run ruff format --check src tests` 退出码 0。
- `uv run python qa-tmp-workbench-auto-guidance-20261117/capture.py <会话可视化目录>` 退出码 0。沿用生产工作台、主题和默认引导绑定，对两张实际 Qt 渲染图复核。 `workbench-s07-default-without-banner.png` 为 1130×725 的默认状态，搜索区接在工程与内容区下方，表格获得原横条的空间。 `workbench-s07-alerts-without-banner.png` 为 735×725 的异常状态，保存失败、译名方案读取失败和 ParaTranz 账号不一致仍可见。
- 复验使用本地合成工程和离屏 Qt。导入与分页集成结果见首轮。本次复验未运行全仓测试或真实外部服务测试。
