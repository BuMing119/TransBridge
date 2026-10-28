# 工程包自动升级，移除未打包工程兼容

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05
- 前序：[004 导入名称边界](2026-10-26-004-import-name-boundary.md)
- 计划：[plan.md](../../../../plans/task-result-lifecycle/plan.md)

## 用户边界

用户明确收窄此前目录兼容方案：只需导入 `.transbridge`，程序自动识别支持的包格式并升级；用户不需要区分新旧格式或手动执行迁移。未打包的旧目录、独立 `project.json` 不再兼容。先前增量保持历史原样，当前约束已写回需求与 ADR。

## 本次变更

- `persistence/project_archive.py`：`_prepare_import` 只接受 `.transbridge` 文件，删除目录/独立 JSON 分支。包内仍自动区分两种受支持布局，统一发布为当前工程。`_read_current` 将历史快照内嵌的版本记录也送入 `migrate_to_current`，补齐此前只升级工程和当前版本的遗漏。保留原包、版本与历史数量，不额外创建正式版本；同名、身份冲突、原子发布及普通异常回滚保持。
- `persistence/legacy_project_records.py`：删除 `LegacyDirectoryRecords` 及其扫描、重解析点、目录深度等专属实现；只保留包记录读取契约和 ZIP 适配。
- `ui/coordinators/project_transfer_coordinator.py`：文件选择器只显示 `.transbridge`，移除旧目录内 JSON 的说明和筛选。继续后台检查、导入、普通重名/副本交互，无格式升级选择或额外确认。
- 删除 `tests/persistence/v2/test_legacy_project_directory.py`；`test_legacy_project_task_assets.py` 的原字节、坏附件隔离、归档往返和回滚场景改从真实工程包导入，必要的构造/故障注入 helper 保留在该测试文件内。`test_legacy_project_archive.py` 的稀疏历史恢复只保留包入口。
- `tests/persistence/v2/test_project_archive.py`：新增工程、版本、schema 2/3 历史记录一起升级并重新导出为当前格式的回归；覆盖未打包目录、独立 JSON、普通 ZIP 和带 `.transbridge` 后缀的目录不产生写入。
- `tests/ui/test_v2_project_transfer.py`：旧包/当前包共用 picker 和自动转换，原包不变；补同名重命名、重复身份副本及取消路径。
- 更新 `docs/requirements.md` 的 FR19.15.8、ADR-049、计划、开发说明及直接相关索引。未更改用户版本号、旧增量记录、旧测试报告或真实工程数据。

## 验证与审查

- `uv run --offline --no-sync pytest tests/persistence/v2/test_project_archive.py tests/persistence/v2/test_legacy_project_archive.py tests/persistence/v2/test_legacy_project_task_assets.py tests/ui/test_v2_project_transfer.py -q`：**97 passed**。
- UI Agent 独立实现范围测试：**8 passed**（包含在上述集合内）。另一个全新只读 Agent 审查真实入口、快照迁移、复制/发布与当前工程打开；新增核心用例独立执行 **6 passed**，未发现遗漏。
- `uv run --offline --no-sync ruff check src tests`：通过；`uv run --offline --no-sync ruff format --check src tests`：通过，2188 个文件。
- `uv run --offline --no-sync pytest --collect-only -q`：**9357 项收集成功**；`git diff --check`：通过。
- 测试使用专属临时目录和 offscreen Qt；未运行整套测试、真实云端/LLM 或用户桌面验收，本轮没有本次相关的未解决失败。前轮扩大矩阵限制仍保留在其 QA 中，不因本轮通过而被覆盖。

## 兼容结果

支持的 `.transbridge` 工程包在正常导入过程中完成格式升级，原包不被改写。包内可证明的版本与历史继续正常使用；原本缺来源或无法确认身份的数据仍保留明确的恢复边界，不通过格式升级伪造原文或写入权限。未打包数据不提供导入入口。
