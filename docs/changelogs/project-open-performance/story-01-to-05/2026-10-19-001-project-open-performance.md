# 本地工程后台打开、按需表格与旧记录保真

- 日期：2026-10-19
- Epic：project-open-performance；Story：S01～S05。
- 关联：[实施方案](../../../../plans/project-open-performance/plan.md)、[ADR-025](../../../adr/025-project-window-launch-and-open-progress.md)。
- 起点：HEAD `a75548e` 加任务开始时已有的大量未提交 UI 修改。本记录按任务开始时的文件哈希和源码副本识别增量，不把全部 Git diff 归为本次工作。没有提交、推送、依赖或锁文件修改。

## 用户可见结果

当前窗口打开工程的来源解析、候选构建和磁盘提交均在 worker；GUI 一次安装最终项目视图，打开期间保留进度反馈。翻译表改为按需取单元格内容的模型，避免一次创建或释放每词条七个 QTableWidgetItem。编辑、排序、选择、搜索、标签和术语预览保留原行为。

合法旧记录不再因可选字段规范化而误报 `VARIANT_LOAD_STALE`。真实磁盘 revision/hash 冲突仍被拦截。历史来源 namespace 仅通过可验证且无歧义的内容哈希恢复；无法对应的已存词条进入只读恢复，完整保留数据，不能靠空译文特判删除。

## 逐文件增量

以下均为相对任务开始状态的修改；标为“新增”的文件由本任务创建。

### S01～S02：持久化与准备

- `src/transbridge/persistence/project_lifecycle_loader.py`：`_load_variant()` 普通打开复用同一 LoadedRecord，保留原字节摘要；快照路径仍单独核对正式版本。`_materialize()` 拒绝普通打开丢失来源或非 tombstone 词条。
- `src/transbridge/persistence/current_project.py`：提前读取存档指纹，`_load_baseline()` 按内容哈希恢复 namespace，拒绝歧义；缺失身份进入 recovery。`activate()` 增加候选视图准备和旧状态签名保护，失败时恢复基线前像。
- `src/transbridge/persistence/v2/baselines.py`：新增基线目录前像捕获/条件恢复，避免失败打开留下部分来源，也避免覆盖之后的更新。
- `src/transbridge/persistence/v2/repository.py`：同次读取、同一锁内复用已验证的当前格式记录；恢复、迁移路径继续重读，未引入跨操作缓存。
- `src/transbridge/persistence/v2/project_activation_journal.py`：`write=False` 不再重复验证同一 payload，保留前像、revision/hash 和故障恢复检查。
- `src/transbridge/persistence/v2/variant.py`：提供专门的 label_library 复制导出，避免只为标签构造全量 DTO。
- `src/transbridge/application/projections/adapters.py`：项目投影使用标签专用导出，保留嵌套数据隔离。
- `src/transbridge/application/projects/transition_preparation.py`（新增）：集中候选加载、临时 leases 和回调失败清理，不产生第二套事务。
- `src/transbridge/application/projects/lifecycle.py`：委托候选准备，处理旧状态签名和准备回调，继续负责 token 消费及提交语义。
- `src/transbridge/application/projects/gui_facade.py`：透传可选候选准备与旧状态签名，保持既有调用可用。

### S03：后台打开与一致发布

