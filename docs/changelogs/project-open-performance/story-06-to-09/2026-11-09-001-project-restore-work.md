# 工程恢复重复计算、后台视图证据与启动包预检

- 日期：2026-11-09。
- Epic：`project-open-performance`；Story：S06～S09。
- 关联：[实施计划](../../../../plans/project-open-performance/plan.md)、[ADR-025](../../../adr/025-project-window-launch-and-open-progress.md)、[ADR-035](../../../adr/035-authoritative-project-mutations-and-recoverable-save.md)。
- 状态：本地实现、分组及扩大回归、关闭回调复验与五轮性能对照完成；扩大回归的 7 个既有失败已在本轮起点源码复现。全流程 200 ms 心跳预算未达到。
- 增量归属：以本轮开始时保存的源码副本及任务文件清单对照，工作区原本已有更新器、导入/导出和打开文件等大量未提交修改。本记录只包含以下恢复/启动职责，不把全部 Git diff 归入本轮；未提交或推送，未修改依赖、锁文件、安装包或数据格式。

## 用户可见结果

普通工程打开减少相同 Variant 的严格解析和无变化重建；首次视图一致性准备移入 worker，GUI 安装继续核对真实内容。开始页先显示状态，再后台读取工程目录与可恢复任务；指定 `.transbridge` 工程包启动时直接进入包导入，避免先完整恢复旧工程。归档冲突提示只读取所需事实，真正导入仍重新完整校验。

相同真实工程副本五次重载，总打开时间中位数由 40.10 秒降至 36.09 秒，约减少 10.0%；GUI 安装由 2.10 秒降至 0.49 秒，约减少 76.7%。样本波动较大，最大事件循环间隔中位数反而由 2.67 秒变为 2.76 秒，**不能宣称秒开、无卡顿或已满足 200 ms 响应预算**。完整 MainWindow 启动性能不在本次实测覆盖内。

## 生产文件增量

### S06：普通恢复与来源解码

- 修改 `src/transbridge/persistence/v2/repository.py`：当前 schema 版本检查后将同次严格解析的 document 交给 decode；写入前仅需版本时读取已有不可变证据。增加仅限 prepared Variant 路径的 `_read_validated_version`，仍取得 read lease 并读取实际磁盘字节。
- 修改 `src/transbridge/persistence/v2/validated_reads.py`：证据保留 `RecordVersion`，增加版本查询/解码接口，避免只需 revision/hash 时重建 DTO；完整公开 read 继续返回独立可变 DTO。证据只限同次操作及精确 repository/ref/raw。
- 修改 `src/transbridge/persistence/v2/project_record_guard.py`：新增冻结的 `RecordVersion`，供 activation 检查使用；既有 revision/hash 冲突和 exact preimage 验证保持。
- 修改 `src/transbridge/persistence/v2/project_activation_journal.py`：未写入记录验证当前版本及 preimage 后直接省略，不再为相同 previous/target 两次 base64 编码；实际写入、manifest 读回与恢复继续验证，减少只需版本信息时的解析。
- 修改 `src/transbridge/persistence/prepared_project_read.py`：通过新鲜磁盘字节核对已准备 Variant，保留嵌套可变 metadata 检查；记录 source relations/baselines，仅在原 snapshot、baselines、hydrations、来源/关系一致且没有缺失输入时复用完整来源覆盖结论。
- 修改 `src/transbridge/persistence/project_lifecycle_loader.py`：消费 prepared Variant 的版本证据；materialization 仅在增加来源/词条或补齐 inferred field 时创建新 Snapshot。无变化返回原对象，并据此保留 clean 状态；快照恢复、真实补齐或输入变化继续重算来源缺失信息。
- 修改 `src/transbridge/persistence/v2/schema.py`：精确内置 JSON 类型采用直接通过路径，联合类型、浮点整数、子类和非法值仍走原 Draft 校验；来源 namespace 的唯一性集合也用于后续语义判断，避免列表成员检查。
- 修改 `src/transbridge/persistence/source_artifact_codec.py`：单次 artifact decode 内最多分别复用 64 个冻结 namespace/revision 值；只有 exact `str`/`int` 进入缓存，构造器验证保留，不共享复杂可变 metadata，不跨读取缓存。
- 修改 `src/transbridge/persistence/source_artifact_store.py`：JSON object pairs 先由内置 dict 构造，无重复时直接返回；发现数量差异后定位并报告首个重复键，嵌套重复键仍拒绝。

### S07：后台一致性准备与 Qt 安装

