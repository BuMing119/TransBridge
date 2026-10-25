# 分类直接上传与两种固定路径

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S11
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 需求：[FR3.4～FR3.4.6](../../../requirements.md)
- 决策：[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)
- 使用说明：[分类上传](../../../dev/paratranz-categorized-upload.md)

## 问题与结果

分类上传要求用户先对照再确认，整项目快照过期会阻断与变化无关的文件；旧文件/词条关联还可能把内容引向旧路径，或者让空项目上传遇到假冲突。用户要求选择分类后直接上传、后台实时核验，并明确同内容按完整路径归属。最终路径不增加“普通上传”或“分类上传”层：

```text
BSHeartland.esm.json
BSHeartland.esm/物品.json
BSHeartland.esm/任务/对话_[05602739].json
BSHeartland.esm/任务/任务日志.json
```

单独与批量共用来源命名；同名来源用稳定 source_id 摘要消歧。普通上传保留审阅及更新策略，分类窗口移除手动对照、确认令牌、预测变化和“整理已有云端”选项。打开分类窗口只加载本地内容，每次点击上传重新读取云端；取消、失败或部分成功后再次上传也重新核验。

文件先由固定路径确定，词条再在文件内匹配。旧关联只能在该目标内核实使用；其他路径的相同 key/原文不阻塞、不被认领或移动。目标被删除、重建或改名后按约定路径定位；旧路径保留。读取后发现他人修改目标词条时保留该项，其他独立项继续。未知写入不自动重发，下一次上传先核实。

沿用真实内容创建文件、文件/批次保存、后台 worker、100 ms 进度合并和有界详情。已有文件新增按最多 500 条一批读取完整目标索引，每个新增/更新请求前另读轻量文件元数据核验路径；不逐条重读完整大文件。客户端读取与写入之间仍有竞争窗口，没有引入服务端条件写入。

## 逐文件变化

启动时工作区已有大量未提交修改，分类上传目录也尚未跟踪。本记录基于启动副本和后续修改前副本核对，只记录本轮增量；既有 Git 差异、用户翻译数据和历史记录不纳入。未执行提交或推送。

### 分类应用与云端端口

- **新增 `src/transbridge/application/categorized_upload/direct.py`**：`execute_upload` 每次读取当前快照，选择本次文件，复用批次执行；日志关联初始与最终计划，GUI 不持有旧确认计划。
- **新增同目录 `live_targets.py`**：逐文件刷新、按 ID 核验更新、按批次检查新增，路径改变或已观察到的内容变化只影响相关目标；新建身份及时加入索引，未知结果保留。
- **新增同目录 `ownership.py`**：以完整路径限制旧引用可用范围；其他来源的过期 ID 不跨路径宣称归属。
- **修改同目录 `models.py`**：增加直接执行所需的目标读取端口及冲突类型，保留既有数据格式。
- **修改同目录 `__init__.py`**：导出直接上传入口，更新模块说明。
- **修改同目录 `classification.py`**：提取 `source_directory_names`，覆盖空来源及同名消歧，供普通/分类共用；保留插件扩展名及任务子目录。
- **修改同目录 `planner.py`**：先匹配固定路径，后匹配文件内身份/内容；旧关联不覆盖目标路径，移除跨路径迁移规划，保留参数签名兼容。
- **修改同目录 `executor.py`**：提取共用文件执行循环，接入实时目标刷新及本地目标检查日志；拒绝旧跨路径移动计划。独立审阅 API 保留，不再作为分类 GUI 的必经流程。
- **修改同目录 `checkpoints.py`、`file_creation.py`**：失败恢复提示改为再次上传自动核验，保留已导入事实、批次保存与未知结果语义。
- **修改 `src/transbridge/paratranz/categorized_upload.py`**：提供 `read_file`、`find_files`、`read_entry`、`read_file_path`；只将明确 NOT_FOUND 视为不存在，其他读取失败保留原因；沿用完整分页和写后核验。
- **修改 `src/transbridge/paratranz/pagination.py`**：仅替换三处要求手动重新对照的错误提示，分页解析行为不变。

### 工程、普通上传与 UI

