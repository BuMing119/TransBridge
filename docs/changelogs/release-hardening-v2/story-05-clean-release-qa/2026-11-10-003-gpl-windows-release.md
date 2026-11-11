# v0.2.1.10 GPLv3 与 Windows 发布记录

- 日期为 2026-11-10，按 Asia/Shanghai 记录。
- 关联 [Release Hardening V2 S05](../../../../plans/release-hardening-v2/stories/story-05-clean-release-qa.md)。
- 发布标签 `v0.2.1.10` 指向 `7174007e5a0ca50cd41dea2ae6601c88688049cc`，于 00:36:26 公开为正式版和 Latest。发布侧增量从 `d380b803` 算起。[GitHub Release](https://github.com/BuMing119/TransBridge/releases/tag/v0.2.1.10) · [Git 差异](https://github.com/BuMing119/TransBridge/compare/d380b803...v0.2.1.10)

## 发布侧变更

- `178e98cb` 在 `pyproject.toml` 和 `uv.lock` 同步版本 `0.2.1.10`。
- `03d0fae7` 将项目许可改为 GPLv3。 `NOTICE` 按第 7(b)、7(c) 条要求保留作者署名与版权声明、禁止冒称原创、标明修改版。早期 Apache 2.0 材料及其归属保留。 `src/` 与 `data/` 的 1,402 个相关文本源文件各增加一行指向许可和署名条款。
- `build.bat` 调用 `installer/collect_licenses.py` 收集锁定环境的许可证。 `LICENSES/` 保存历史许可、Qt 和缺少 wheel 许可文本的锁定依赖材料。 `installer/setup.iss` 在安装时展示项目声明并安装许可文件。
- `81311735` 和 `7174007e` 补齐 Mesa 声明，改由用户独立安装 Microsoft Visual C++ x64 运行库。安装器检查必需 DLL，缺失时显示微软官方下载地址并在写入应用前退出。安装器从系统运行库生成部分依赖所需的文件名别名，回归测试覆盖 DLL 排除和别名来源。

## 附件结果

- `TransBridge_v0.2.1.10_Setup.exe` 为 159,509,821 字节，公开资产的 SHA-256 为 `a3b8c2422ef4b80313f806427b86e4a7104df649ee495bf4dcedd9367881930b`。
- 项目源码使用 GitHub 从标签自动生成的 `Source code (zip)` 和 `Source code (tar.gz)`，均包含该提交的项目源码和构建脚本。
- 首次上传把完整 Qt 归档和项目源码合成约 1.26 GiB 的附件。随后换成项目源码 ZIP，仍与 GitHub 的自动归档重复。纠正时曾短暂把依赖下载清单单独上传。用户最终明确只保留安装程序作为自定义附件，两份额外 ZIP 和文本清单均已从 Release 移除。回读确认安装包和 GitHub 自动归档可用，版本标签和安装包仍对应原发布提交。
- 按用户后续指示删除了 `installer/output/v0.2.1.10-gpl/` 下的两个本地源码 ZIP，共 1,377,139,262 字节。该目录保留了 2,842 字节的依赖源码下载清单，其中包含精确版本的下载地址和校验值。

## 构建与验证

- CPython 3.12.12、uv 0.9.21、Inno Setup 6.7.1。 `uv lock --check --offline` 通过，构建时使用冻结的锁文件和隔离环境。 `build.bat` 生成应用负载，最终提交重新生成许可材料和安装包，并用 Git 差异核对应用、版本、锁文件等 PyInstaller 输入与生成负载的 `03d0fae7` 一致。
- 构建前使用 `uv run --frozen pytest` 运行 `test_package_baseline.py`、`test_clean_release_smoke.py`、`test_installer_upgrade.py` 和 `test_final_qa_gate.py`，19 项通过，1 项因成品未生成跳过。成品生成后用隔离环境的 `python -m pytest` 复跑前两个模块，14 项通过。安装器回归使用私有 AppId，覆盖新装、覆盖安装和卸载保留测试数据。
- `ruff check src tests installer/collect_licenses.py` 和对应的 `ruff format --check` 均通过。隔离安装后的 CLI、MCP stdio、GUI 和许可材料检查通过，测试副本替换兼容的 Qt DLL 后 GUI 可启动。缺少 VC 运行库的模拟安装在写入应用文件前停止。
- 回读远端标签、公开 Release、Latest 和附件。实际调用桌面更新检查，从 `0.2.1.9` 发现 `0.2.1.10`，安装包 URL、大小和 SHA-256 与本地产物一致。附件纠正后再次核对公开资产列表和 Latest。
- 干净 Windows 虚拟机验证仅对本版按用户指示跳过，本轮未重跑全量 pytest。隔离安装的结果单独记录。发布证据保存在本地 `installer/output/v0.2.1.10-gpl/evidence/`，构建临时目录已清理。
- 第三方源码清单从 Release 移除后，公开下载指引待确认。精确版本的下载地址和校验值见本地清单。

后续发布按 [bm-release](../../../../.agents/skills/bm-release/SKILL.md) 执行。外部源码地址要在分发期间保持可用，实际随包分发的组件、版本及源码供给方式以当次产物和许可证为准。