- 修改 `src/transbridge/ui/projection_consistency.py`：新增一次性 `PreparedProjectionConsistency`，后台建立首次完整证据；acknowledge 处理提交后不同的 authority rows，take 仅交付匹配 entries 与 collections 的证据。
- 修改 `src/transbridge/ui/project_view_binding.py`：prepared state 可携带上述证据；安装在锁内一致切换 cache/slots，随后正常 `diverged` 仍检查 journal 和 mutable refs。后台保存同时捕获 cache、slots、installed snapshot，安装交错时重新核对，防止旧比较污染新证据。
- 修改 `src/transbridge/ui/coordinators/project_open_operation.py`：在 worker 中统一准备 slots、view state、一致性证据并在提交后 acknowledge；GUI 接收已准备 payload，保持原提交和失败处理边界。
- 修改 `src/transbridge/ui/source_hydration.py`：来源注册 identity/reference location 已匹配时保留原 hydration，避免无变化 dataclass replace 再次检查全部来源 entries。

### S08～S09：启动目录与归档边界

- 新增 `src/transbridge/ui/shell/start_center_directory.py`：读取工程目录和任务恢复为纯数据结果；loader 通过现有 ApiWorker registry 持有单个 worker，将连续刷新合并到最新 revision，关闭中不投递或启动后继。
- 修改 `src/transbridge/ui/shell/start_center_controller.py`：同步渲染缓存/空目录并请求后台刷新；离开开始页、切入创建流程、过期 revision 或关闭时拒绝旧结果，任务恢复失败仍提供诊断。当前工程标记继续依据窗口自身身份。
- 修改 `src/transbridge/ui/coordinators/project_coordinator.py`：`init_workspace` 增加可选 `restore_active`，始终恢复 workspace/filter，明确启动包意图时跳过旧活动工程恢复。
- 修改 `src/transbridge/ui/main_window.py`：指定包且没有显式 project path 时采用上述分支；两个显式参数同时存在时优先 project path。启动导入等待逻辑在 close pending 时退出；保留此前已有更新器接线。
- 新增 `src/transbridge/persistence/project_archive_reader.py`：从 archive service 抽取容器、manifest、Project/Variant/history 和来源成员读取验证；current schema 直接验证，旧 schema 才 migration。路径、重复成员、fingerprint、归属和 source artifact 验证保持。
- 修改 `src/transbridge/persistence/project_archive.py`：公共 `inspect_import` 继续完整验证；新增 `inspect_import_conflicts` 供 GUI 读取 portable 包冲突事实，legacy 包沿原完整准备路径。`import_project` 重新打开包并完整验证，不复用预检 draft，不凭文件时间/大小信任输入；发布与回滚保持原协议。
- 修改 `src/transbridge/ui/coordinators/project_transfer_coordinator.py`：GUI 预检接入 `inspect_import_conflicts`，真正导入保留完整校验。冲突策略与打开提示回调在入口及 modal 返回后检查关闭状态，关闭中不弹后继对话框、不启动导入或打开新工程；已开始的导入事务正常完成，成果保留。

## 测试与文档文件增量

- `tests/persistence/v2/test_project_open_reuse.py`：断言普通未变更打开的 Variant 只解码一次且不编码无用 journal，保留原 Snapshot；扩展提交前真实外部修改和新增默认词条导致来源覆盖重算。
- `tests/persistence/v2/test_project_activation_transactions.py`：补充未写入记录在 preimage 捕获间发生修改仍被拒绝。
- `tests/persistence/v2/test_schema_execution.py`：类型 × 值矩阵比较快速路径与原 Draft 的校验结果。
- `tests/persistence/test_source_artifact_store.py`：验证 immutable scalar 只在一次解码中复用、metadata 独立、bool/float/负 revision 拒绝和嵌套重复 JSON 键拒绝。
- `tests/ui/test_project_open_operation.py`：首轮全量一致性工作在 worker，提交后独立 acknowledged projection 仍正确处理。
- `tests/ui/test_projection_consistency_cost.py`：安装前 journal 编辑、嵌套 metadata 修改及不同 authority/collection 输入不被 prepared evidence 隐藏。
- `tests/ui/test_projection_check_during_open.py`：后台比较与同 token 安装交错时，重试采用新 cache 和 snapshot。
- `tests/ui/test_project_source_hydration.py`：已绑定 hydration 不触发全量重验，来源 identity/reference 保持。
- 新增 `tests/ui/test_start_center_directory.py`：慢目录读不阻塞初始渲染、刷新合并、GUI 投递、导航/关闭后迟到结果不渲染。
- `tests/ui/test_start_center_guided_project.py`、`tests/ui/test_start_center_window_isolation.py`：适配抽取的目录纯数据边界，保留恢复失败诊断及多窗口当前工程语义。
- `tests/ui/test_v2_project_transfer.py`：实际 MainWindow 启动包导入成功/取消/失败及显式 project path 优先，保留 workspace/filter；关闭回归覆盖预检完成、身份确认返回、命名返回、发布完成和打开确认返回五个边界。
- `tests/ui/test_destructive_confirmation_defaults.py`：测试替身采用新的冲突预检接口，保留默认取消断言。
- `tests/persistence/v2/test_project_archive.py`：current-schema 不迁移、完整 inspect/import 保持、预检后包被修改不能越过导入校验。
- `plans/project-open-performance/plan.md`、`plans/INDEX.md`：接续已有 S01～S05，记录 S06～S09 实现、测量与明确的未达预算；本日志及 `docs/changelogs/INDEX.md` 仅增加本轮记录。没有把原用户其他文档改动算入本轮。