- **修改 `src/transbridge/application/projects/categorized_upload.py`**：新核实批次检查内部身份唯一性，采用本次确认的文件归属；不让未选来源的旧引用否决已核实结果，不修改未选来源数据。下载发现跨来源重复旧文件绑定时拒绝该来源作用域，避免串内容。
- **新增 `src/transbridge/paratranz/ordinary_upload.py`**：普通上传适配器只读取和修改固定根目录文件；真实首条导入建文件，补齐服务端归一化后的译文/状态，拒绝已知 ID 复用，写前及回读后核验文件归属。
- **新增 `src/transbridge/ui/operations/ordinary_upload_scope.py`**：根据登记来源解析固定文件名，复用分类命名；禁用来源、缺少来源身份或歧义不回落为全项目匹配。
- **修改 `src/transbridge/application/sync/planner.py`、`upload_planner.py`**：增加显式路径作用域选项，普通 GUI 上传启用；范围外旧引用不参与目标匹配，原本地 CAS 快照不改写，其他同步调用保留原行为。
- **修改 `src/transbridge/ui/operations/production_support.py`**：`SyncRequest` 携带固定上传路径并纳入冻结目标检查。
- **修改同目录 `sync_preparation.py`、`paratranz_sync.py`**：仅上传分支采用普通路径适配器；原普通审阅及下载准备/执行链保留。
- **修改 `src/transbridge/paratranz/workflow/uploader.py`**：旧批量上传按根目录完整路径匹配，不认领嵌套目录中的同名文件；写前核验路径，清除不可信旧 wire ID，保留 HTTP 413 的确定性根目录分片。
- **修改 `src/transbridge/ui/workbench/cards/upload_card.py`**：普通单独/批量入口共用固定来源文件名。
- **修改同目录 `upload_views.py`**：普通文件名显示为只读，保留扩展名，不再允许手工改变目标。
- **修改 `src/transbridge/ui/operations/categorized_upload_bridge.py`**：移除 compare/execute 及确认 token，新增单次 `upload` 合同；每次捕获本地权威内容和选择，检查权限、目标及取消，关闭网关；非阻塞会话锁防止重复启动，强制不迁移。
- **修改同目录 `categorized_upload_dialog.py`**：只读本地分类列表直接上传，不显示旧云端预测；移除对照和整理控件，保留异步窗口模态、取消、重试、进度通道与结果汇总。
- **修改同目录 `categorized_upload_feedback.py`**：截断和恢复提示改为自动核验流程。
- **修改 `scripts/benchmark_categorized_upload_ui.py`**：适配 `load_preview + upload` 回调及单上传按钮，仍测真实工程保存与 Qt 心跳。

职责复核：新增生产模块及其他本轮修改模块均未超过 500 行/主类 30 方法阈值。既有 `upload_views.py` 为 538 行、七个独立小对话框，本轮只局部修改文件名参数及只读显示，没有新增职责；暂不扩大拆分。该文件继续聚合多个视图，后续若新增对话框或明显扩展设置，应先按对话框拆分。

### 测试

- **新增 `tests/application/categorized_upload/test_direct_upload.py`**：当前云端、局部变动、取消/重试、路径变化、读取失败、并发创建、批次读取及未知结果。
- **新增同目录 `test_fixed_paths.py`、`test_source_paths.py`**：固定路径、跨路径相同内容、旧身份、共享命名与禁用/空来源。
- **修改同目录 `test_adversarial_upload.py`、`test_diagnostics.py`、`test_execution.py`、`test_planning.py`、`test_recovery.py`**：将旧迁移断言更新为路径隔离，保留真实歧义、日志、批次保存和恢复覆盖。
- **修改 `tests/application/projects/test_categorized_upload.py`**：新核实归属优先、未选旧关联不阻塞、错误新归属仍拒绝、歧义下载保护。
- **新增 `tests/paratranz/test_ordinary_upload_adapter.py`**：导入后的状态、已知 ID 复用、未知结果与写前路径变化；独立审查时先复现四项失败后修复。
- **新增 `tests/paratranz/test_ordinary_upload_paths.py`**：旧批量根目录/嵌套目录隔离及 413 分片。
- **修改 `tests/paratranz/test_categorized_file_import.py`、`test_categorized_upload_gateway.py`**：实时目标读取、真实 multipart 导入及新路径规则；**修改 `test_single_upload_order.py`** 适配写前路径核验。
- **新增 `tests/ui/operations/ordinary_upload_support.py`、`test_ordinary_upload_paths.py`**：受控云端适配器及实际准备/执行链的两种路径隔离。
- **修改同目录 `test_categorized_upload_bridge.py`、`test_categorized_upload_checkpoints.py`、`test_categorized_upload_dialog.py`、`test_categorized_upload_feedback.py`、`test_categorized_upload_recovery.py`**：直接上传合同、空项目恢复、旧关联保留、并发目标、重复执行、批次落盘和 UI 进度。
- **修改同目录 `test_upload_mode_routing.py`**：模式入口仍区分单独/批量，分类直接上传且禁用迁移。
- **修改同目录 `test_paratranz_target_binding.py`、`test_paratranz_upload_review_request.py`、`test_paratranz_upload_roundtrip.py`、`test_sync_diagnostic_preflight.py`**：补全固定来源路径 fixture 并验证新目标，保留原审阅/绑定/诊断断言。

### 文档

