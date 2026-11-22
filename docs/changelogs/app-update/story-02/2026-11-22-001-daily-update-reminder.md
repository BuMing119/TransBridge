# 启动时按本地日期提醒可用更新

2026-11-22 · [应用更新 S02](../../../../plans/app-update/plan.md)

- 修改 `src/transbridge/ui/shell/update_controller.py`。启动检查发现正式新版时打开现有更新窗口。窗口显示后，按版本把本地日期写入更新缓存中的 `reminder.ini`。多个实例通过同一文件锁协调提醒。当天再次启动仍能从侧边栏查看更新说明。
- 修改 `tests/ui/test_update_controller.py`。使用临时更新目录和离屏 Qt 窗口，验证关闭中的窗口、同日再次启动、手动打开、同日另一版本以及跨日提醒。

## 验证

- `uv run --offline --no-sync --no-cache pytest tests/ui/test_update_controller.py tests/ui/test_update_dialog.py tests/ui/test_update_download_flow.py tests/ui/test_update_shutdown.py tests/ui/test_update_button.py tests/application/test_update_service.py -q` 通过 63 项。
- 仓库虚拟环境中的 `ruff check src tests`、`ruff format --check src tests` 和 `git diff --check` 均通过。
- 本次未制作安装包或执行真实发布升级。已有安装版将在升级到包含此改动的版本后获得提醒功能。