## 验证证据

### 已完成分组

分组有重叠，以下数字不能相加为独立测试总数。测试均使用现有 uv 环境与锁文件；本地 Windows 的 pytest `mkdir(0700)` ACL 与长临时目录名导致的环境失败，通过任务专属 wrapper 在该临时根内采用继承 ACL 和短 basename 处理。没有更改生产文件系统策略、pytest 公共配置或失败断言。

- backend 最终完整分组：561 passed，35.10 秒；覆盖 persistence/v2、来源恢复、loader 与相关应用契约。
- 首次视图与保存一致性 UI 分组：80 passed。
- artifact codec/store 相关分组：71 passed。
- 归档入口分组：117 passed。
- entry UI/CLI 最终分组：133 passed、3 个既有失败；包含关闭 guard 的五个边界用例。之前 128 passed 是补充关闭用例前结果，不累计。
- 主会话扩大应用/投影/GUI 分组：409 passed、1 skipped、2 个既有失败。
- 主会话扩大项目/投影/开始页 20 文件分组：162 passed、2 个既有失败。
- 两轮只读 QA 未发现其他已证实的可达数据安全回归；审查识别的导入关闭后继问题限定在导入回调修复，不修改全局 foreground 行为。

backend 的 561 项以 `uv run --offline --no-sync python -` 启动任务专属 stdin wrapper，实际 pytest 参数为 `tests/persistence/v2 tests/application/projects/test_v2_loader.py tests/persistence/test_strict_json_pairs.py -q -p no:cacheprovider --basetemp=.tmp-project-open-opt-20261109/d`。3 条 SWIG 弃用警告，不影响通过结论。其余主要命令如下；`run_pytest.py` 为上述仅限任务临时根的 ACL/短路径适配，`q`/`u`/`qa-entry-*` 是不同测试临时目录。运行时 `UV_CACHE_DIR` 指向任务 cache，Qt 采用 offscreen。

```powershell
uv run --offline --no-sync pytest tests/ui/test_project_open_operation.py tests/ui/test_project_view_installation.py tests/ui/test_projection_check_during_open.py tests/ui/test_projection_consistency_cost.py tests/ui/test_project_source_hydration.py tests/ui/test_content_projection_merge.py -q -p no:cacheprovider --basetemp=.tmp-project-open-opt-20261109/qa-ui-root
uv run --offline --no-sync python -B .tmp-project-open-opt-20261109/run_pytest.py k tests/persistence/test_source_artifact_store.py tests/persistence/v2/test_source_artifact_archive.py tests/persistence/v2/test_saved_project_sources.py -q
uv run --offline --no-sync python .tmp-project-open-opt-20261109/run_pytest.py qa-entry-s09 tests/persistence/v2/test_project_archive.py tests/persistence/v2/test_legacy_project_archive.py tests/persistence/v2/test_source_artifact_archive.py tests/persistence/v2/test_archive_business_assets.py tests/persistence/v2/test_source_update_archive.py tests/security/test_archive_attack_corpus.py -q --tb=short
uv run --offline --no-sync python .tmp-project-open-opt-20261109/run_pytest.py qa-entry-s08-final tests/ui/test_v2_project_transfer.py tests/ui/test_start_center_directory.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_window_isolation.py tests/ui/test_main_window_coordinators.py tests/ui/test_main_window_shell.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_destructive_confirmation_defaults.py tests/ui/ux/test_current_user_journeys.py tests/integration/entrypoints/test_cli_entrypoint.py -q --tb=short
uv run --offline --no-sync python .tmp-project-open-opt-20261109/run_pytest.py q tests/application/projects tests/application/projections tests/integration/gui tests/ui/test_background_gui_operations.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_main_window_shell.py tests/ui/test_v2_project_transfer.py tests/ui/test_prepared_entry_projection.py -q -m 'not llm'
$projectUiTests = rg --files tests/ui | Select-String -Pattern '^tests.ui.test_(project_|projection_|start_center)' | ForEach-Object { $_.Line }
uv run --offline --no-sync python .tmp-project-open-opt-20261109/run_pytest.py u @projectUiTests -q -m 'not llm'
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
```

