# 一键更新、自动安装与重启

日期：2026-11-11

Epic：[app-update](../../../../plans/app-update/plan.md)；Story：S03。

## 用户可见变化

更新窗口的主按钮改为“更新并重启”。用户点击后，程序下载并校验完整安装包，下载线程结束后自动进入既有保存关闭流程；安装自动进行并显示进度，成功后重新打开新版。Windows 管理员权限确认仍保留。下载取消、保存关闭取消、其他主窗口未关闭时不会继续安装；已校验安装包可稍后点击“保存并重启”继续。

安装失败、取消权限确认、需要重启 Windows 或新版无法启动时，辅助进程立即显示具体原因并记录安装包与日志位置。不会自动重启操作系统，也不宣称安装失败后自动回滚。

## 文件与动机

- 修改 `src/transbridge/ui/shell/update_controller.py`：在下载 worker 的 `finished` 释放之后自动尝试安装，下载出错清除待安装路径，保留取消/关闭及多窗口保护；交接携带当前可执行文件路径。将退出时的大文件重复哈希读取移出 Qt 主线程，沿用下载校验与 helper 启动前校验；helper 启动失败立即提示并保留错误记录。
- 修改 `src/transbridge/ui/shell/update_dialog.py`：统一“更新并重启”“保存并重启”“重试更新”按钮，点击前说明下载完成会自动保存、安装并重新打开；等待下载线程结束期间按钮仍禁用。
- 修改 `src/transbridge/ui/shell/update_handoff.py`：提取可执行的交接脚本，等待旧 PID 退出并校验安装包；通过 `/SILENT /SP- /SUPPRESSMSGBOXES /NORESTART /RESTARTEXITCODE=3010` 自动安装到当前应用目录，禁止安装器另行关闭/重启其他应用，写入 `install.log`。等待安装退出码，仅成功后由 helper 以原调用者权限启动新版；非零/未知退出码、系统重启要求、丢失文件或启动失败均明确处理。继续采用已有 SHA-256 完整性验证，没有增加新的摘要机制。
- 修改 `tests/ui/test_update_download_flow.py`：覆盖一次点击后自动衔接、取消退出后必须手动继续、结果抵达后取消/关闭/错误均阻止安装、多窗口阻塞与恢复。
- 修改 `tests/ui/test_update_dialog.py`、`tests/ui/test_update_controller.py`：更新按钮契约，验证原应用路径和期望摘要的传递、helper 无法启动与安装包丢失时的可见错误及记录。
- 新增 `tests/ui/test_update_handoff_execution.py`：实际执行生成的 PowerShell 脚本，替换进程与弹窗边界；保留真实路径检查、哈希、脚本解析及错误文件写入。11 个分支覆盖成功、等待超时、文件丢失、内容变化、权限取消、安装失败/未知状态、要求系统重启、重启失败与错误记录不可写；路径含空格、中文、单引号、美元符及反引号。
- 新增 `tests/ui/test_update_shutdown.py`：复用真实异步保存/关闭生命周期夹具，验证保存成功后仍须等待应用退出信号，保存失败并取消关闭时撤销安装。
- 修改 `tests/packaging/test_installer_upgrade.py`：隔离安装夹具支持可执行测试载荷，使用私人开始菜单组，避免影响真实快捷方式。
- 新增 `tests/packaging/test_automatic_update.py`：编译独立测试程序及随机 AppId 的 Inno 安装包，实际执行安装与重启，确认旧运行库替换、测试数据保留、应用只启动一次及日志生成；结束后卸载私人安装。测试安装器使用最低权限，真实 UAC 不在此测试中确认。
- 修改 `plans/app-update/plan.md`、`plans/INDEX.md`：将本轮流程作为 S03，更新原先“下载后再次确认/不支持静默安装”的现行约定；保留历史增量文件不变。
- 最小更新 `docs/changelogs/INDEX.md`：仅添加本记录，保留工作区原有三项增量索引。