- `src/transbridge/ui/coordinators/project_open_operation.py`（新增）：独立负责打开 worker、阶段进度、结果安装、失败隔离与收尾。
- `src/transbridge/ui/coordinators/project_coordinator.py`：委托完整打开操作；worker 已退出但 GUI 结果未安装时仍保持忙碌。
- `src/transbridge/ui/project_view_binding.py`（新增）：集中投影订阅、打开期间排队、最终状态/槽位/标签一次安装及过期检查。
- `src/transbridge/ui/context.py`：保留 facade 接口并委托 ProjectViewBinding；打开或失败隔离期间拒绝编辑和标签写入。
- `src/transbridge/ui/source_hydration.py`：纯数据 detached 槽位一次构建，避免逐来源重建身份索引。
- `src/transbridge/ui/entry_projection.py`：共享不可变投影快照到条目状态的转换，保留公开 apply 的新对象语义。
- `src/transbridge/ui/main_window.py`：根据已安装视图判断操作状态，保持打开/失败隔离期间的控件与关闭语义。
- `src/transbridge/ui/shell/intent_composition.py`：命令和快捷入口检查打开/失败状态，避免 GUI 同步等待 lifecycle 锁。
- `src/transbridge/ui/coordinators/source_update_coordinator.py`：来源更新直接入口也检查打开/失败状态。
- `src/transbridge/ui/shell/window_lifecycle.py`：关闭等待 worker 与 GUI 安装安全终态，取消关闭时不误启用隔离视图。
- `src/transbridge/ui/tools/ai_translator/task_recovery_prompt.py`：打开期间延期恢复扫描，避免 GUI 查询 lifecycle 锁。
- `src/transbridge/ui/tools/ai_translator/task_registry.py`：恢复任务目录从已安装视图取得。
- `src/transbridge/ui/dialogue/editing.py`：打开期间拒绝提交并保留编辑草稿。

### S04～S05：按需表格与基准

- `src/transbridge/ui/workbench/translation_model.py`（新增）：QAbstractTableModel 按需返回显示/编辑/预览角色，编辑只发意图；拒绝过期及外来索引，session 替换验证身份和顺序。
- `src/transbridge/ui/workbench/translation_table.py`：改为 QTableView，移除逐项对象与分批定时器；保留完整身份的选择、勾选、排序、编辑和滚动恢复。
- `src/transbridge/ui/workbench/translation_table_sorting.py`：表头提示使用模型 API。
- `src/transbridge/ui/workbench/step2.py`：安装期间合并重复刷新；菜单、双击和编辑接入模型接口，完整行数即时可查询。
- `src/transbridge/ui/workbench/inline_edit.py`：从编辑意图进入权威提交，先捕获草稿；失败不提前污染业务对象。
- `src/transbridge/ui/workbench/entry_refresh.py`：使用公开 session 替换和 QModelIndex 定位，单行刷新保留持久索引。
- `src/transbridge/ui/workbench/filters_presenter.py`：无筛选时复制引用，按需建立标签查询。
- `src/transbridge/ui/workbench/filters_view.py`：分类按本次 context 缓存，避免重复推导。
- `scripts/benchmark_project_open.py`（新增）：复现真实 ProjectCoordinator + AppContext + Step2 流程；合成输入或真实工程副本，一次冷开和五次重载，分阶段记录安装、数据就绪、视口 Paint 与心跳。原用户文件仅校验哈希，不激活。

### 回归测试