Ruff check 全部通过；format check：2454 files already formatted。原始数据保护通过直接字节比较验证：本轮 seed 对应的 236 个原 data 文件全部一致，没有引入新哈希来代替直接比较。另以任务起点副本核对 src/tests/scripts/plans/docs 及 AGENTS/pyproject/uv.lock，排除本任务清单与日志索引后的 3989 个文件逐字节一致，未覆盖原有非任务改动。

保存本记录中的数值后，核验所有权标记及绝对目标路径，已删除任务临时根与本轮创建的三个外部 Temp 残留，并确认不存在。普通清理受到 Windows ACL 拒绝后，通过批准的提权清理完成；未删除用户已有临时目录。文中临时脚本/数据路径仅记录实际执行历史，脚本、副本及测量 JSON 均不作为永久产物提交。最终 `git -c core.safecrlf=false diff --check` 通过。

### 既有失败与基线复现

下列 7 个不同用例在当前扩大回归中失败，随后在本轮开始时保存的源码与测试副本中以相同错误复现；未修改无关夹具来制造全绿。

- `tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_commits_initial_states_without_second_variant_command`：mock `add_source` 结果缺少 `source_id`。
- `tests/ui/test_main_window_coordinators.py::test_authoritative_source_import_preserves_each_same_key_initial_state`：相同 mock 缺少 `source_id`。
- `tests/ui/ux/test_current_user_journeys.py::test_current_parse_dialog_cancel_has_no_worker_or_message`：QMainWindow 替身没有 `context`。
- `tests/ui/test_background_gui_operations.py::test_authoritative_hydrations_restore_all_sources_without_reparsing`：fake projection 没有 `values`。
- `tests/ui/test_background_gui_operations.py::test_authoritative_hydration_failure_is_visible_and_keeps_existing_slots`：相同 fake projection 缺少 `values`。
- `tests/ui/test_project_creation_transition.py::test_new_project_replaces_all_old_slots_and_remains_saveable[2]`：既有断言要求的来源标识顺序与实际顺序不符。
- `tests/ui/test_start_center_multi_plugin.py::test_choose_multiple_plugins_creates_one_project_and_loads_every_collection`：来源 identity 与历史路径断言不一致。

复现分两次执行（5 个与 2 个 nodeid），cwd 为 `.tmp-project-open-opt-20261109/before`，使用原仓库 uv 环境并显式指定 before 源码。命令形态为 `uv run --project <仓库根> --offline --no-sync python <任务根>/run_pytest.py qa-entry-baseline-five -o pythonpath=<before/src> <前五个完整nodeid> -q --tb=short`，第二次目录名 `qa-entry-baseline-two`，传入最后两个 nodeid。路径已泛化，结果分别 5/5 和 2/2 同样失败；不把它们归因于本轮优化。

### 同输入 before/after 性能

Windows、Python 3.12.12、离屏 Qt，复用已有 `scripts/benchmark_project_open.py::measure_project`。before 加载本轮开始时的源码副本，after 加载修改后的源码；两者从相同只读 seed 复制为独立数据根，runner 拒绝任务目录以外写入和网络连接。执行入口：

```powershell
uv run --offline --no-sync python -B .tmp-project-open-opt-20261109/run_perf.py before
uv run --offline --no-sync python -B .tmp-project-open-opt-20261109/run_perf.py after
```

实际覆盖 `ProjectCoordinator + AppContext + Step2`，活动 Variant 约 96.2 MB、96751 条、80 来源；当前显示来源为 119 行，不能把报告中的 `entries=119` 当成整个工程规模。每组一次冷开、五次重载全部成功；下列秒数保留三位小数，顺序为实际采样顺序。

