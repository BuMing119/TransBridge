# 本地工程打开性能修复方案

- **Feature slug**：`project-open-performance`
- **状态**：S01～S05 已实现；相关功能回归通过，最终性能验收见增量记录。
- **日期**：2026-10-19
- **来源**：用户反馈开始页加载工程时明显停顿，并询问优化与修改方案。
- **对应需求**：[FR19.4、FR19.7、FR28.6、NFR1.5、NFR1.7](../../docs/requirements.md)
- **相关 ADR**：[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-025](../../docs/adr/025-project-window-launch-and-open-progress.md)、[ADR-035](../../docs/adr/035-authoritative-project-mutations-and-recoverable-save.md)
- **已有实现**：[工程启动 Hub](../start-center-launch-hub/plan.md)、[Project/Session Persistence V2](../project-session-persistence-v2/plan.md)。这两个 Epic 保留原完成记录；本计划修复其跨持久化与 UI 的性能缺口。

## 目标与范围

工程打开期间事件循环持续响应，减少重复读取、校验与数据转换；保持文件格式、词条身份、编辑行为、保存真实性、并发检查及崩溃恢复语义。同步修复合法旧记录被误判为加载冲突的问题。

第一批 S01～S04 已完成基础优化和复测，之后按证据实施 S05：19584 行解析结果的 GUI 安装中位数 311 ms；单次诊断中旧表释放占 start_render 87%。最终替换表格数据呈现和必要调用方，保留工作台操作。该真实工程随后发现来源身份兼容问题，前述数据仅用于定位旧表开销，不能作为数据保真的成功打开基线。

不调整视觉布局、全局缓存/并发配置，不更换持久化格式或引入依赖。活动工程行继续直接返回工作台，不重新加载。新窗口继续使用独立进程。

## 已验证事实

2026-10-19，在 Windows、Python 3.12.12、HEAD `a75548e` 加当时未提交 UI 改动的工作区中，用真实工程文件的独立副本和离屏 Qt 测量。原始工程文件哈希未变，测试副本已清理。

- “蕾米尔”8311 条，五次重载：主线程连续占用中位数 21.123 秒，范围 20.814～21.760 秒；其中激活中位数 19.563 秒，范围 19.268～20.409 秒。
- 同轮同步词条挂载中位数 1.524 秒，范围 1.350～1.565 秒；后续表格分批填充中位数 2.118 秒，范围 2.064～2.261 秒。
- 激活阶段执行 10 次 `validate_v2`，其中 Project 4 次、Variant 6 次，累计约占激活耗时 83%～86%。之前的后台准备另有校验，不计入这 10 次。
- “艺术馆”19387 条副本在激活中返回 `VARIANT_LOAD_STALE`，因此没有成功打开的五轮基线。原记录全部缺少可选 `external_refs`；反序列化再序列化会补空数组并改变 `inferred_fields`，不代表文件被外部修改。
- 项目打开、来源恢复、开始中心和统计筛选相关测试 47 项通过；全仓 Ruff check/format check 通过。已有测试仅断言 prepare 离开 GUI 线程，并显式要求 activate 在 GUI 线程，不能证明完整加载响应性。

上述为定位用初始测量，未接入完整 MainWindow 的所有订阅，也不是优化后的结果或正式 CI 基线。实施时须补可复现 runner，再在相同输入、机器、依赖和字体/DPI 条件下比较。

## 调用链与设计约束

改前路径：`StartCenterController → ProjectCoordinator → worker.prepare_path → GUI.activate → switch_v2 → prepare_transition/commit_transition → projection → GUI hydration → 逐个 remove/add slot → 表格刷新`。