- `tests/application/projections/test_project_label_projection.py`（新增）：标签导出隔离与无需全量 DTO 的投影路径。
- `tests/application/projects/test_transition_preparation.py`（新增）：准备回调、过期旧状态决定与临时资源释放。
- `tests/persistence/v2/test_project_open_records.py`（新增）：可选字段旧记录、精确摘要、快照归属及真实冲突。
- `tests/persistence/v2/test_project_open_activation.py`（新增）：旧记录成功激活、真实外部改动拒绝与基线回滚。
- `tests/persistence/test_project_source_identity.py`（新增）：哈希恢复旧 namespace；歧义、冲突选项、来源变化、缺失空/非空译文词条均保真恢复。
- `tests/persistence/test_project_recovery.py`：调整注入 loader 夹具，验证恢复和保存记录保持。
- `tests/ui/test_project_open_operation.py`（新增）：worker 完整打开、候选失败、安装失败、重入与关闭收尾。
- `tests/ui/test_project_open_guards.py`（新增）：命令、编辑、恢复扫描、关闭和过期状态保护。
- `tests/ui/test_project_view_installation.py`（新增）：一致发布、一次最终刷新与较新投影隔离。
- `tests/ui/test_project_source_hydration.py`（新增）：完整 EntryKey、多来源、脱离控件的数据构建与原公开接口契约。
- `tests/ui/test_background_gui_operations.py`：断言来源准备和激活都离开 GUI 线程。
- `tests/ui/test_project_recovery.py`：补全测试 host 的正式投影端口，恢复路径仍保持当前工程。
- `tests/ui/test_translation_table_model.py`（新增）：20k 替换、权威编辑、鼠标/键盘勾选、持久索引、过期/外来索引和 session 身份检查。
- `tests/ui/test_translation_table_reading.py`：按模型读取原文、译文和完整提示，保留大文本行为。
- `tests/ui/test_translation_table_sorting.py`：排序映射、选择/编辑/滚动稳定性迁至模型 API。
- `tests/ui/test_step2_incremental_rendering.py`：从分批 item 假设迁至全量模型可查询和按需呈现。
- `tests/ui/test_entry_refresh.py`：增量刷新和编辑定位使用模型，保留单词条更新契约。
- `tests/ui/test_inline_edit_recovery.py`：失败草稿和重试的权威提交验证。
- `tests/ui/test_inline_edit_shutdown.py`：编辑草稿、保存失败与关闭保护。
- `tests/ui/test_translation_reset.py`：重置翻译动作通过模型验证结果。
- `tests/ui/test_accessibility_contracts.py`：表格键盘和可访问契约改用 QModelIndex。
- `tests/ui/test_modern_workbench_visual_shell.py`：保留工作台外观断言，迁移表格读取。
- `tests/ui/test_original_match_projection.py`：完整原文限定身份在模型中的投影和操作。
- `tests/ui/test_v2_variant_hydration.py`：版本挂载后的模型读取与状态保持。
- `tests/ui/test_dialogue_authority.py`：对话编辑的权威状态及打开保护。
- `tests/ui/test_dialogue_consistency.py`：对话和表格一致性迁至模型读取。
- `tests/ui/test_dialogue_editor.py`：编辑交互与表格结果保持。
- `tests/ui/test_workbench_slices.py`：各切片与模型交互回归。
- `tests/ui/test_workbench_story07.py`：工作台选择、编辑与数据呈现契约。
- `tests/ui/test_workbench_theme_migration.py`：模型外观与主题回归；测试结束恢复 QApplication 全局主题，计时前清理前序测试 DeferredDelete，未放宽 250 ms/196 px 阈值。
- `tests/ui/tools/test_ai_version_snapshots.py`：版本快照回到工作台后的模型结果。
- `tests/ui/workbench/test_terminology_profile_switching.py`：方案切换的模型提示更新。
- `tests/ui/workbench/test_terminology_projection_consistency.py`：术语预览和权威译文一致性。
- `tests/performance/test_workbench_story07_performance.py`：新增 20k 行项目替换每次少于 200 ms 的回归。
- `tests/scripts/test_benchmark_project_open.py`（新增）：runner 数据保护、协议、模型及旧表适配、独立绘制观测。

### 文档与索引

- `docs/adr/025-project-window-launch-and-open-progress.md`：更新后台提交、一致安装、模型呈现、失败隔离及身份保真契约；保留“提议”状态。
- `plans/project-open-performance/plan.md`：把已完成 S01～S05 写回对应正文，删除已被模型替代的首批延后方案，明确兼容和性能边界。
- `plans/INDEX.md`：仅移动并更新该 Epic。
- 本文件（新增）与 `docs/changelogs/INDEX.md`：记录此次增量，不改写历史日志。

## 验证与性能

### 功能回归

所有命令使用现有 uv Python 3.12.12 环境；Qt 使用 `QT_QPA_PLATFORM=offscreen`。为避免受限进程无法访问 pytest 临时目录，测试使用经自动批准的执行环境及任务专属 `--basetemp`，没有改用户目录权限。

持久化、项目应用和投影组合：