```text
before reload total   = [36.111, 78.364, 47.493, 40.104, 39.829]
after  reload total   = [59.479, 31.485, 33.884, 37.477, 36.092]
before reload install = [ 2.098,  5.546,  2.030,  2.101,  2.273]
after  reload install = [ 0.434,  0.422,  0.930,  0.489,  1.326]
before reload gap     = [ 2.111,  5.579,  2.414,  2.669,  2.739]
after  reload gap     = [ 3.812,  2.383,  2.635,  2.757,  2.828]
before reload prepare = [23.423, 41.807, 34.108, 25.507, 25.910]
after  reload prepare = [49.428, 21.620, 23.052, 25.749, 24.788]
before reload activate= [10.545, 30.932, 11.323, 12.433, 11.612]
after  reload activate= [ 9.579,  9.415,  9.838, 11.207,  9.911]
before reload paint   = [36.111, 78.364, 47.493, 40.103, 39.828]
after  reload paint   = [59.479, 31.485, 33.884, 37.477, 36.091]

before cold: total=35.212, prepare=23.666, activate=10.156, install=1.301, gap=1.591, paint=35.195
after  cold: total=81.528, prepare=53.278, activate=28.128, install=0.020, gap=2.453, paint=81.527
```

五轮重载总时间中位 40.1036 → 36.0918 秒，范围分别 36.111～78.364 与 31.485～59.479 秒；GUI 安装中位 2.10137 → 0.488574 秒。准备阶段中位 25.910 → 24.788 秒，activation 中位 11.612 → 9.838 秒。prepare 包含严格校验、from_dto 和来源恢复等多个阶段，不能把全部时间归给 source artifact。

before 第二次重载和 after 冷开/第一次重载明显较慢；保留全部样本，不剔除后重算结论。单次冷开未改善，不足以声称启动加速。重载中位下降是本次样本观察，无法保证每次打开都缩短相同比例。

最大心跳间隔中位 2.669 → 2.757 秒，after 范围 2.383～3.812 秒，远高于既有 200 ms 预算。GUI 安装缩短不等于全流程响应性完成；worker CPU/GIL、对象释放和其他来源恢复成本仍需进一步定位。profile 记录只用于定位调用热点，不能把带 profiler 的耗时当作普通 wall time；组件微基准也不替代这组完整路径对照。

### 来源 artifact 组件诊断

对同一隔离 seed 中两个 artifact，交替执行 before/after 各五次 `ProjectSourceArtifactStore.get`。开始计时前确认两份恢复对象完全相等；每次计时包含磁盘读、摘要、严格 JSON parse 和 typed decode，GC 在计时前单独执行。任务命令为 `uv run --offline --no-sync python -B .tmp-project-open-opt-20261109/qa-codec/compare.py 8000000 18000000`，脚本仅用于本轮诊断，不作为永久 runner 交付。

```text
A: manifest=5,923,755 bytes, hydration=2246 rows
before = [0.2876, 0.3716, 0.3469, 0.2931, 0.2844] seconds; median=0.2931
after  = [0.2128, 0.2034, 0.2153, 0.2039, 0.2249] seconds; median=0.2128
B: manifest=21,611,707 bytes, hydration=8107 rows
before = [1.2639, 1.1064, 1.1154, 1.1185, 1.0771] seconds; median=1.1154
after  = [0.9851, 0.9875, 1.1678, 0.9619, 1.1457] seconds; median=0.9875
```

这两组是已验证输出等价的组件诊断，不能外推为全部来源或完整工程打开的加速比例。codec 71 项回归耗时 3.99 秒，含 3 条 SWIG 弃用警告。

## 兼容、遗留与未执行

- 未新增 schema、artifact ABI、依赖或全局缓存。公开 repository read 的 DTO 隔离、旧版本迁移、真实磁盘冲突、preimage、恢复和导入回滚继续保留。
- 归档 lightweight conflict inspection 不等于包完整有效；完整 `inspect_import` API 与 `import_project` 重验仍保留。预检与导入之间文件可以变化，导入以当时重新读取并完整验证的内容为准。
- 仍完整加载权威 Variant 与来源 artifacts。本轮没有按来源懒加载；它需要独立定义 source readiness、取消/失效、保存完整性及写回/导出 capability gates 的架构方案。
- 未测完整 MainWindow 性能、安装包启动、跨工程 A→B→A 性能、峰值内存或可见屏幕实际绘制时间；离屏 viewport paint 只代表事件派发完成。启动与关闭路径具有功能回归证据，不把它当成完整窗口性能证据。
- 性能响应预算仍未达成。后续优先继续按阶段测量必要 schema/来源解码和对象生命周期，再决定是否推进按来源懒加载；不以新增全局 GC 策略或静默跳过校验规避问题。