生产安装脚本 `installer/setup.iss` 无需修改：现有 `skipifsilent` 会跳过静默安装后的启动项，由 helper 负责重启，避免双重启动或继承安装器的提权身份。普通手动安装保持原有向导。

## 验证

命令在仓库根目录执行。使用 `--frozen --no-sync` 保持现有环境与锁文件；任务专属缓存和临时目录在收尾时删除。

1. 最终聚焦回归：70 passed。

   ```powershell
   uv --cache-dir .tmp-app-update-20261111/uv-cache run --frozen --no-sync pytest tests/application/test_update_service.py tests/ui/test_update_controller.py tests/ui/test_update_dialog.py tests/ui/test_update_download_flow.py tests/ui/test_update_handoff.py tests/ui/test_update_handoff_execution.py tests/ui/test_update_shutdown.py -q --basetemp=.tmp-app-update-20261111/pytest-focused-final
   ```

2. 实际隔离安装与升级：3 passed，包括自动安装重启、首次安装、旧版覆盖升级及卸载数据保留。

   ```powershell
   $env:TRANSBRIDGE_RUN_INSTALLER_TESTS = '1'
   uv --cache-dir .tmp-app-update-20261111/uv-cache run --frozen --no-sync pytest tests/packaging/test_automatic_update.py tests/packaging/test_installer_upgrade.py -q --basetemp=.tmp-app-update-20261111/pytest-installer
   ```

3. 扩大相关回归：151 passed、1 failed。唯一失败为既有环境版本不一致，见下文。

   ```powershell
   uv --cache-dir .tmp-app-update-20261111/uv-cache run --frozen --no-sync pytest tests/application/test_update_service.py tests/ui/test_update_controller.py tests/ui/test_update_dialog.py tests/ui/test_update_download_flow.py tests/ui/test_update_handoff.py tests/ui/test_update_handoff_execution.py tests/ui/test_update_shutdown.py tests/ui/test_update_button.py tests/ui/test_cancel_application_close.py tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_save_recovery.py tests/ui/test_main_window_shell.py tests/ui/test_modern_workbench_visual_shell.py tests/packaging/test_package_baseline.py -q --basetemp=.tmp-app-update-20261111/pytest-final
   ```

4. 全库静态检查通过，2531 个文件格式检查通过；`git diff --check` 通过。

   ```powershell
   uv --cache-dir .tmp-app-update-20261111/uv-cache run --frozen --no-sync ruff check --no-cache src tests
   uv --cache-dir .tmp-app-update-20261111/uv-cache run --frozen --no-sync ruff format --check --no-cache src tests
   git diff --check
   ```

初次执行受 Windows 沙箱限制，无法访问 uv 默认缓存及 pytest 新建的临时目录；随后在审批通过的沙箱外运行隔离测试。首轮 PowerShell 测试的模块自动加载覆盖了测试替身，改为先显式加载系统模块再替换边界，11 个分支及最终 70 项复验均通过。

## 验证限制与兼容

- `test_source_import_is_headless_and_uses_project_version` 失败：现有 `.venv/Lib/site-packages/transbridge-0.2.1.9.dist-info/METADATA` 的版本为 `0.2.1.9`，而 HEAD 和工作区 `pyproject.toml` 均为 `0.2.1.10`。现有 `_version.py` 优先读取已安装包元数据。本次对 `_version.py`、`__init__.py`、`pyproject.toml` 的 `git diff --exit-code` 为空，没有为通过测试改动版本源或环境元数据。
- 未运行全仓测试、真实 GitHub 下载、实际用户安装目录升级或真实 UAC 交互；本地已验证实际私人安装包与测试程序的安装/重启链路。
- 未构建正式发行包、改版本号、提交、推送或发布 Release。现有用户首次升级到包含本改动的版本仍沿用旧客户端的更新流程，之后才使用一键自动安装重启。
- 工作区已有的发布 skill、翻译检查点、ParaTranz 下载、其他计划/ADR/增量及 `.transbridge/` 不属于本次变更。
