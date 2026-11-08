# 桌面更新检查与安装交接

日期：2026-11-08

Epic：[app-update](../../../../plans/app-update/plan.md)；Story：S01～S02。

## 变更与原因

- 新增 `src/transbridge/application/update_service.py`：从固定公开 GitHub 仓库读取最新正式 Release，仅接受四段版本号、精确命名的 Windows 安装包及 SHA-256 摘要；流式下载、大小与哈希校验、取消清理和有效缓存复用。避免把未验证的远端文件交给安装器。
- 新增 `src/transbridge/ui/shell/update_controller.py`：后台自动与手动检查、版本提示、更新说明、下载进度与取消、安装前确认；源码模式不启动安装。更新下载不阻塞 Qt 事件循环；关闭窗口时取消下载。
- 新增 `src/transbridge/ui/shell/update_handoff.py`：应用确认关闭后启动独立 PowerShell helper，等待旧进程退出、重核安装包，再通过 UAC 启动 Inno Setup。交接失败写入缓存目录，下一次启动提示重试。
- 修改 `src/transbridge/ui/main_window.py` 与 `src/transbridge/ui/shell/window_lifecycle.py`：接入更新控制器；保存或关闭被取消时撤销安装交接，避免日后正常退出误触发安装。
- 修改 `src/transbridge/ui/shell/navigation_rail.py`、`intent_composition.py`、`action_catalog.py` 及 `src/transbridge/ui/foundation/tabler_icons.py`：侧边栏新增“检查更新”和“发现新版本”状态，并接入统一 intent 与下载图标。保留 `action_catalog.py` 中原有的其他工作区改动。
- 新增 `tests/application/test_update_service.py`、`tests/ui/test_update_controller.py`、`tests/ui/test_update_handoff.py`，更新 `tests/ui/test_modern_workbench_visual_shell.py` 与 `tests/ui/test_worker_shutdown_registry.py`：覆盖版本与资产验证、下载与取消、导航提示、关闭取消、旧进程等待及安装启动边界。
- 新增 `plans/app-update/plan.md` 并最小更新 `plans/INDEX.md`：记录本轮范围、发布资产约定和首次安装兼容边界。

## 验证

- `uv run --frozen --no-sync pytest tests/application/test_update_service.py tests/ui/test_update_controller.py tests/ui/test_update_handoff.py tests/ui/test_modern_workbench_visual_shell.py tests/ui/test_main_window_shell.py tests/ui/test_worker_shutdown_registry.py tests/packaging/test_package_baseline.py -q -p no:cacheprovider`：64 passed。
- `uv run --frozen --no-sync pytest tests/ui/test_update_handoff.py tests/ui/test_update_controller.py -q -p no:cacheprovider`：最终交接改动后 9 passed。
- `uv run --frozen --no-sync ruff check src tests`：通过。
- `uv run --frozen --no-sync ruff format --check src tests`：2443 files already formatted。
- 扩大回归 `tests/ui/test_worker_shutdown_registry.py tests/ui/test_main_window_coordinators.py tests/packaging/test_installer_upgrade.py tests/packaging/test_package_baseline.py`：24 passed、2 skipped、2 failed。两项失败位于工作区原有 `parse_coordinator.py` / 测试变更，都是测试假对象缺少 `source_id`，未归因于本次更新切片；前述 64 项聚焦组合通过。

## 未完成与限制

本轮未发布 GitHub Release、未生成新安装包，也未对真实安装版执行在线下载与覆盖安装。当前安装包未签名，SHA-256 可验证下载内容与发布元数据一致，但不提供发行者身份保证；正式发布前宜加入代码签名。Inno Setup 自身的安装失败和回滚能力沿用现状，不宣称自动回滚。首次带更新功能的版本需用户手动安装一次。