1. `project_lifecycle_loader.py` 普通打开同一 Variant 两次；每次 repository.load 又分别在副作用预检与正常读取中完整解析、校验。
2. `project_activation_journal.py` 的 `write=False` 分支仍对相同 previous/payload 校验两遍。提交时版本/哈希检查必须保留，不能把“减少重复工作”实现成跳过安全检查。
3. `activate()` 已提交磁盘与权威活动对象。不能在它成功后才做可失败的 hydration，又声称任何失败都会保留旧工程。应在提交前准备尽可能完整的纯数据结果，提交后只做受控 UI 发布。
4. `ProjectLifecycleService` 与 `ProjectionStore` 持有锁。单纯换线程仍可能让 GUI 查询阻塞；投影监听也不能在持锁回调中访问控件或同步等待 GUI。
5. 当前 AppContext 投影回调具备 `safe_mutate`，但投影和槽位分别通知，会暴露新项目元数据配旧内容的中间状态。需要统一发布顺序和一次通知。
6. `context.py` 约 813 行、`lifecycle.py` 约 1072 行，必须先按完整职责抽取将扩展的切片；不能新增 mixin 或继续堆入大类。`project_coordinator.py` 约 493 行，应将打开操作抽成独立 coordinator。`repository.py` 约 595 行，先做责任审查，允许局部消除重复读取，不增添跨操作缓存职责。

## S01：修复合法旧版本的假冲突

**状态**：已实现，兼容及真实冲突回归通过。**依赖**：无。

**实现**：

1. 普通版本打开只读取一份 `LoadedRecord`，用它构造 `VariantSnapshot`，并携带其 revision/source_hash。
2. 删除“原始 DTO 与规范化后的 DTO 比较”这一错误的并发检测。实际提交仍复查磁盘 revision/hash；读取到提交之间的真实外部编辑必须返回冲突。
3. 快照恢复仍单独读取指定快照和正式版本，保留正式版本归属与提交前版本检查，不能把两类来源合并。
4. 使用脱敏合成 fixture 表达缺少 `external_refs`、缺少其他兼容字段及显式空值，不将用户译文或项目文件加入测试库。
5. 读取存档中的来源指纹，只有源文件 SHA256 能唯一且无冲突地对应时，才恢复原来源 namespace；不把历史 Windows 路径当作 namespace。缺失来源或无法对应的非 tombstone 词条进入只读恢复，保留全部存档，禁止普通打开静默剔除旧身份。空译文不例外。

**文件落点**：修改 `src/transbridge/persistence/project_lifecycle_loader.py`；新增 `tests/persistence/v2/test_project_open_records.py` 和 `test_project_open_activation.py`，聚焦规范化与真实冲突行为；复用既有 activation/workflow closure 测试。

**验收**：缺少合法可选字段的旧记录能打开；译文、Stage、labels、provenance 和 inferred 语义保持；真实外部修改仍被拒绝；失败不覆盖原文件。激活期间完整校验的预期诊断计数由 10 降至 8，不将调用次数作为唯一正确性测试。

## S02：消除同次打开中的重复校验和转换

**状态**：已实现，持久化事务和只读路径回归通过。**依赖**：S01。

**实现**：

1. repository 副作用预检对当前合法格式产生可复用的已读、已校验结果，在同次读取和相同锁作用域中直接形成 `LoadedRecord`。若进入恢复、迁移或隔离路径，恢复后必须重新读取，不复用过期字节。
2. 未写入的 activation record 保留 previous 的完整验证、revision/hash 检查与 preimage 验证，避免再次解析验证完全相同的 payload。新写入的 target 仍执行独立验证，journal 发布和故障恢复协议不变。
3. `project_projection()` 为取得 label_library 使用专门的只读导出方法，移除为此调用整份 `variant.to_dto()` 的全量转换，保留嵌套元数据的不可变与复制约定。
4. 复用 `EntryProjectionUpdate.from_snapshot()` 和 immutable snapshot.values，按一次打开构建一次身份索引；避免 `snapshot.to_dict()` 全量解冻以及每来源重建同一索引。
5. 不改变现有公开 `apply_variant_projection()` 的新集合/新条目契约。新的批量准备路径仅操作尚未发布的对象，允许直接生成最终条目，之后转移所有权；旧公开调用方继续兼容。

**文件落点**：`src/transbridge/persistence/v2/repository.py`、`project_activation_journal.py`、`variant.py`，`src/transbridge/application/projections/adapters.py`，`src/transbridge/ui/source_hydration.py`、`entry_projection.py`。复用 repository/read_snapshot、activation、projection 合同测试并补充针对性场景。