```powershell
uv run pytest tests/application/projects tests/persistence/v2 tests/application/projections tests/persistence/test_project_recovery.py tests/persistence/test_project_source_identity.py tests/integration/bootstrap/test_persistence_v2_projection_composition.py -q -p no:cacheprovider --basetemp .tmp-project-open-01a152cf/final-persistence-identity
```

初轮 497 passed、2 failed；两项归档失败为 WinError 3 的深层临时路径。缩短临时根后 `test_project_archive.py` 得到 47 passed、1 setup error（WinError 5 原子替换拒绝）；再独立复验两项原失败及全部危险归档名参数：

```powershell
uv run pytest tests/persistence/v2/test_project_archive.py -q -k 'plugin_strings_and_sqlite_wal_assets_survive_portable_roundtrip or same_name_conflict_can_be_renamed_without_changing_archive_identity or rejects_unsafe_or_reserved_members_before_writing' -p no:cacheprovider --basetemp .tmp-project-open-01a152cf/a2
```

18 passed。原 499 项均取得通过证据，未修改归档业务逻辑或降低断言。之后新增空词条保留用例的结果见收尾检查。

模型及所有直接/间接工作台消费者：

```powershell
uv run --no-sync pytest tests/ui/test_translation_table_model.py tests/ui/test_translation_table_sorting.py tests/ui/test_translation_table_reading.py tests/ui/test_workbench_story07.py tests/ui/test_workbench_slices.py tests/ui/test_entry_refresh.py tests/ui/test_inline_edit_recovery.py tests/ui/test_inline_edit_shutdown.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_authority.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_translation_reset.py tests/ui/test_original_match_projection.py tests/ui/test_v2_variant_hydration.py tests/ui/test_accessibility_contracts.py tests/ui/test_modern_workbench_visual_shell.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/test_workbench_theme_migration.py tests/ui/characterization/test_workbench_contract.py tests/ui/test_layout_stability.py tests/ui/foundation/test_accessibility_theme_matrix.py tests/integration/gui/test_ui_modularization_parity.py tests/ui/test_interaction_filters.py tests/ui/test_dialogue_editing_flow.py tests/ui/test_dialogue_reopen.py tests/ui/test_dialogue_worker_lifetime.py -q -p no:cacheprovider --basetemp=.tmp-project-open-01a152cf/model-tests-final-2
```

249 passed，20.49 秒。前轮主题计时与后续布局受测试遗留窗口和 QApplication 主题影响，修复测试清理后通过；未放宽原 250 ms 与 196 px 阈值。

```powershell
uv run --no-sync pytest tests/ui/test_project_recovery.py -q --basetemp .tmp-project-open-01a152cf/qa-recovery-final -p no:cacheprovider --disable-warnings
```

2 passed。综合 UI 收集 2506 项，在约 87% 主动中断以避免无关全局主题测试持续干扰性能测量，未获得完整结束摘要，不能声称全 UI 通过；已观察到的一个 recovery host 夹具失败已修正并独立通过。另两项 AI 模式布局失败使用任务开始源码副本复验：

```powershell
uv run --no-sync pytest tests/ui/tools/test_ai_mode_switching.py -q -k "size1 or size2" -o pythonpath=.tmp-project-open-01a152cf/baseline/src --basetemp .tmp-project-open-01a152cf/qa-ai-baseline -p no:cacheprovider --disable-warnings
```

同样 2 failed、4 deselected，均为第 171 行字段 Y 坐标变化断言，确认是起点已有问题；本轮未修改该 AI 模块。

### 性能回归与正式采样

```powershell
uv run --no-sync pytest tests/performance/test_workbench_story07_performance.py tests/performance/test_ui_foundation_performance.py tests/performance/test_ui_modularization_performance.py -q -p no:cacheprovider --basetemp=.tmp-project-open-01a152cf/model-performance-1
```

