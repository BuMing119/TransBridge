# 批量导出主线程阻塞与线程栈采样崩溃修复

- 日期：2026-11-13
- Epic / Story：file-io-dialogs / Story 02（既有导出流程缺陷修复）；关联 [plan](../../../../plans/file-io-dialogs/plan.md)。沿用 S01～S03 合并记录目录，序号递增为 002。
- 关联历史：[独立导入导出窗口](2026-11-08-001-independent-import-export.md)、[GUI 卡顿诊断器](../../maintenance/ai-runtime-diagnostics/2026-10-23-001-log-handles-and-gui-hang-stacks.md)。历史记录保留不改，诊断器当前行为以下述实现为准。
- Git 基线：`43df54bc`。工作区已有大量未提交功能变更；本记录仅覆盖本次可归属的六个代码/测试文件中的具体改动，不将同文件已有的多方案导出、位置定位与缓存功能归入本次修复。

## 触发与证据

用户在批量导出中选择输出目录，保持默认“各来源原格式”，点击“导出文件”后无响应，最终退出码为 `-1073741819 (0xC0000005)`。使用上次的 `BSAssets.esm`、`BSHeartland.esm` 继续取证。

- 实际 GUI 卡顿栈显示：`write_dialog._finish_automatic_export → confirm → facade.submit → write_feature.submit_write → _check_batch → operation_write.preflight → write_projection.project_entries → projection._normalized_source_contains → re.escape`。后台预检完成后，确认阶段又在 GUI 线程重复执行全量预检和术语投影，导致事件循环无法响应。
- 大术语方案还会为大量根本不在原文中的词条构造边界正则，增加匹配成本和正则缓存抖动。
- 独立复现中，原生 `faulthandler.dump_traceback_later` 输出 `Timeout (0:00:15)!` 后，在线程栈尚未写完时发生 `Windows fatal exception: access violation`；当时工作线程正在插件保真校验，GUI 事件循环仍有心跳。这是独立于主线程阻塞的采样崩溃问题。
- Windows 事件中原始故障与独立复现均为 `python312.dll` / `0xc0000005`，偏移分别为 `0x9a956`、`0x287750`，不能视为同一指令位置。原生延迟采样与解释器帧销毁竞争的机制有 [CPython #140815](https://github.com/python/cpython/issues/140815) 支持；现有证据支持移除该危险采样路径，但未取得原生 C 栈或 minidump，不能宣称已逐指令证明首次退出的原因。

## 逐文件变化

- 修改 [ui/operations/write_feature.py](../../../../src/transbridge/ui/operations/write_feature.py)：`submit_write` 的批量与单文件原格式分支复用后台预检缓存及冻结投影，删除确认时重复预检与对应的未使用导入。批量输出路径完全冲突时仍从既有 `preflight.checks` 提前拒绝提交；实际工作负载继续执行原有输入/目标身份检查和发布前指纹校验。未改写多方案展开逻辑或底层发布服务。
- 修改 [application/terminology_profiles/projection.py](../../../../src/transbridge/application/terminology_profiles/projection.py)：仅在 `_normalized_source_contains` 中先判断规范化术语是否为原文子串，缺失时立即返回；存在时继续使用原有边界规则。不改变 Unicode 边界、大小写/空白规范化及替换语义。
- 修改 [ui/hang_watchdog.py](../../../../src/transbridge/ui/hang_watchdog.py)：以 Python 守护线程和 `sys._current_frames()` 保留的帧引用替代原生延迟采样；采样、轮转和关闭串行化，先停止并等待监视线程，再关闭日志。保持每次连续卡顿一次采样、恢复记录、2 MiB 轮转与两个备份；限制 100 个线程/每线程 100 帧，不写局部变量或源码正文。不安装或取消进程级原生故障处理器及延迟采样定时器；诊断失败保留异常上下文。
- 新增 [tests/ui/operations/test_write_projection_responsiveness.py](../../../../tests/ui/operations/test_write_projection_responsiveness.py)：四种单文件/批量、普通/多方案组合，通过真实 Qt facade、后台运行时和术语投影断言 GUI 线程不执行投影、后台等待期间心跳可响应、输出译文正确且原集合未变。
- 修改 [tests/ui/test_hang_watchdog.py](../../../../tests/ui/test_hang_watchdog.py)：覆盖线程关闭/等待、轮转、启动失败和不调用原生延迟采样 API；隔离子进程验证 Python 等待、快速帧创建销毁和 Windows 持有 GIL 的原生等待，检查恢复、日志隐私与关闭后不再写入。
- 修改 [tests/application/terminology_profiles/test_prepared_projection.py](../../../../tests/application/terminology_profiles/test_prepared_projection.py)：新增 `test_literal_candidate_filter_preserves_unicode_boundaries` 的 11 组边界回归，包括英文词前后 Unicode、下划线、撇号、`C++`、空白规范化、中文子串与空词。
- 新增本记录，并在 [changelog 索引](../../INDEX.md) 添加一条入口；既有 plan 状态未改变。

## 实际文件复验

环境为 Windows、仓库既有 Python 3.12.12 / uv 环境。读取同批两个 ESM 文件，通过 SQLite 只读备份复制已保存项目数据，在隔离目录运行真实 Qt facade、任务运行时和正式发布/保真校验；使用已保存的 20,074 条译文和 8,418 条映射方案，未调用真实模型或网络服务。

- 修复后的两文件批量导出约 **18 秒**，两个文件均完成；`BSAssets.esm` 单文件导出约 **5 秒**，导出期间心跳约每秒持续输出。
- 启用新诊断器，并将阈值缩短到 0.1 秒、注入 350 ms GUI 停顿，验证采样及恢复后导出可以完成。
- 最后逐字节比较两个原始输入与运行前内容，确认原文件未改变。输出写入本任务隔离目录，没有覆盖用户来源。
- 上述耗时为本机单轮实际流程观测，不是严格性能基准；不是完整主窗口鼠标操作或原生目录选择器验收。

保留的本机证据位于 `data/log/batch-export-diagnosis-20261113/`（诊断产物，不作为用户翻译数据提交）：

- [original-gui-stall.log](../../../../data/log/batch-export-diagnosis-20261113/original-gui-stall.log)：用户再次运行时捕获的完整 GUI Python 卡顿调用链。
- [native-sampler-crash.log](../../../../data/log/batch-export-diagnosis-20261113/native-sampler-crash.log)：原生延迟采样期间访问冲突及 Python 线程栈。
- [fixed-batch.log](../../../../data/log/batch-export-diagnosis-20261113/fixed-batch.log)、[fixed-single.log](../../../../data/log/batch-export-diagnosis-20261113/fixed-single.log)：修复后完成结果、心跳与原文件未变核对。
- [safe-sampler-stacks.log](../../../../data/log/batch-export-diagnosis-20261113/safe-sampler-stacks.log)：新诊断器采样结果。
- [regression.log](../../../../data/log/batch-export-diagnosis-20261113/regression.log)：最终扩大回归输出。

本任务的 `.tmp-batch-export-repro` 已在确认归属和绝对路径后清理，包含复现脚本、项目副本、导出样本及临时测试缓存；保留上述取证日志。其他机器需自行提供合法样本和隔离环境，不能依赖本机日志链接。

## 验证命令与结果

以下为修复阶段实际执行的命令；本轮仅补写历史记录，不重复运行代码测试。

```powershell
$env:TEMP = 'D:/MyCode/TransBridge/.tmp-batch-export-repro/runtime'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = 'D:/MyCode/TransBridge/.tmp-batch-export-repro/uv'
$env:PYTHONIOENCODING = 'utf-8'
uv run --offline --no-sync pytest tests/ui/operations/test_write_projection_responsiveness.py -q -x -p no:cacheprovider --basetemp=.tmp-batch-export-repro/test-before
uv run --offline --no-sync pytest tests/ui/operations tests/application/terminology_profiles tests/ui/test_hang_watchdog.py tests/contracts/io/test_localized_hydrated_publication.py tests/integration/gui/test_ui_foundation_startup.py -q -p no:cacheprovider --basetemp=.tmp-batch-export-repro/final-regression
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check -- src/transbridge/ui/operations/write_feature.py src/transbridge/application/terminology_profiles/projection.py src/transbridge/ui/hang_watchdog.py tests/ui/operations/test_write_projection_responsiveness.py tests/ui/test_hang_watchdog.py tests/application/terminology_profiles/test_prepared_projection.py
```

- 修复前新响应性回归首例失败，报 `Terminology projection blocked the GUI thread`；修复后四种组合通过。
- 扩大回归最初发现四个完全冲突批次未提前拒绝的问题，已修复并纳入最终复验。另有沙箱内 SQLite 临时数据库重命名 `WinError 5`；在允许该文件操作的环境执行相同测试集合后消除该环境阻碍。
- 最终扩大回归：**689 passed，3 warnings，28.63 秒，退出码 0**；警告来自既有 SWIG 类型。该数字包含聚焦用例，不累计重复测试次数。
- 全仓 Ruff 检查通过，格式检查 **2646 files already formatted**；六文件差异空白检查通过。
- 本轮文档记录另外检查新增编号唯一、相对链接可解析及文档差异空白。

## 兼容、限制与遗留项

无需配置或数据迁移，输出格式与既有发布保护保持。运行中的旧进程需要重新启动才能加载修复；未改依赖、锁文件、安装器或构建配置，未提交或推送 Git。

新诊断器依赖 Python 获得执行机会：原生调用若持续持有 GIL，采样会推迟，不能保证在恢复之前取得阻塞现场栈。它提供有界 Python 线程栈，不替代原生崩溃转储。

未运行全仓 pytest、真实网络/LLM 集成或发行包构建；未进行完整主窗口/原生目录选择器操作验收。原始崩溃的原生 C 栈仍未取得，现有访问冲突结论的证据边界见上文。

本次代码修复与增量记录无剩余实施项。
