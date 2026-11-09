# 全量 QA 后的存储、任务恢复与界面修复

- 日期：2026-11-09
- Epic/Story：maintenance/regression-suite
- 依据：用户要求修复此前 QA 分析中的全部确认问题，并调用 bm-pilot 完成验证与记录。
- 关联：[FR32](../../../requirements/project-storage-redesign.md)、[ADR-054](../../../adr/054-project-storage-and-independent-tasks.md)、[ADR-039](../../../adr/039-terminology-localization-profiles.md) 的现行 v2 方案合同；沿用工程存储与独立任务架构，不新增旧项目兼容入口。
- 归属边界：以本轮开始时保存的工作树文件快照为基准，仅记录本轮实际增量；不把此前工程存储重做、文件对话框开发、67 个测试文件的清理或用户既有修改归入本次实现。

## 已确认根因与验收行为

此前 24 个剩余失败中，22 个来自实现、工具或资源问题，合并为 15 个根因；另两个术语测试仍沿用过期断言。插件添加、紧凑布局和 POT 缺项在 HEAD 基线同样失败。修复保留失败保护、幂等和版本约束，没有添加 skip/xfail 或放宽性能预算。

### 保存与任务结果

- `src/transbridge/application/projects/durable_variant.py`：区分后验读取异常、确证不匹配和已保存；有界只读核验，保留尚未确认的保存批次及操作身份。重试先对账活跃工作副本。来源结构变更和普通保存前先厘清待确认提交，防止旧 CAS 基准卡死后续保存。
- `src/transbridge/application/projects/lifecycle.py`：透传已提交后的偏好告警；增加受锁和目标约束的保存对账及结构变更前检查，共用内容更新事件。
- `src/transbridge/application/projects/save_changes.py`：对账已确认的保存批次，移除已持久化来源增量并保留后续修改。
- `src/transbridge/persistence/v2/variant.py`：以冻结的提交前内容逐字段保留晚到编辑；译文与 Stage 同组，避免标签修改覆盖下载译文或显式清空被回填。
- `src/transbridge/application/projects/ports.py`：生命周期提交可返回诊断，兼容旧适配器的 None。
- `src/transbridge/application/projects/gui_facade.py`：提供保存结果对账入口。
- `src/transbridge/bootstrap/persistence.py`：给现有自动保存服务注入项目读取端口，用于确认旧基准及操作凭据。
- `src/transbridge/application/tasks/result_application.py`：支持调用方固定 operation_id；新明确确认不按粗粒度候选内容归并到旧操作，同次重试保留幂等性。
- `src/transbridge/application/sync/download_view.py`：定义可原地重试保存的类型化失败。
- `src/transbridge/application/sync/download_merge.py`：已有提交凭据先同步工作副本，再返回成功；区分无法安全恢复和暂时保存失败。
- `src/transbridge/application/sync/download_task_results.py`：凭据恢复后仅收尾独立任务元数据，使用已有回执统计，不重新写入项目。
- `src/transbridge/ui/operations/batch_download_dialog.py`：保存重试保留本轮下载、选择和操作身份，不重复 HTTP；提交后的晚取消不能误报“本地未修改”或清除保存重试入口。
- `src/transbridge/ui/tools/ai_translator/task_merge_review.py`：冻结每次明确确认的操作身份、目标及相关当前内容；合入与收尾分离，收尾失败只补记恢复证据及任务记录，不再次覆盖译文。
- `src/transbridge/ui/tools/ai_translator/task_merge_dialog.py`：关闭按钮、窗口关闭及 Esc 共用收尾门；后台补记成功后关闭，失败保留窗口并说明原因。
- `src/transbridge/ui/tools/ai_translator/task_registry.py`：把待补记错误交给既有应用退出保护。
- `src/transbridge/application/translation/task_merge_evidence.py`：复用已有 Windows 路径规范化能力，支持深目录中的证据及 applied 回执原子写入和读取。

### 工程存储与历史

- `src/transbridge/persistence/project_lifecycle_uow.py`：透传提交后的诊断。
- `src/transbridge/persistence/project_store/lifecycle.py`：偏好发布或写后核验出错时保持项目已提交事实，返回告警；建项和改名共享名称发布约束。
- `src/transbridge/persistence/project_store/catalog.py`：复用跨进程 OS 租约，在发布边界重查规范名称，并区分同名冲突和目录繁忙。
- `src/transbridge/persistence/project_store/repository.py`：建项在隔离文件完成内容准备，再在名称租约内发布正式数据库。
- `src/transbridge/persistence/project_store/archive.py`：包导入先拒绝目录等非普通文件；副本重映射版本、活动版本、条目、历史快照及术语选择，发布前验证引用；导入同样受名称约束。
- `src/transbridge/bootstrap/history_search.py`：按真实来源折叠关系过滤，保留独立迁移来源的正式译文搜索。
- `src/transbridge/ui/coordinators/guided_project_coordinator.py`：创建成功提示保留提交后的告警。
- `src/transbridge/ui/coordinators/project_open_operation.py`：打开成功与告警合并展示，避免随后的成功消息覆盖告警。

### 界面与导出

