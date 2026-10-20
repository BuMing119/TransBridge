# ParaTranz 同步检查原因统计与逐条日志

- 日期：2026-10-20
- Epic / Story：`paratranz-sync-operation-ux` / S12
- 计划：[plan.md](../../../../plans/paratranz-sync-operation-ux/plan.md)
- 需求：FR22.3.1

## 问题与结果

同步窗口显示数千条待处理时，只有数量无法判断问题是否来自重复标识、失效关联、原文差异或目标占用。本轮按用户最终要求只补现有应用日志；不新增诊断报告、导出按钮或界面，不猜测这批真实数据的根因，也不实施新的批量匹配规则。

每次成功完成上传或下载预检，记录一条 `PARATRANZ_SYNC_CHECK` 摘要及每个实际冲突计划项的 `PARATRANZ_SYNC_UNMATCHED`。同次检查共享独立 `check_id`，同计划重新检查也使用新编号。摘要包含方向、项目、计划和快照标识、操作数量、未解决数及原因码/中文说明/数量；逐条记录本地标识、出现次数、已有云端关联、候选数量与编号、可用性、阻止原因及匹配证据。

计数以实际 `CONFLICT` 计划项为准，已确认跳过的项不计入；重复本地条目的实例数单列。没有冲突也记录摘要；缺少复核证据时明确标记不可用，不推断“没有候选”。过期选择重建标记 `stale_decisions`。预检失败记录 `PARATRANZ_SYNC_CHECK_FAILED` 及失败类别；诊断生成异常记录 `PARATRANZ_SYNC_DIAGNOSTICS_FAILED` 和异常栈，保留已生成的同步计划。

诊断消息为 INFO 级单行 JSON，复用现有应用日志和凭据脱敏。原文只保留 SHA-256、长度及比较结果，不记录原文或译文正文，不记录内部选择 token 或引用 metadata。上下文先完整脱敏，再取最多 240 字符预览，附原值指纹、长度与截断标记；精确相等判断使用完整原值。空白、换行或大小写处理后的比较仅放在 `diagnostic_only`，不用于自动匹配。换行及 Unicode NEL/行分隔符均转义。

## 本轮文件增量

前轮上传、下载和界面工作尚未提交；本记录只归属以下日志增量，不将其他工作区修改算入本 Story。

- 新增 `src/transbridge/paratranz/sync_diagnostics.py`：Qt-free 诊断投影及结构化日志。
- 新增 `src/transbridge/paratranz/sync_diagnostic_reasons.py`：既有原因码的中文说明。
- 修改 `src/transbridge/ui/operations/sync_preparation.py`：成功预检日志、失败分类日志及诊断异常隔离。
- 新增 `tests/contracts/paratranz/test_sync_diagnostics_logging.py`：19 项日志合同回归。
- 新增 `tests/ui/operations/test_sync_diagnostic_preflight.py`：19 项真实预检链路回归，服务使用受控 fixture。
- 更新需求、S12 计划及相关索引。

两个新增生产模块各少于 250 行，预检模块少于 200 行；日志职责独立，不增加窗口职责，不修改持久化格式、公共计划导出、匹配规则或远端写入合同，无数据迁移。

## 验证

使用仓库现有 Python 3.12 / uv 环境，不安装或更新依赖。先执行新增合同和预检测试，再运行最终联合回归：

```text
uv run --no-sync --no-cache pytest tests/ui/operations tests/contracts/paratranz tests/paratranz tests/security/test_data_boundaries.py tests/ui/test_logging_setup.py -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-sync-log-pilot-final
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
```

- 最终联合回归：**512 passed, 1 skipped**。真实账户合同因未配置而跳过；11 项既有 SWIG/兼容 API 弃用警告。
- Ruff 全库通过；格式检查 **1948 files already formatted**。
- 覆盖双向统计、全部 2,181 条日志、已解决项排除、重复出现次数、候选占用及缺失、重复检查、失效选择、零冲突、预检失败、诊断异常、长上下文和真实日志 formatter 的 JSON 可解析性与凭据脱敏。
- 生产预检 fixture 断言零云端写入、本地集合不变；既有下载→本地编辑→上传往返测试继续通过。
- 未执行全库 pytest、真实 ParaTranz 联机检查、安装包构建或发布。原始数千条问题仍需用户用更新后的代码重新检查，依据实际日志分析。

## 日志位置与检索

开发运行沿用 `data/log/*.log`；打包程序沿用 `%APPDATA%\TransBridge\data\log\*.log`。搜索 `PARATRANZ_SYNC_CHECK` 找到最新检查摘要，再用其 `check_id` 汇集同次检查的全部逐条诊断。这里的 JSON 是应用日志的一条消息，不生成单独报告文件。