13 passed、1 failed，317.89 秒。新增 20k 行替换 <200 ms、既有 100 生命周期与心跳测试通过；`test_final_gui_foundation_profile_enforces_all_versioned_budgets` 的独立子进程超时 300 秒。当时广 UI 仍并行执行，未形成独占环境；不能据此宣称 Foundation 全部预算通过，也没有扩大超时或放宽阈值。

正式工程采样在上述测试进程全部退出后串行独占执行。Windows 11 10.0.26200、Qt 6.10.0/PyQt 6.10.2、offscreen、96 DPI、DPR 1；每规模一次冷开和五次重载。范围为真实 ProjectCoordinator、AppContext、Step2，未接入完整 MainWindow。每次最终只触发一代渲染。

```powershell
uv run python scripts/benchmark_project_open.py --entries 1000 10000 20000 --output .tmp-project-open-01a152cf/benchmark-current-virtual-synthetic.json
uv run python scripts/benchmark_project_open.py --data-root data --case 1 --output .tmp-project-open-01a152cf/benchmark-current-virtual-real-case1.json
uv run pytest tests/scripts/test_benchmark_project_open.py -q --basetemp=.tmp-project-open-01a152cf/benchmark-case-tests -o cache_dir=.tmp-project-open-01a152cf/benchmark-model-pytest-cache
```

runner 的最终 16 项测试通过；真实案例选择参数也受覆盖。单次恢复保真检查使用任务临时脚本，执行后按完整快照和每词条状态核对，只保留本记录中的计数证据。TEMP/TMP 和 UV_CACHE_DIR 均指向任务专属临时根，未启动用户数据目录的应用实例。

合成项目最终五次重载结果（毫秒，中位数及最小～最大）：

- 1k：总耗时 1120.34（989.69～1276.88）；GUI 安装 2.31（2.09～2.97）；最大心跳间隔 78.72（78.08～158.62）。冷开总耗时 1271.59、安装 27.13、心跳 78.64。
- 10k：总耗时 11971.74（11560.95～12210.40）；GUI 安装 36.77（28.24～55.60）；最大心跳间隔 218.87（212.42～358.56）。冷开总耗时 11824.64、安装 120.93、心跳 171.46。
- 20k：总耗时 25474.92（25010.05～25640.02）；GUI 安装 110.80（99.28～118.46）；最大心跳间隔 437.36（402.50～513.45）。冷开总耗时 24775.32、安装 82.59、心跳 280.80。

对应五次原始样本，顺序均为重载 1～5（毫秒，取两位小数）：

```text
1k total   = [1276.88, 1120.34, 1114.82, 1129.63, 989.69]
1k install = [2.24, 2.40, 2.09, 2.31, 2.97]
1k gap     = [158.62, 78.65, 78.72, 78.08, 90.92]
10k total   = [11560.95, 11971.74, 11605.75, 12134.12, 12210.40]
10k install = [28.24, 33.73, 49.80, 55.60, 36.77]
10k gap     = [212.42, 358.56, 217.35, 342.67, 218.87]
20k total   = [25146.74, 25010.05, 25474.92, 25640.02, 25523.33]
20k install = [99.28, 105.33, 118.46, 113.35, 110.80]
20k gap     = [440.60, 402.77, 402.50, 437.36, 513.45]
```

同环境初始 10k 五次中位数：GUI 安装 1542.28 ms、最大心跳 19237.37 ms、总耗时 25558.46 ms；1k 初始总耗时 2482.22 ms、心跳 1863.29 ms。最终表格 start_render 中位数为 0.42/11.41/27.17 ms（1k/10k/20k），不再有逐项批次。

初始 runner schema 1 总耗时终点为全行就绪加 processEvents；最终 schema 2 单独观察视口 Paint 派发，总耗时仅作近似对比，不声称精确提升百分比。安装与心跳统计定义一致。**10k 和 20k 的全流程心跳仍超过 NFR1.5 的 200 ms 预算，不能标记整体响应预算完成。**此前诊断观测到长心跳与 worker 中 gen2 GC 重合；本轮不修改全局 GC 策略或引入新的进程架构。