**验收**：迁移、未来版本拒绝、损坏记录隔离、多进程冲突、每个 journal 中断节点仍通过；读取快照无写入；同次打开不保留跨 revision 的长期缓存。普通干净打开的激活校验预期降至 4 次（Project/Variant 各 2），这是静态预期，实际耗时以复测为准。

## S03：完整后台打开与一致的界面发布

**状态**：已实现，相关 UI 回归通过。**依赖**：S01～S02。

**实现的数据流**：

`GUI dirty 决策/捕获身份 → worker 校验来源与准备 lifecycle candidate → worker 构建纯数据 UI payload → worker commit → GUI 一次安装最终 projection 与 slots → 安装表格模型并按需绘制`。

**实现步骤**：

1. 新增 `src/transbridge/ui/coordinators/project_open_operation.py`，把现有打开操作、阶段进度和 worker 收尾从 ProjectCoordinator 完整抽取。沿用 ApiWorker、worker registry 和窗口关闭协议。
2. 复用 lifecycle 的 prepare/commit token，不新增第二套事务。`transition_preparation.py` 完整承担临时 leases、候选加载/校验和准备状态构造；通过 `prepare_candidate` 回调在 token 生成前构建独立视图。token 的 owner、generation 和消费检查仍由 lifecycle 维护，不暴露 `_prepared` 私有字段。
3. `CurrentProjectOpener.activate()` 增加可选 `prepare_view` 和 `expected_active_signature`，结果携带不透明 `view_payload`。原同步 API 保持可用。`ProjectOpenOperation` 的 UI adapter 将候选投影变为 detached slots 和标签元数据；提交后只对齐已确认身份/修订和 dirty 状态，再交给 GUI 安装。
4. 来源转换、Variant 应用、身份索引和必要统计尽量在 commit 前完成。校验或构造失败即丢弃 prepared token，旧活动工程继续可用。worker 不构造 QWidget、不调用 UI 信号对象的方法更新状态。
5. 后台提交期间，GUI 使用已安装的只读视图状态判断动作可用性，不同步查询 lifecycle 锁。禁用工程编辑、切换、导入/导出等冲突入口；已有后台任务仍遵守身份/revision/lease 校验，不能仅靠按钮禁用保护权威状态。
6. 从 AppContext 抽取完整的工程投影绑定职责到 `src/transbridge/ui/project_view_binding.py`，保留 facade 公共接口。打开期间暂存该操作的投影通知，最终把 projection、全部 slots、active key 和标签状态一次安装后通知观察者。正常编辑投影仍及时更新，不全局断开订阅。
7. 不依赖不同 Qt 信号之间的隐式先后顺序。结果包含操作代次、工程/版本身份和候选修订；失效结果不覆盖新上下文。若权威状态已前进，发布当前状态的一致刷新或明确恢复流程，不静默丢掉已提交状态。
8. commit 前失败不切换目标工程；若用户选择 SAVE 且旧工程已保存，后续准备失败不会撤销这次保存。commit 后若发生意外 GUI 安装错误，展示可诊断恢复状态，禁止重新启用旧内容编辑或伪装事务已回滚。关闭请求等待现有 worker 安全收尾，不阻塞 GUI `wait()`，不强杀磁盘事务。
9. 开始页进度覆盖校验、准备、切换与一致视图安装，不虚构百分比。重入/异常/关闭路径都复位或正确保持关闭态。来源校验和提交实际迁入 worker；表格由 S05 模型按需绘制，不再构造或清理逐词条的单元格对象。
10. 已同步修订 ADR-025 的当前窗口流水线、进度和失败语义；原测试“activate 必须在 GUI 线程”的断言改为完整后台打开。

**已定位的接线细节**：

