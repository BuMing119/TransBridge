# 分类上传批量关联与界面进度

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S09
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4.3](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 使用说明：[分类上传](../../../dev/paratranz-categorized-upload.md)
- 范围：上传关联按文件/批次保存，阶段反馈与 UI 响应；不新增旧云端布局迁移

## 问题与结果

真实日志中，首个 7 词条文件导入、回读结束后，到下一文件开始相隔 52.927 秒。原会话逐条调用工程关联提交，每次都重新序列化、原子保存整个 Variant 并发布投影；目标/修订检查本身只是轻量校验。后台线程存在，但逐条持久化、逐条 Qt 进度事件及主线程处理投影仍造成等待。上传窗口原为非模态，手动编辑还可能在主线程等待后台保存持有的生命周期锁。

现由应用层累积已核实的文件绑定及词条身份，在完整文件边界达到 500 条关联或 16 个文件时保存，小文件共用一批；一次导入的大文件整体保存，已有文件的逐条远端操作每 500 条保存。取消、部分失败和正常结束保存剩余已确认事实。保存失败只尝试一次，受影响结果记为待核实并停止后续写入；重新对照后可复用真实远端身份，不重复创建。

上传不新建翻译版本，保留权威事务、目标与修订保护。仍然是完整工程持久化，但次数按批次减少；没有新增关联侧存储或修改 schema。

UI 每 100 ms 读取当前任务的最新进度，结束时取出末值并关闭通道，旧任务通知不污染新任务。显示权限核查、云端读取及分页、上传、回读和本地保存；已处理数量包含非成功项，最后本地提交完成前不显示处理完成。文件结果在后台汇总，异常示例及 GUI 渲染有界，汇总文案改为“失败或待核实”。分类窗口以 `WindowModal` 和 `show()` 异步展示，关闭前所属工作台暂不接受手动编辑，进度与取消仍可操作。

## 逐文件变化

开始时工作区已有分类上传及其他工作线的未提交修改。修改归属以本轮启动副本为基线，不将所有未跟踪文件视为本轮新增。

- **新增 `src/transbridge/application/categorized_upload/checkpoints.py`**：`EntryAssociation` 保留完整本地身份；`CheckpointBuffer` 合并关联、按阈值提交，记录 `checkpoint_started/checkpoint_saved/save_checkpoint`，保存失败降级回执并禁止自动重试。
- **修改 `src/transbridge/application/categorized_upload/executor.py`**：增加可选批次回调，保留原单条回调兼容；按文件/逐条写入边界提交、取消后收尾，保存失败停止后续操作，修正终态前的进度与取消文案。
- **修改 `src/transbridge/application/projects/categorized_upload.py`**：复用来源校验索引，只替换本批发生变化的词条/来源对象；原子持久化与并发修改保护不变。
- **修改 `src/transbridge/paratranz/categorized_upload.py`**：可选 `status` 回调描述现有请求阶段、文件序号、页码和数量；不新增请求或重试，不输出翻译正文。
- **修改 `src/transbridge/ui/operations/categorized_upload_bridge.py`**：以一次来源索引校验整批回执，一次调用权威提交；连接网关阶段反馈并保留完整执行回执。
- **新增 `src/transbridge/ui/operations/categorized_upload_feedback.py`**：阶段消息沿用当前计数；按文件汇总结果，最多保留 30 条非完成示例。
- **新增 `src/transbridge/ui/operations/categorized_upload_progress.py`**：每任务独立的线程安全最新进度通道，容量固定为一条，支持终态原子关闭。
- **修改 `src/transbridge/ui/operations/categorized_upload_dialog.py`**：100 ms 定时消费、任务身份隔离、显式处理计数、取消保护、相同状态免重复样式刷新、200 行/32,000 字符详情上限，以及异步窗口模态。
- **新增 `tests/application/categorized_upload/test_checkpoints.py`**：覆盖完整大文件、小文件合并、500 条/16 文件边界、取消、部分失败、保存失败后的已有成功批次、无盲重试及检查点日志。
- **修改 `tests/application/projects/test_categorized_upload.py`**：多词条/跨插件批次保存、未变对象保留与整批失败回滚。
- **修改 `tests/paratranz/test_categorized_upload_gateway.py`**：阶段/分页反馈、无正文泄露、HTTP 请求序列及取消语义保持。
- **修改 `tests/ui/operations/test_categorized_upload_bridge.py`**：适配可选阶段回调工厂，原往返/恢复用例继续覆盖。
- **新增 `tests/ui/operations/test_categorized_upload_checkpoints.py`**：真实工程两小文件一次保存/投影，磁盘身份与译文状态保真，取消后保存、保存失败及重新对照恢复、其他写入的并发编辑保护。
- **新增 `tests/ui/operations/test_categorized_upload_feedback.py`**：两万条回执按文件汇总、异常示例上限与网关阶段计数保持。
- **修改 `tests/ui/operations/test_categorized_upload_dialog.py`**：两万次真实后台进度合并、GUI 心跳、终态/旧任务隔离、取消、详情截断、窗口模态与关闭后交互恢复。
- **新增 `scripts/benchmark_categorized_upload.py`**：真实合成工程、原子存储和投影的离线前后调用模式对照；逐次验证磁盘引用、译文/状态和 Variant 身份。
- **新增 `scripts/benchmark_categorized_upload_ui.py`**：实际分类 dialog、AppContext/ProjectViewBinding 和两万行工作台下的后台提交与 Qt 心跳测量。
- **修改 `docs/requirements.md`、`docs/adr/047-opt-in-categorized-project-upload.md`、`docs/dev/paratranz-categorized-upload.md`**：同步批次契约、并发/取消恢复、窗口交互、进度与日志解释、测量限制。
- **修改 `plans/paratranz-categorized-upload/plan.md`、`docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：完成 S09 并更新本功能直接索引。
- **新增本记录**：保存真实差异和验证证据；既有增量文件不改写。

职责审查：本次涉及的生产模块均不超过 500 行，最大为工程服务 482 行；主类最多 24 个方法。缓冲、结果汇总、线程进度通道分别为独立职责，没有扩张全局生命周期或投影协议。

## 功能验证

使用现有 uv 环境和锁文件，设置 `UV_CACHE_DIR=.tmp-categorized-responsive/uv-cache`、`TMP/TEMP=.tmp-categorized-responsive`（执行时均为仓库内绝对路径）、`PYTHONIOENCODING=utf-8`、`QT_QPA_PLATFORM=offscreen`。

网关新增 6 项回归在旧实现上失败后通过；服务对象保留新增断言在旧实现上 2 项失败后通过。核心/会话首轮 84 项通过；最终相关回归 **331 passed、3 warnings，8.72 秒，退出码 0**：

```powershell
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_categorized_snapshot.py tests/contracts/paratranz/test_categorized_unbound_source_download.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_checkpoints.py tests/ui/operations/test_categorized_upload_feedback.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_paratranz_reference_metadata.py tests/ui/test_logging_setup.py tests/contracts/paratranz/test_sync_execution_logging.py tests/contracts/paratranz/test_sync_diagnostics_logging.py tests/ui/operations/test_paratranz_upload_roundtrip.py tests/contracts/paratranz/test_upload_resolution.py tests/paratranz/test_key_roundtrip.py -q --tb=short --show-capture=no
```

3 条警告为已有 SWIG 类型缺少 `__module__` 的 DeprecationWarning。中间一轮的 5 项 UI 失败仅为汇总文案改为“失败或待核实”后旧断言未更新，更新后已纳入最终通过结果。各聚焦集合重叠，不相加。

全库及新增基准脚本 Ruff 检查和格式检查通过（2142 个 Python 文件，退出码 0）：

```powershell
uv run ruff check src tests scripts/benchmark_categorized_upload.py scripts/benchmark_categorized_upload_ui.py
uv run ruff format --check src tests scripts/benchmark_categorized_upload.py scripts/benchmark_categorized_upload_ui.py
```

## 五轮本地持久化对照

环境：Python 3.12.12、Windows 11 10.0.26200，同一 uv 锁定依赖、同一两万词条合成插件，每种方式新建隔离的本地工程。输入生成、工程打开和磁盘验证不计入提交耗时。旧方式使用任务启动时的 service 副本，SHA-256 为 `6F43D6CA837D7B34FFF17829DDB22A81C378406F7C7F46187E4A9328B93CC09B`，其他模块相同。

实际执行命令：

```powershell
uv run python .agents/skills/bm-perf/scripts/benchmark_command.py --runs 5 -- D:\MyCode\TransBridge\.venv\Scripts\python.exe scripts/benchmark_categorized_upload.py --entries 20000 --receipt-count 7 --scratch .tmp-categorized-responsive/persistence-perf --baseline-service .tmp-categorized-responsive/baseline/src/transbridge/application/projects/categorized_upload.py --output-dir .tmp-categorized-responsive/persistence-perf/results
```

五轮均退出 0，以下为各轮提交耗时（秒）：

- 旧式首文件 7 条关联：82.019、61.754、57.574、57.246、56.197；中位 **57.574**，范围 **56.197～82.019**；每轮 **8 次原子保存、8 次投影发布**。
- 批次保存相同 7 条关联：33.149、13.818、51.861、13.957、13.490；中位 **13.957**，范围 **13.490～51.861**；每轮 **1 次原子保存、1 次投影发布**。
- 整个单文件 20,000 条关联：15.946、16.642、16.456、16.345、16.021；中位 **16.345**，范围 **15.946～16.642**；每轮 **1 次原子保存、1 次投影发布**。

每轮验证实际磁盘关联数量、ID、文件归属、译文/状态和 Variant 身份。该对照复现首文件逐条提交的放大成本；没有执行旧模式完整两万次写入，也不代表完整网络上传耗时。首轮与部分回归并发，且批次耗时有显著波动，不能承诺固定加速倍数；保存与投影次数的减少是稳定结果。

本轮基线及临时工程在验收后清理。后续复现可去掉 `--baseline-service`，比较同一现行 service 的旧式调用节奏与批次调用，不应将其称为旧版完整代码对照。

## Qt 响应测量

两万次后台进度突发与 GUI 心跳测试连续五轮通过（每轮测试命令耗时 0.39～0.42 秒）；该测试验证进度通道，不代表真实存储耗时。

另串行执行真实本地提交，避开上述五轮基准：

```powershell
uv run python scripts/benchmark_categorized_upload_ui.py --entries 20000 --receipt-count 7 --scratch .tmp-categorized-responsive/qt-real --output .tmp-categorized-responsive/qt-real.json
```

该测量包含真正的分类对话框后台任务、AppContext→ProjectViewBinding 投影应用、填充两万行的 Step2PreviewWidget 和窗口模态；提交后验证 GUI 的 revision 与磁盘关联。10 ms Qt 定时器在 13.708 秒测量窗口内产生 **1089 次心跳**，最大间隔 **191.059 ms**，超过 100 ms 的间隔 **8 次**，超过 1 秒 **0 次**。

真正后台提交耗时 **13.621 秒**，进程 CPU 时间 **13.656 秒**；其中原子保存 **13.196 秒**。一次保存和一次投影，磁盘 7 条关联正确。脚本输出中的 `persistence.elapsed_ms` 还包含对话框及表格准备，应使用 `phases.worker_commit` 比较实际提交时间。`projection_rebuild` 只计 store 的发布，不包含发布前快照构建，不能作为全部投影成本。

这是单轮初始 GUI 响应测量，没有旧版 Qt 对照；仍存在短暂延迟，不能声称零卡顿或严格延迟上限。

## 兼容、遗留与验证边界

- 工程 schema、远端身份、普通上传契约及依赖不变；单条执行器回调继续兼容，生产分类会话使用批次回调。
- 原子保存仍序列化完整工程并持有生命周期锁，本轮减少重复次数、阻止所属工作台手动编辑等待该锁，并提供保存阶段反馈。进一步缩短单批持久化耗时需另行评估存储协议。
- 强制结束进程可能留下已上传而尚未本地保存的最后一批，须重新对照恢复；正常取消会保存已核实事实。
- 未进行真实云端写入、人工桌面上传、全仓 pytest 或发行构建；离线 HTTP、真实本地存储与 offscreen Qt 验证不能替代真实账号验收。
- 重启应用加载新代码后重新打开分类窗口并对照。未提交、推送或发布，其他工作线的既有修改保留。
