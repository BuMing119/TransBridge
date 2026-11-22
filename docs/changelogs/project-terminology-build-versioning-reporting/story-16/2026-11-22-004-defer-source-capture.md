# 项目术语页按需检查来源并释放窗口

2026-11-22 · 项目术语构建、版本与报告 · S16 读取性能与退出。[关联计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

打开已就绪工程的术语页曾自动读取所有启用来源并校验指纹，点击提取时还要再次捕获输入。现在首次显示直接加载已有术语状态；用户点击“检查来源”或“提取术语”时才首次完整读取来源。

## 修改位置

以下四个生产文件位于 `src/transbridge/ui/tools/terminology/`。

- `window.py` 的 `showEvent` 只请求一次初始状态。`extraction_options_changed` 在首次检查前只更新配置；已有检查时随配置变化重新检查。
- `asset_presentation.py` 的 `apply_shared` 通过同一入口恢复上次提取方式，避免浏览历史结果触发来源读取。
- `launcher.py` 的 `_window_closed` 在窗口接受关闭后清理引用并安排 Qt 销毁窗口，覆盖用户手动关闭和上下文切换。
- `presenter.py` 的 `close` 在配置锁内标记关闭并释放来源快照。
- `tests/ui/test_worker_shutdown_registry.py` 新增连续五次打开、切换提取方式、关闭窗口再退出主程序的回归，验证来源捕获次数为零、各窗口已销毁、主窗口完成保存并退出。
- `tests/ui/tools/terminology/test_conflict_choices.py` 在显式重新检查多个来源后验证关窗释放快照。

## 修复时的验证

- `uv run --offline --no-sync --no-cache pytest tests/ui/tools/terminology tests/integration/terminology/test_ui_workflow.py -q`，332 项通过。
- `uv run --offline --no-sync --no-cache pytest tests/integration/bootstrap/test_terminology_presenter_build.py tests/ui/test_worker_shutdown_registry.py -q`，21 项通过。
- `ruff check src tests`、`ruff format --check src tests` 通过。

尚未测量用户“Vicn四部曲”工程的实际退出耗时。共用退出提示无法识别截图中当时等待的具体任务。