- opening 状态独立于 `worker.isRunning()`，持续至 GUI 安装完成；result 排队时线程可能已经结束，不能提前允许第二次打开。
- dirty 决策绑定用户回答时的旧身份/revision；后续新编辑或旧任务提交不能被过期的 DISCARD 决策覆盖。候选使用的 baseline registry 也要有准备作用域或明确前像，准备失败/取消时释放 token、leases 并恢复必要状态。
- `TaskRecoveryDiscovery → project_directory → active_project_path → lifecycle.active` 是实际 GUI 等锁路径。打开期间挂起该恢复扫描，目录查询改为读取已安装的视图上下文；`SourceUpdateCoordinator.start_current()`、菜单、快捷键和命令面板的直接入口一并检查 opening 状态。
- 每种旧信号各发一次仍可能触发多次表格构建。工程安装必须提供明确的统一完成事件或有界合并机制，保证标签和 collection 的订阅最终只刷新一遍；不能用全局 `blockSignals()` 丢弃权威更新。
- close pending 时不弹出只读恢复窗口或自动导航；提交开始后等待安全终态并对齐当前 authority，再继续既有异步关闭。任何已提交结果都不能只因 UI 代次过期而被丢弃。

**验收与测试**：

- prepare、候选数据准备与磁盘 commit 确实在 worker；控件和 AppContext 最终发布在 GUI 线程。
- 使用事件屏障控制后台耗时，验证等待期间 Qt timer、进度及非冲突交互仍工作；再用真实数据副本测心跳，覆盖 Python CPU/GIL 工作，不能只用 sleep mock 证明性能。
- 覆盖 dirty 保存/丢弃/取消、准备失败、提交失败、hydration 失败、只读恢复、重复点击、保存冲突、任务迟到、打开期间关闭和取消关闭。
- 多来源一次安装后只触发一次最终列表/当前内容更新；所有观察者读取到的项目身份、标签和条目属于同一版本；默认 active source 保持既有选择语义。
- 复跑 `tests/ui/test_background_gui_operations.py`、`tests/integration/gui/test_app_context_projection.py`、`tests/application/projects/test_lifecycle.py`、`tests/persistence/v2/test_project_activation_transactions.py` 及 worker/窗口关闭相关测试。

## S04：压缩 UI 收尾并建立可复现验收

**状态**：已实现，五轮性能结果见最终增量记录。**依赖**：S03。

**实现**：

1. Step2 在工程安装事务期间合并标签、collection 和 terminology 通知，只为最终活动来源启动一次渲染；保持普通编辑的单行增量更新。
2. 默认筛选直接复制条目引用，分类按本次 context 复用；无术语投影时避免无意义的身份序列化。第一批的首批延后与分批填充实现已由 S05 模型替代，不保留两套渲染路径。
3. 新增 `scripts/benchmark_project_open.py`，直接覆盖实际 ProjectCoordinator 打开流程。默认使用合成项目与临时数据根；真实输入仅显式复制后测量，绝不激活原始 `data/`，不输出译文或凭据。源码无需硬编码用户项目名。
4. runner schema 2 记录准备、激活（含候选准备与提交）、槽位与标签准备、GUI 安装、模型行数据就绪、视口 Paint 派发和 Qt 心跳间隔，报告一次冷开与五次重载；兼容旧逐项表格以便基线对照。仅包含 ProjectCoordinator、AppContext 和 Step2PreviewWidget；完整 MainWindow 订阅、峰值内存与跨工程 A→B→A 性能未测，空工程和多来源由功能回归覆盖。同工程重载只用于诊断，活动工程行不新增此行为。
5. 五次定位对比给出原始样本、中位数及范围；既有 FR25 的 10k/20 样本/100 生命周期单独回归。原 schema 1 的总时长终点是全行数据就绪加 processEvents，schema 2 另观察视口绘制，因此总时长只能作近似比较；GUI 安装和心跳定义保持一致。“艺术馆”最终进入只读恢复，不能把失败或恢复耗时当作可编辑打开成功对照。

**文件落点**：`src/transbridge/ui/workbench/step2.py`、`translation_table.py`、`translation_model.py` 及既有渲染切片；新增 benchmark runner 和聚焦开销/交互回归。TablePresenter 最终无需修改。超阈值模块先审查职责，新增数据准备或安装协调不得继续堆入 Step2/Table。

**验收**：