- `src/transbridge/ui/guidance/models.py`、`state_machine.py`：增加内容统计尚未就绪的引导状态和禁用操作说明。
- `src/transbridge/ui/guidance/qt.py`：分页来源复用带身份与版本约束的后台统计，不同步扫描失效集合。
- `src/transbridge/ui/workbench/project_paging.py`：提供已确认统计和就绪信号，编辑、版本变化时重新调度，拒绝迟到统计。
- `src/transbridge/ui/shell/intent_composition.py`：接入既有分页统计服务。
- `src/transbridge/ui/coordinators/source_add_operation.py`、`src/transbridge/ui/source_hydration.py`：新增来源时构建完整且唯一的工作台投影，保留旧来源原生模板及未保存译文；普通重开仍沿用分页。
- `src/transbridge/ui/operations/facade.py`、`coordinator.py`：通用导出先展示格式，在确认后按格式异步准备输入，保持预检和授权。
- `src/transbridge/ui/coordinators/source_input_coordinator.py`、`write_input_coordinator.py`：按实际输出要求选择插件，并保护取消、工程/版本变更及后台失败。
- `src/transbridge/ui/operations/write_dialog.py`、`write_json_export.py`：展示来源名称，批量 JSON 使用可用的来源显示名称生成文件名，不把合成 SQLite URI 当作 Windows 路径。
- `src/transbridge/ui/shell/assistant_project_navigation.py`：缺失或不可读的工程返回明确不可用状态，历史仍可浏览。
- `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py`：标题换行、工具栏分行，避免混合模式最小宽度撑大公共配置区域。
- `src/transbridge/ui/i18n/messages.pot`：同步五个菜单标签和三个提示，移除对应已退役入口。

### 基准工具

- `scripts/benchmark_project_open.py`：复用生产建项命令，等待当前来源完整物化，回收分页 worker；保留被其他比较工具引用的测量接口。
- 新增 `scripts/benchmark_project_inputs.py`：抽出原输入隔离职责。新格式使用只读 SQLite backup，复制实际选中的不可变术语方案，清除隔离库的任务提交凭据；原工程及附属资产不改动。旧格式逻辑仅用于历史 checkout 对照，不接回生产入口。沿用原有输入摘要校验，未新增身份哈希算法。

## 测试变更

- `tests/application/projects/test_durable_variant.py`：确证不匹配与读取异常的保存恢复行为。
- `tests/application/translation/test_merge_evidence_cost_audit2.py`：Windows 深路径 applied 回执往返。
- `tests/integration/bootstrap/test_project_storage_profile_package.py`：按副本的新版本身份检查术语选择。
- `tests/integration/bootstrap/test_terminology_browsing.py`、`tests/ui/tools/terminology/test_effective_browsing.py`：实际保存的 profiled:v2 方案完整映射生效，草稿仍独立；v1 checkpoint 兼容测试保持不变。
- `tests/persistence/v2/test_project_activation_transactions.py`：真实跨进程导入、建项和改名共享发布约束。
- `tests/persistence/v2/test_project_archive.py`：副本双版本、历史快照引用及并发发布。
- `tests/persistence/v2/test_project_management.py`：同名改名拒绝。
- `tests/persistence/v2/test_project_provisioning.py`、`test_workflow_closure_audit.py`：偏好 replace/readback 故障后数据已提交、结果有告警、恢复目标可核验。
- `tests/scripts/test_benchmark_project_open.py`：新格式、真实术语关联、原件只读、隔离凭据及完整物化测量。
- `tests/ui/guidance/test_qt_binding.py`：加载状态、统计就绪、迟到统计拒绝与真实编辑保存。
- `tests/ui/operations/test_download_autosave_boundaries.py`：丢回执、连续核验失败、晚到标签/显式清空、普通保存、来源拓扑变更及任务终态。
- `tests/ui/operations/test_unified_download_service.py`：同轮无 HTTP 保存重试、更改选择失效及晚取消。
- `tests/ui/test_background_gui_operations.py`、`test_project_creation_transition.py`：打开及真实创建成功同时展示偏好告警。
- `tests/ui/test_partial_save_open.py`：部分保存告警保留在最终成功提示内，不被后一条通知覆盖。
- `tests/ui/test_on_demand_source_inputs.py`：无原插件时单个/批量 JSON、SST 实际导出，以及插件写回取消和重选。
- `tests/ui/test_plugin_drop_workflow.py`：来源唯一性、未保存译文及版本切换保护。
- `tests/ui/test_v2_project_transfer.py`：副本使用独立版本身份，同时保留版本名称、修订和词条内容；原包及原工程不变。
- `tests/ui/tools/test_ai_result_lifecycle.py`：二次明确覆盖、两类收尾故障仅补记、关闭与 Esc 失败保护及重开恢复。

## 验证

### 功能与静态检查