真实 8311 条工程一次冷开和五次重载全部成功，原记录及源文件哈希未变。五次重载：总耗时中位数 15201.06 ms（15080.28～15892.96）、GUI 安装 53.46 ms（49.13～57.08）、最大心跳 281.71 ms（281.09～324.43）、首个视口 Paint 派发完成 15200.81 ms（15079.38～15892.69）。冷开总耗时 11866.34 ms、安装 28.09 ms、心跳 249.74 ms。初始同工程五轮中位数：总耗时 29519.61 ms、安装 1553.09 ms、心跳 21380.94 ms。

```text
8311 total   = [15892.96, 15088.27, 15080.28, 15201.06, 15342.39]
8311 install = [54.35, 53.46, 49.13, 52.73, 57.08]
8311 gap     = [310.65, 324.43, 281.09, 281.71, 281.33]
8311 paint   = [15892.69, 15088.22, 15079.38, 15200.81, 15338.69]
```

第二个真实工程单独执行 prepare→activate 只读路径，恢复 19387/19387 状态：missing=0、modified=0、完整 VariantSnapshot 相等，editable authority 保持 None，原始及副本 Project/Variant 字节哈希均不变。该工程初始报 `VARIANT_LOAD_STALE`，最终因确实存在无法匹配词条而安全恢复；没有将只读结果计入成功编辑性能数据。

### 收尾检查

```powershell
uv run pytest tests/persistence/test_project_source_identity.py tests/ui/test_project_open_guards.py tests/ui/test_project_open_operation.py tests/ui/test_project_view_installation.py tests/ui/test_project_source_hydration.py tests/ui/test_background_gui_operations.py tests/integration/gui/test_app_context_projection.py -q -p no:cacheprovider --basetemp .tmp-project-open-01a152cf/z
uv run ruff check src tests scripts/benchmark_project_open.py
uv run ruff format --check src tests scripts/benchmark_project_open.py
```

最终 74 passed（含 7 个来源身份用例），1.81 秒；5 个既有弃用警告。Ruff check 通过，1878 文件格式检查通过。结合前述 499 项，持久化/项目应用/投影分组共 500 个独立测试取得通过证据；不同测试组有交集，不将各组数量简单相加为唯一总数。

相对任务开始哈希确认 72 个文件属于本次增量；`git diff --check` 通过，实施方案及本日志的相对链接均可解析。用户既有 UI 修改保留；未修改 `uv.lock` 或 `pyproject.toml`。

任务专属临时根使用所有权标记核验，保存本记录的测量摘要后统一清理；真实数据根及源文件未修改。基准 JSON 和隔离源码副本不作为用户数据或构建产物提交。

## 兼容与限制

- 本次不新增 schema 版本、不无条件重写项目。既有旧 schema 仍使用已有安全迁移协议；缺省可选字段可在内存补齐，普通保存使用当前序列化规则。不能把“打开成功”解释为所有文件立即改写成新格式。
- “艺术馆”副本存档 19387 条，源文件哈希未变，但当前解析 19584 条；恢复 namespace 后 19386 个完整 key 能对应，1 个旧 key 无对应，另有 198 个解析条目。缺失条目为空译文也必须保留，最终进入只读恢复，可通过已有更新源文件/迁移预览核对；未自动改用户工程。
- commit 前失败不切换目标工程；用户明确选择 SAVE 后已完成的旧工程保存不会因为后续候选失败而撤销。commit 后 GUI 安装失败会禁用错误视图，不能声称已回滚磁盘事务。
- lifecycle 仍超过 700 行：本轮已抽取扩展的候选准备职责，既有保存/目录/导出事务不整体重组。后续扩展这些职责前需单独拆分并复跑故障事务测试。
- 未执行可见 Windows 完整 MainWindow 人工交互、安装包启动、跨工程 A→B→A 性能或峰值内存验证。没有将离屏视口 Paint 事件等同于显示器完成呈现。