- 沿用既有 NFR1.5 事件循环最长心跳间隔 ≤200 ms；这是已有预算，不是未经测量承诺的改后结果。
- 在相同环境与样本下证明主线程占用和总加载时间均改善；两项分别报告，不能用后台化掩盖总耗时。
- 首屏就绪后可滚动/选择；完整身份、排序、搜索、标签、双击编辑、F2、批量选择和译名预览保持。
- 稳定态不新增持续轮询；关闭后无遗留 timer/worker/订阅或持续内存增长。若旧表释放或单批渲染仍超预算，第一批不能宣称性能验收完成，进入 S05。

## S05：按测量触发的虚拟表格改造

**状态**：已实现；受影响模型、编辑、选择、排序等 249 项回归通过。**依赖**：S04 的分段结果。

采用 `QTableView + QAbstractTableModel`，数据保留在条目集合/显示索引，单元格数据按视图请求提供，避免构造每条七个 QTableWidgetItem。保留当前 UI 外观和交互，不用固定截断条目或任意分页改变工作流。

新增内聚的 `src/transbridge/ui/workbench/translation_model.py` 和必要的视图选择/编辑适配；迁移 EntryRefresh、inline edit、菜单、勾选与范围解析，保留 TablePresenter 公开调用接口，删除退役 item 路径。编辑只发送意图，经权威提交后更新模型；用代次拒绝过期或外来索引，替换 session 时验证身份和顺序，不破坏增量刷新期间的持久索引。

验收覆盖模型 reset/局部 dataChanged、完整 EntryKey 与显示顺序映射、滚动位置、勾选、编辑和术语预览；按 S04 协议复测。20k 行替换的专门回归仍使用 200 ms 预算，未放宽既有主题或布局阈值。

## 验证命令与交付

实施时先运行聚焦测试，再运行受影响的持久化、投影、关闭及工作台回归。推荐起点：

```powershell
uv run pytest tests/persistence/v2/test_project_open_records.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_repository.py tests/persistence/v2/test_repository_read_snapshot.py tests/persistence/v2/test_project_activation_transactions.py tests/persistence/v2/test_workflow_closure_audit.py -q
uv run pytest tests/application/projects/test_lifecycle.py tests/integration/gui/test_app_context_projection.py tests/ui/test_background_gui_operations.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_start_center_guided_project.py -q
uv run pytest tests/ui/test_workbench_slices.py tests/ui/test_translation_table_reading.py tests/performance/test_workbench_story07_performance.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

兼容测试和 `scripts/benchmark_project_open.py` 均已落地。依赖使用现有 uv 环境和锁文件；必要时使用任务专属缓存，不升级依赖。实际命令、跳过项与失败按结果记录，不能以旧的 47 项通过替代改动验证。

## 兼容、风险与回退

- 本次不新增 schema 版本或无条件重写项目。既有旧 schema 继续走原安全迁移协议；合法可选字段在内存中补默认值，正常保存时才按当前序列化规则写回。来源哈希/身份无法可靠匹配的工程进入只读恢复，须通过现有来源更新流程核对迁移，不能承诺所有历史文件均可直接编辑。
- repository 复用必须限定同次读取与正确锁边界；恢复后、磁盘摘要变化后不能复用旧结果。
- 线程迁移必须同时解决投影顺序、生命周期锁和关闭等待，不能只移动一个函数调用。
- UI 发布不是新的业务提交；不能把 UI 内容重新写回权威聚合，避免破坏 ADR-035。
- 每个 Story 可独立提交和回退；S03/S04 的配套线程/通知改动必须成组回退。既有 journal 和公开接口始终可用，不增加永久双实现开关。
- 当前工作区有大量未提交 UI 改动，实施只修改已明确的性能职责，保留用户其他改动。
- 责任审查：lifecycle 的候选准备已抽为独立模块，主文件缩小但仍超 700 行；其既有保存、目录事务和导出协议本轮不整体重组，以限制持久化回归范围。后续扩展上述任一事务责任前，须先单独抽取相应切片并复跑事务故障测试。AppContext 保留已有 facade，但工程订阅、队列排序、安装及状态已集中到 ProjectViewBinding；不向旧 facade 增加数据转换职责。
- 最终测量、200 ms 心跳预算的实际达成情况、未完整执行的广 UI 和可见窗口检查，均以本轮增量记录为准；不把后台运行等同于完全没有停顿。