- 最终收集 **11,293** 个测试节点。按模块分进程执行，Qt 工具目录每批 10 个模块；最终结果账本覆盖全部节点，无遗漏或额外节点。最新结果为 **11,269 通过、23 跳过、1 失败**；唯一失败是下面列出的既有主题性能门禁，实际运行的功能用例通过。不是单进程全库通过。
- 此前 24 个失败项复验通过。另在全量中修正两处与本轮合同变化有关的断言：工程包副本使用新版本身份、部分保存告警合入最终成功通知。对应模块分别 18 通过，以及打开/创建告警联合 30 通过。
- 使用现有 uv 环境与锁文件，PowerShell 命令形态为 `$env:QT_QPA_PLATFORM='offscreen'; uv run --offline --no-sync pytest <分组路径> -q -ra -p no:cacheprovider --basetemp <本轮独立临时目录>`。保存与下载边界、存储/包导入、界面导出和任务生命周期分别先做聚焦验证，再纳入完整节点覆盖。
- 测试故障注入曾替换 Path.open，导致临时 QA 结果收集器自身中断；改为提前打开记录流后，应用、bootstrap、contracts、integration、parser 五组完整补跑通过。首次 Qt 工具分组未正确拆小，出现长时间等待，终止的仅为已核验路径的本轮测试父子进程；该目录随后拆成 22 组全部通过。没有删除失败节点或放宽断言来掩盖中断。
- `uv run --offline --no-sync ruff check src tests scripts/benchmark_project_open.py scripts/benchmark_project_inputs.py`：通过。
- `uv run --offline --no-sync ruff format --check src tests scripts/benchmark_project_open.py scripts/benchmark_project_inputs.py`：2,526 文件格式通过。
- `git -c core.safecrlf=false diff --check`：通过。
- 新格式基准脚本测试通过；使用历史 HEAD checkout 作 source-root 的工具探针完成 6 个样本，验证历史对照入口仍可用，不代表所有真实工程性能达标。

### 尚未通过的性能门禁

- 用例：`tests/performance/test_ui_foundation_performance.py::test_final_gui_foundation_profile_enforces_all_versioned_budgets`。原完整分组为 71 通过、1 失败：窗口打开 final P95 734.9 ms，高于当次相对上限 645.9 ms。
- 无其他本轮测试并行的独立复验：该文件 7 通过、1 失败（160.28 秒）。窗口打开预算已通过，但主题切换心跳 P95 **246.0 ms > 200 ms**；切换本身 P95 244.6 ms，低于独立的 250 ms 预算。两个门槛均保留。
- 修复前工作树快照使用相同环境、同一测试、同一预算串行对照：1 失败（180.12 秒），同样为心跳 P95 **273.7 ms > 200 ms**。测试、代表窗口夹具及 Foundation 主题路径在本轮前后未改变。这证明修复前也存在该失败，不证明所有波动均来自外部环境。
- 20 次切换的独立阶段探针：总计 3908.7 ms；全树 repolish 2919.5 ms（74.7%），Qt 事件处理 835.2 ms（21.4%），setPalette 39.8 ms（1.0%），同步订阅 111.5 ms（2.9%）。每次刷新 1,648 个已 polish 控件。探针心跳 P95 为 218.3 ms，同样超标。
- 同色图标重复渲染约占总耗时 1%，复用预估仅省约 2.4 ms P95，不能作为已解决门禁的依据。主要同步开销位于 `src/transbridge/ui/foundation/palette_refresh.py` 的全树样式刷新；减少或分片刷新涉及 QSS 调色板一致性和回滚，尚无已验证的安全局部改动，本轮未改动该路径。
- **遗留：主题切换性能门禁仍失败**；窗口打开结果也存在波动。没有提高预算、添加 skip/xfail，或将最后一次成功指标写成整项通过。offscreen 测量仅是 partial-boundary，不代表 Windows 可见窗口的完整性能验收。

### 未执行的环境相关验证

23 个既有跳过项：12 个原生 Windows HWND 用例要求 windows 平台插件；6 个符号链接用例缺少系统权限；2 个真实安装/升级用例需要显式启用；1 个真实 ParaTranz 用例缺少专用启用配置；1 个 Windows 非法文件名场景不适用；1 个普通 Windows 深路径创建用例受系统能力限制。未新增跳过。专项合入证据的 Windows 超长路径读写回归已经通过，不能代替所有系统路径能力验证。

## 边界与责任审查

- 不迁移或清理旧项目，不修改用户翻译数据、依赖、锁文件或安装器；不执行真实远端 API 写入或发布。
- `lifecycle.py` 在本轮之前已超过 700 行。本次保存锁、目标校验和发布复用原有职责，字段合并放在聚合及 save_changes 中，按局部缺陷修复例外保留位置；继续新增独立生命周期职责前必须拆分，避免扩大跨操作耦合。
- `variant.py` 超过 500 行；新增方法仍属聚合的保存确认职责。`WorkbenchProjectPaging` 已超过 30 方法，新方法仅服务既有统计调度和读取；新增独立职责前应拆分。
- 新增基准输入模块承接完整隔离复制职责，避免继续扩张原基准脚本。
- 不从未确认的 prewrite 文件推断合入成功；未模拟进程被强杀。正常窗口关闭的记录失败有明确保护与重试。
- 此前校对批次计数不稳定项连续 20 次复验通过；此前会话权限错误未确认底层原因。不能以本轮未复现宣称它们已被根治。