- **修改 `docs/requirements.md`**：直接上传、实时核验、旧关联恢复、固定路径与不跨路径整理的验收条件。
- **修改 `docs/adr/045-original-qualified-plugin-entries.md`、`047-opt-in-categorized-project-upload.md`**：统一普通/分类的目标归属、实时读取、批次保存与并发边界，移除过时对照/迁移要求。
- **修改 `docs/dev/paratranz-categorized-upload.md`、`docs/dev/paratranz.md`、`docs/dev/INDEX.md`**：当前操作、路径例子、实现入口、错误恢复、诊断和验证范围。
- **修改 `plans/paratranz-categorized-upload/plan.md`、`docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：记录 S11 和直接相关索引；新增本文件，不改写 S01～S10 历史增量。

## 验证

使用仓库 uv 环境和锁文件，`UV_CACHE_DIR=.tmp-categorized-direct-upload/uv-cache`、`TMP/TEMP=.tmp-categorized-direct-upload`（执行时均为仓库内绝对路径），`QT_QPA_PLATFORM=offscreen`、`PYTHONIOENCODING=utf-8`。未更新依赖或锁文件。

最终主回归 **862 passed、1 failed、11 warnings，32.94 秒，退出码 1**：

```powershell
$uploadUiTests = @(Get-ChildItem tests/ui/operations -File |
    Where-Object { $_.Name -match '^test_(categorized_|ordinary_|paratranz_|sync_|upload_mode)' } |
    ForEach-Object { $_.FullName })
uv run pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz tests/contracts/paratranz @uploadUiTests tests/ui/operations/test_production_facade.py tests/ui/operations/test_production_support_authority.py tests/ui/test_logging_setup.py --ignore=tests/paratranz/test_typed_client.py --ignore=tests/contracts/paratranz/test_terms_api_contract.py -q --tb=short --show-capture=no
```

唯一失败为 `tests/ui/operations/test_paratranz_download_recovery.py::test_snapshot_failure_keeps_unsaved_local_content[legacy-io-error]`：预期 FAILED，实际 COMPLETED。将本轮修改前的 `production_support.py`、`sync_preparation.py`、`paratranz_sync.py` 用 `importlib` 加载后，单独执行同一旧下载用例仍得到相同失败（1 failed、4 deselected，0.38 秒）。该测试与旧下载持久化实现未改动，不将其描述为本轮已修复或全部测试通过。最初主回归另有四项目标绑定 fixture 缺少插件路径，补齐后六项绑定测试通过，最终联合回归中这四项也通过。

本机 HTTP 服务测试单独执行，**28 passed、1 skipped，15.48 秒，退出码 0**：

```powershell
uv run pytest tests/paratranz/test_typed_client.py tests/contracts/paratranz/test_terms_api_contract.py -q --tb=short --show-capture=no
```

这两组与主回归不重叠，合计 **890 通过、1 项基线失败、1 跳过**。跳过项要求显式配置专用真实 ParaTranz contract token/project，本轮未配置。早期扩大运行包含本机 HTTP 测试时出现 `WinError 10013`，隔离运行确认沙箱禁止本机连接；经执行权限审批后，仅上述本机 HTTP 组重跑并通过。最初包含整个 `tests/ui/operations` 的扩大运行在未完成汇总时退出 1，不计为通过。

聚焦回归及独立审查还覆盖普通首次导入状态归一化、已知 ID 复用、读取后文件改名，以及新归属被旧关联否决等问题，均先复现再修复。各聚焦集合与最终集合有重叠，不额外累加数量。

全库及更新的基准脚本静态检查、格式检查通过（2155 文件，退出码 0）：

```powershell
uv run ruff check src tests scripts/benchmark_categorized_upload_ui.py
uv run ruff format --check src tests scripts/benchmark_categorized_upload_ui.py
```

真实 Qt/原子保存的小规模冒烟：

```powershell
uv run python scripts/benchmark_categorized_upload_ui.py --entries 30 --receipt-count 7 --scratch .tmp-categorized-direct-upload/persistence-ui-smoke --output .tmp-categorized-direct-upload/persistence-ui-smoke/report.json
```

30 行工作台中 7 条关联得到 1 次原子保存、1 次投影发布；落盘关联和 UI 修订验证通过，窗口保持模态。10 ms 心跳最大间隔 12.938 ms、worker 提交 36.498 ms。本轮只是新回调合同的集成冒烟，不是两万条性能复测，不据此承诺大型工程延迟。S09 的大样本数据仍保留在原记录。检查本轮差异、文档链接和职责大小后，移除本轮专用临时基线、缓存及 QA 输出。

## 兼容性与遗留项

工程 schema 和既有 key 不变；关联仍按文件或批次保存。普通文件名现在保留插件扩展名，例如 `BSHeartland.esm.json`；旧 `BSHeartland.json` 或其他路径文件保留，不自动迁移。413 分片使用同一根目录下稳定后缀。普通单独/批量、分类单独/批量共用来源命名。

分类 GUI 的 compare/execute 合同改为直接 upload；核心独立审阅接口保留。旧 `migrate` 参数保留签名，但不能触发跨路径移动。发现历史下载绑定跨来源重复时，需先重新上传该来源恢复固定目标绑定，再下载，避免把错误来源内容带入工程。

遗留：上述一项旧下载恢复失败尚未修复；真实账号的服务端导入和更新尚未验收。未运行全库 pytest、构建/安装包或两万条性能复测。未写入真实 ParaTranz 项目；不承诺消除客户端 GET/写入之间无法原子化的并发窗口。
