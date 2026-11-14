# 项目存储重做与任务历史独立

- Feature/Epic：`project-storage-redesign`。
- 状态：S01～S10 本地实现与相关验证完成；S04 稳定滚动及当前来源后台完整加载、前台驻留已完成验证。此前三项既有测试失败、冷缓存及发行验证限制仍见交付记录。
- 日期：2026-11-09。
- 需求：[FR32](../../docs/requirements/project-storage-redesign.md)（已确认）。
- 架构：[ADR-054](../../docs/adr/054-project-storage-and-independent-tasks.md)（已采用、本地实现）。
- 用户边界：2026-11-09 用户再次调用 bm-pilot，授权开始编码；生产代码与单元测试分别交由 GPT-6.1 Sol / high 子 Agent，主会话负责协调、审查及总集成/回归测试。此前仅文档阶段已结束，不迁移或清理用户旧数据。

## pilot 阶段记录

当前环境未提供 update_plan 工具，阶段状态在本计划维护，不输出伪工具调用。

- [x] 复核已确认 FR32、仓库入口和相关历史 ADR；复用需求，不重做产品分析。
- [x] bm-arch：新存储、任务归属、并发、格式和退役边界提议。
- [x] bm-plan：S01～S10 的依赖、落点与验收方案。
- [x] bm-story：S02/S03/S06 高风险流程细化。
- [x] bm-qa：本轮文档差异、链接、路径、需求覆盖与状态校验。
- [x] bm-chronicle：记录本轮可归属文档变更与实际验证。

上述六项为已完成的设计/文档阶段。本轮实施阶段如下，未获得运行证据不标记完成：

- [x] bm-dev：编码子 Agent 按依赖完成生产接线与必要模块。
- [x] 单元测试：独立 Sol/high 子 Agent 编写并运行聚焦验证。
- [x] bm-qa：主会话执行集成、整体回归和必要性能验证，审查修复。
- [x] bm-chronicle：记录本轮实际代码差异、验证和未完成项。

旧数据迁移或自动清理始终不在授权范围内。项目切换仅发现新根，独立任务历史保留在自己的根目录。

### 实施验证与交付

主会话最后领域/格式/归档回归 1277 passed、1 skipped；助手与 SQLite 1213 passed，最终稀疏改动后助手工具再验 375 passed；任务 UI 655 passed；打开/包/窗口/分页对话 101 passed、1 skipped；新存储与真实项目核心 84 passed。bootstrap/publish/entrypoints 联合 331 passed、1 skipped、3 项与编码前同原因的基线失败。执行引擎隔离诊断 15 passed；性能扩展 3 passed；全库 Ruff 与 2523 文件格式检查通过。不将重叠分组相加为唯一测试总数。

同输入 100k/80 来源、旧 HEAD 对照，新版五轮首屏中位 103ms、完整范围累计就绪 9383ms；单条保存 80ms、100 条 90ms、10000 条 1705ms。10000 条准备加保存中位 4107ms。首屏、全量与编辑准备分别测量；首次样本不是清空缓存后的冷开。单大来源完整筛选仍约 3.53～8.28 秒、最长采样心跳 250ms。旧数据没有迁移或自动清理。

具体命令、逐文件变更、原始样本、故障修复和未执行边界见[实施记录](../../docs/changelogs/project-storage-redesign/story-01-to-10/2026-11-09-001-translation-project-store.md)。Windows 沙箱测试运行器仅调整任务自有临时目录；共享 QApplication 与 worker 收尾用于稳定重复 Qt 测试，未修改生产权限策略。

## 范围、依据与责任审查

新项目独立 SQLite 库和独立任务库替换项目 JSON/artifact 全量链；项目保存必要翻译内容，插件本体不持久化，来源路径作为可失效引用保存，旧项目和旧包一律拒绝。保留项目版本、快照、格式保真、显式清空、三方合入、输出原子性和有效的会话/任务权限。

旧工作线 `project-open-performance` 解决同次解析复用和 UI 后台化，`project-self-contained-storage` 目标是保存完整来源及升级旧工程；与本轮不兼容的产品边界不同，因此新建 Epic，不追加到已完成 Story 里。既有计划与测试报告保留历史状态。

改造前 `bootstrap/persistence.py` 装配旧 project/variant repository、source artifact、migration 和 journal。原 `GuiProjectCommandFacade.task_result_directory` 依赖活动工程；AI recovery/history/store 在项目目录下持久化。`SourceNamespace` 已排除绝对路径，完整 `EntryKey` 可继续复用。

本轮只读计数：`application/projects/lifecycle.py` 1087 行、`gui_facade.py` 583 行、`persistence/v2/variant.py` 513 行、`repository.py` 637 行、`ui/context.py` 647 行、`source_registry.py` 505 行。禁止继续向这些文件加入分页数据库、任务存储或格式抽取职责。新增内聚切片后在旧入口删改接线；保留 Session 所需的 v2 基础设施，不以目录整体替换扩大风险。实施前重新计数，不能把这些数字当永久事实。

下列落点已与实际文件核对，接口与实现细节以代码为准。不引入 ORM、服务端数据库或新的内容寻址资源系统，不修改锁文件作为快捷修复。

## 共同合同与实现选择

- 使用标准库 sqlite3，每项目数据库将 Project/Variant 和合入凭据放入同一事务，任务数据库与项目物理分离；首版 DELETE journal、短事务和 revision CAS，具体理由见 ADR-054。
- 新根为 `project-store-v1` / `task-store-v1`，不扫描旧根进行升级。新 `.transbridge` 包沿用 ZIP 外壳，必需新 manifest 与数据库身份，旧包拒绝。
- 业务读取采用有 revision 的范围/分页查询；编辑采用稀疏 patch 和独立 dirty 状态，禁止未加载等同于不存在。项目 dirty、查询 generation、任务结果修订分别管理。
- 输入提取和任务序列化采用白名单；SourceSnapshot、prepared_source、插件 bytes 和完整解析树不可跨入持久边界；来源路径仅进入独立引用字段，不作为内容身份。翻译文件原生字段按格式保真保留。
- 项目与任务合入采用项目内凭据及任务侧对账，不宣称跨库单事务；不以清空用户编辑或重跑任务解决回执失败。
- 术语版本先独立发布，再由项目事务引用不可变版本；新项目的选择引用单点权威。项目包只携带正式业务资产，不携带任务和运行回执。

## Story 与依赖

1. S01：新存储格式、查询/事务接口与基准夹具。无前置实现依赖。
2. S02：来源提取与新建项目，无插件留存，路径仅作引用。依赖 S01。
3. S03：稀疏编辑与原子增量保存。依赖 S01、S02。
4. S04：按需打开、完整范围查询与工作台。依赖 S03。
5. S05：独立任务存储、查询和恢复。依赖 S01、S02 的字段合同。
6. S06：任务成果合入、跨库对账与独立删除。依赖 S03、S05。
7. S07：当次选择插件写回与翻译文件保真导出。依赖 S02、S03。
8. S08：版本、快照、来源更新和新项目包。依赖 S03、S07。
9. S09：全入口和下游切换，移除旧项目运行链。依赖 S04～S08。
10. S10：故障、性能、空间与发布前回归。依赖 S01～S09；单 Story 验证在各步持续执行。

本轮按用户指令委派独立 Sol/high 编码和单元测试 Agent，文件写入范围分别为 src/scripts 与 tests；主会话维护文档并负责总体验证。切换生产组合前，新路径必须具备完整功能；开发中的测试 factory 不得演变为产品永久新旧开关。

## S01：新存储格式、查询/事务接口与基准夹具

状态：本地完成，格式/事务/成本证据已验证。需求：FR32.1、FR32.8、FR32.9。

**验收标准**：新项目与任务根、格式标识和白名单数据模型可独立识别；错误/旧格式零改写拒绝；条目 identity、NULL/空值、标签和外部引用完整往返；提供分页读取和按变化提交所需的窄接口，不能把旧 JSON 放入单个数据库大字段。

**落点**：新增 `application/projects/storage_contracts.py`（领域输入/结果与协议）、`persistence/project_store/format.py`、`connection.py`、`schema.py`、`reads.py`、`writes.py`、`paths.py`；新增 `application/tasks/storage_contracts.py`。复用现有 `application/io/identity.py`、`application/projects/ports.py` 的适用合同与 `persistence/v2/ids.py` 的不含路径身份值类型，不把 SQL 引入 UI。

**步骤**：定义新 application_id/version 与包 manifest；列出逻辑记录及查询索引；明确 nullable 原文身份、可选字段和最小格式字段；实现连接所有权、事务、busy/只读/磁盘满诊断；添加来源目录、按 key 批量读、分页/范围和 CAS patch 协议。准备公开或合成数据集，不复制用户旧项目作迁移夹具。

**验证**：新增 `tests/persistence/project_store/test_format.py`、`test_entries.py`；覆盖无文件与空库、任意 SQLite 文件、未来格式、损坏 schema、重复 key、不同 original 状态和两写者冲突。检查库里没有单列完整 Variant JSON 或任意对象 codec。

## S02：来源提取与新建项目

状态：本地完成，来源提取、生产建项和格式保真已验证。需求：FR32.1～FR32.4。详细指南：[S02](stories/story-02-source-extraction.md)。

**验收标准**：插件仅在导入操作中读取和解析，正式项目/任务/日志摘要不含插件本体或完整解析树；项目保存来源路径引用，任务/日志不嵌入插件输入；必要原文、上下文、顺序及完整身份保存后可独立读取；多来源建项全成或全败；EET/SST 等原生翻译记录不因抽取丢失。

**落点**：新增 `application/projects/source_extraction.py`、`source_fields.py` 与 `persistence/project_store/repository.py`。改造现有 `persistence/project_provisioning.py`、`application/projects/provisioning.py`、`provisioning_models.py`；利用 `application/io/eet_adapter.py`、`sst_adapter.py`、`strings_adapter.py` 的解析结果，取消向 `saved_project_sources.py` 发布完整 PreparedProjectSource 的新项目调用。

**步骤**：逐字段核验 consumers；将解析期对象与持久翻译内容分开；只在边界允许规定字段；建项事务写入所有来源和默认版本；错误摘要使用来源 ID/名称并保留原因，不在任务历史嵌入原路径；取消及失败释放输入和 staging。

**验证**：新增 `tests/persistence/project_store/test_source_extraction.py`、`test_exchange_fidelity.py` 和 `tests/integration/bootstrap/test_project_storage_composition.py`；复用现有多来源、localized 与 EET/SST 解析夹具。使用带标记的插件字节与路径检查所有持久载荷，不以只查扩展名代替嵌套数据审计。

## S03：稀疏编辑与原子增量保存

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32.1、FR32.8。详细指南：[S03](stories/story-03-working-copy-save.md)。

**验收标准**：单条/少量修改只提交实际变化和必要 revision/凭据，无修改不写；未加载行和其他版本不丢；保存过程中继续编辑仍保持正确 dirty；并发、失败、崩溃保持一致；不依赖插件或任务库判断“已保存”。

**落点**：新增 `application/projects/working_copy.py`、`save_changes.py` 与 `persistence/project_store/commits.py`。改造现有 `variant_commands.py`、`durable_variant.py`、`save_result.py`；从超限 `lifecycle.py` 抽离新保存职责，旧 facade 只做窄接线。`ui/project_view_binding.py` 与 `projection_consistency.py` 改用页/patch 所有权证据，不全量反序列化比较。

**步骤**：定义字段级 patch、base revision 和本地编辑序号；先读指定行再进行领域校验；捕获保存批次；项目数据库 CAS 提交；只确认本批仍匹配的 pending changes；保留原错误原因；明确术语选择引用与项目 revision 同事务，术语资产先发布再引用。

**验证**：新增 `tests/persistence/project_store/test_entries.py` 与 `tests/integration/bootstrap/test_project_storage_composition.py`；继续覆盖现有 `test_save_projection_cost.py` 的无变化行为、`test_project_activation_transactions.py` 的失败语义，替换旧跨文件实现断言。写入预算不能用“最终值正确”代替。

## S04：按需打开与完整范围查询

状态：完整逻辑列表与稳定滚动修复完成，见[滚动修复记录](../../docs/changelogs/project-storage-redesign/story-04/2026-11-09-001-complete-workbench-scroll.md)；当前来源后台完整加载与前台驻留完成，最终分组 200 项验证通过，见[驻留加载记录](../../docs/changelogs/project-storage-redesign/story-04/2026-11-09-002-resident-workbench-source.md)。需求：FR32.3、FR32.8。

**验收标准**：首屏只读取目录和所需页；缺原插件无提示；滚动、筛选、排序、搜索、全选和批量动作语义覆盖完整范围；迟到页不污染切换后的项目或新编辑；任务历史增长不增加打开路径扫描。

本轮修复验收：前台模型具有完整、稳定的逻辑行数与绝对行号。首屏先显示，当前来源的全部词条自动在后台加载并驻留；不滚动也会完成，完成后拖到末尾、返回开头和筛选/排序浏览均直接访问内存，不出现按页等待。选中与编辑仍绑定完整条目身份，加载期间的新编辑和保存不能被旧结果覆盖；切换来源丢弃旧驻留内容和迟到结果，其他来源仍按需打开。不得为修复滚动同步加载整个工程。

- [x] 定位：旧实现用最多四页的缓存内容重建表格，改变前台行数和相对位置。
- [x] 编码：独立 Sol/high Agent 实现完整逻辑行与后台页缓存分离。
- [x] 测试：独立 Sol/high Agent 添加真实 Qt 首尾、跨页、筛选排序与身份回归。
- [x] 主会话：整体回归、性能抽查、审查与增量记录。
- [x] 追加反馈：当前来源后台完整加载、驻留及编辑/切换竞态验证，主会话测量首屏与全量就绪成本。

**落点**：新增 `persistence/project_store/paged_reads.py`、`ui/project_rows.py` 与 `ui/workbench/project_paging.py`；调整现有 `ui/coordinators/project_open_operation.py`、`ui/project_view_binding.py`、`ui/workbench/translation_model.py`、`application/projections/project_entries.py`、`bootstrap/persistence.py`。将页查询交接放入已有 worker/coordinator 生命周期，不往 `ui/context.py` 塞 SQL 或新的大控制器。

**步骤**：先提供概况与来源选择，再后台查询页；稳定行身份与全范围 query token 分开；实现缓存上限与 dirty patch 保留；长范围读取捕获稳定 revision，变化时有界重试；关闭/切项目撤销订阅并忽略迟到结果。

**验证**：新增 `tests/integration/gui/test_project_paged_workbench.py` 与 `tests/performance/test_project_storage_scaling.py`；保留 `tests/ui/test_project_open_operation.py`、`test_projection_check_during_open.py`、`test_translation_table_reading.py` 的适用交互。测试未加载来源全选、筛选跨页、排序后编辑、加载中切换与关闭，不用 sleep-only mock 验收响应性。

## S05：独立任务存储、查询与恢复

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32.5、FR32.6。

**验收标准**：没有活动项目也能列出、读取和导出任务；原项目删除后成果仍可用；输入/候选/进度/报告不多份全量保存；恢复能力真实，缺插件要求重新选择；任务库不可用不阻止项目打开保存。

**落点**：新增 `persistence/task_store/connection.py`、`schema.py`、`results.py`、`recovery.py`、`reports.py` 和 `application/tasks/history_service.py`。改造现有 `application/translation/task_history.py`、`task_recovery.py`、`task_recovery_storage.py`、`ui/tools/ai_translator/task_session.py`、`task_recovery_binding.py`、`task_history_dialog.py`、`ui/shell/task_navigation.py`；`gui_facade.py::task_result_directory` 的任务调用者切换为注入任务服务，最终移除该活动项目路径前置。

**步骤**：建立以 Run ID/Owner 为中心的记录；固定实际范围的输入；候选只保存单一不可变修订；报告和恢复引用候选；新下载未合入成果也走同一服务；任务持久化失败和远端结果不确定时保持真实状态；定义无需项目查询的历史分页与相应 capability。

**验证**：新增 `tests/persistence/task_store/test_independent_lifecycle.py` 与 `tests/integration/gui/test_task_storage_history.py`；沿用 `tests/application/translation/test_task_history.py`、`test_task_recovery_results.py`、`tests/ui/tools/test_ai_task_history_ui.py` 的领域断言，替换项目路径假设。覆盖直接构造 store 的旁路，不只验证主 GUI 入口。

## S06：合入对账与独立删除

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32.5、FR32.6、FR32.8。详细指南：[S06](stories/story-06-result-commit-reconciliation.md)。

**验收标准**：项目提交成功而任务回执失败时，可按凭据恢复状态且不重复合入；未保存/被撤销候选不得假报已持久合入；删除任一方不损坏另一方；失效目标禁止自动回写；清理保留运行中、未合并和必要恢复材料。

**落点**：新增 `application/tasks/result_application.py`、`persistence/project_store/receipts.py`、`persistence/task_store/decisions.py`。复用 `application/projects/result_merge.py` 与 `application/translation/result_merge.py` 的三方规则；调整现有 `ui/tools/ai_translator/task_merge_review.py`、`smart_assistant/tools/translation_task_results.py`、`application/sync/project_download.py`、`persistence/project_management.py` 的对应接线与删除责任。

**步骤**：任务先保存不可变处理请求；权威项目产生 patch 与 pending receipt；项目事务提交变化及凭据；异步回写任务状态并按需对账；区分已应用待保存/已持久合入/目标消失；建立运行租约与删除/保留策略；容量统计与安全回收分开。

**验证**：新增 `tests/integration/bootstrap/test_project_result_receipts.py`、`tests/persistence/task_store/test_independent_lifecycle.py`；复用 `tests/application/projects/test_project_result_merge.py`、`tests/application/translation/test_result_merge.py`。逐提交点故障注入、重复请求、先合入后编辑/撤销/切项目、任务删除后项目保存、项目删除时任务仍运行均必测。

## S07：按需绑定插件写回与翻译文件导出

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32.2～FR32.4。

**验收标准**：普通编辑导出翻译数据不需原插件；插件写回优先尝试保存路径，失效时补充当次输入，严格核验匹配；缺 Strings/错误插件/取消不破坏项目；输出后不留内部副本；EET/SST/XML 的现有保真合同保持。

**落点**：新增 `application/projects/write_inputs.py`；改造 `application/preflight/` 的现有输入合同、`application/io/publish/coordinator.py`、`ui/coordinators/source_input_coordinator.py`、`smart_assistant/tools/write_back_preparation.py`，并调整 EET/SST 等 adapter 的项目数据输入。保持 `application/io/publish/plugin_fidelity.py` 等既有校验，不删验证来绕开缺模板。

**步骤**：区分新项目翻译视图与本次 write snapshot；保存路径尝试、按需选择、映射、许可和输出一条流程；按格式字段重建翻译文件文档；移除补输入后自动永久保存插件的调用；失败路径清理自身临时材料，保留用户原文件与成功输出。

**验证**：复用 `tests/integration/publish/test_plugin_fidelity.py`、`test_atomic_publish.py`、`test_bsa_localized_write.py`、`test_round_trip_fidelity.py`；新增 `tests/integration/bootstrap/test_project_storage_composition.py` 与 `tests/smart_assistant/tools/test_write_back_options.py`。覆盖同名不同内容、多个来源、目标插件模式、EET/SST 重复记录及非默认阶段，不能只测 XML。

## S08：版本、快照、来源更新与项目包

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32.1、FR32.7、FR32.9。

**验收标准**：版本复制独立，命名快照恢复业务状态，换源原子更新全部正式版本且保留必要历史翻译；普通保存不创建历史；项目包只带必要业务内容，在无源插件/任务目录环境可用；旧包明确拒绝且原件不动。

**落点**：新增 `persistence/project_store/history.py`、`archive.py`、`catalog.py`、`persistence/project_store/archive_validation.py`；修改现有 `application/projects/snapshots.py`、`source_update.py`、`source_update_migration.py`、`ui/coordinators/project_transfer_coordinator.py`。复用现有归档路径/重复成员/容量保护，替换旧 `project_archive_reader.py` 的 Project 格式解释。

**步骤**：必要来源修订与历史引用留在项目库；明确回收只处理无正式/历史引用修订；原子来源更新；一致数据库副本去掉运行凭据/未选择历史；白名单导入验证；新目录索引只读发现与发布分开；删除先取得写入租约，原子重命名发布删除状态后清理自有目录；发布失败保留原项目，可重试且不碰任务库。

**验证**：新增 `tests/persistence/project_store/test_entries.py`、`test_archive.py`、`tests/integration/bootstrap/test_project_storage_composition.py`、`test_project_storage_profile_package.py`；复用 `tests/security/test_archive_attack_corpus.py` 和项目版本/快照测试的业务断言。新增恶意 schema、路径穿越、重复成员、快照过滤、包副本身份、发布失败及旧目录零改写检查。

## S09：入口、下游消费者和旧链退役

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32 全部。

**验收标准**：GUI/CLI/MCP/助手实际组合都使用新服务；旧 schema/包/来源恢复链不可达；搜索、术语、命名方案、AI、同步及批量操作不因未加载漏数据；旧 Session 可浏览但不触发旧项目升级；新数据不会写回旧根。

**落点**：现有 `bootstrap/persistence.py`、`bootstrap/history_search.py`、`bootstrap/terminology_sources.py`、`application/projects/profile_entry_inputs.py`、`persistence/terminology/paths.py`、`persistence/terminology/profiles.py`、`ui/shell/start_center_directory.py`、`ui/coordinators/project_coordinator.py`、`ui/shell/project_window_launcher.py`、`cli.py`、`entrypoints/mcp.py`。项目读模型消费者和工具参数接线按全库引用清单逐一核验。

**步骤**：先完成读/写消费者矩阵；新的 composition 不启动旧项目 journal 或 catalog repair；将历史搜索、术语和 profile selection 接到明确新 ports/路径；统一 GUI 双击/拖入/启动和助手格式拒绝；删除项目专用 legacy/archive/artifact/migration 分支和旧 schema fixture；保留 Session、通用身份和文件发布仍使用的 v2 模块；更新实际用户文档。

**验证**：现有 `tests/integration/bootstrap/test_persistence_v2_projection_composition.py`、`test_project_catalog_composition.py`、`tests/ui/test_project_open_guards.py`、`test_v2_project_transfer.py`、`tests/smart_assistant/current_project_support.py` 支持的入口回归须按新合同重写/扩展。扫描所有 ProjectSourceArtifactStore、task_result_directory、legacy_project 和 source path consumer；剩余引用逐项有非项目理由，不以搜索零结果替代行为测试。

## S10：故障、性能、空间与回归

状态：本地实现与相关回归完成，验证限制见交付记录。需求：FR32 全部。

**验收标准**：需求验收矩阵全部有运行证据；量化证明新项目不保存插件，路径仅作按需引用、任务独立、少量保存不全量重写、全范围操作不漏数据、旧格式拒绝不改写。性能未达预算时记录瓶颈并修复，不能仅改报告阈值。

**落点**：复用 `scripts/benchmark_project_open.py` 的测量终点，新增 `scripts/benchmark_project_storage.py` 和 `scripts/compare_project_storage.py`；测试落在各 Story 所列目录和既有 `tests/performance/`、`tests/integration/`。修改性能 runner 时保留旧基线模式作为测试工具，不把旧格式 reader 重新接回产品。

**步骤**：冻结合成/公开夹具、环境和计时边界；每个场景先聚焦回归，再跨入口和故障注入；测冷/热、重复样本、UI 心跳、内存、磁盘、写入量；区分功能证据、性能证据与未测试条件；按验收结果关闭 Story。

**复验入口**：`tests/persistence/project_store`、`tests/persistence/task_store`、`tests/integration/bootstrap`、`tests/integration/publish`、`tests/integration/entrypoints`、`tests/integration/gui/test_project_paged_workbench.py`、`tests/integration/gui/test_task_storage_history.py`、`tests/smart_assistant`、相关 `tests/ui/tools` 及 `tests/performance/test_project_storage_scaling.py`。实际分组、结果及 Windows 测试运行器限制见交付记录。

## 性能验收方案与候选预算

样本至少含 1k / 10k / 100k 条目，100k 样本含约 80 个来源；包含重复 local_key、显式空译文、长上下文、本地化字符串、多版本、历史快照。任务规模单独设置为 0 / 100 / 1000 个历史任务，不能改变项目数据来制造对照差异。使用新建项目输入，不提供生产旧项目转换工具；旧实现对照只在隔离测试夹具执行。

记录 1 次冷读和至少 5 次重载的所有样本、中位和最大值；如宣称 P95，须至少 20 个稳定样本，明确排序取值规则。计时从用户操作到首屏可编辑、当前来源可操作、完整范围操作各自就绪分别记录；后台隐藏等待计入相应端到端耗时。

以下保留实施前的候选预算口径，实际测量见交付记录，不将其改写为产品承诺：100k/80 来源样本重载首屏可编辑中位 ≤2 秒，1/100 条保存中位 ≤500 毫秒，UI 心跳最大间隔 ≤200 毫秒。S01 须固定参考机器和任务范围，S10 验证；若平台固有能力导致预算不合理，提供测量证据和修订理由，不在失败后静默放宽。

硬性结构门禁：原插件持久化为零，日常打开/编辑/保存对来源引用目标的访问为零，项目读写对任务历史遍历为零，少量保存不全量读取/序列化/重写条目。写入量包含 journal/临时文件，比较 10k→100k 总规模下固定 k 条编辑的变化，不只记录最终 DB 文件大小。容量对照分别统计项目、任务、快照/备份和缓存，禁止把任务移出目录的减少冒充总占用减少。

## 风险、回退与未决项

- 最小字段模型可能漏掉对话关系、术语作用域或 EET/SST 原生数据：S02 逐消费者核对，S07 格式保真失败不得跳过或退回保存插件。
- 稀疏模型与旧全量聚合不能长期双权威：S03 定义新工作副本，S09 清理全部生产旁路；保留的 facade 只转发，无自行填空或补加载保存。
- rollback journal 的长读取影响保存：S04 测量读事务时长，分页读取与有限 revision 重试；必要性不足前不引入 WAL 或新运行时依赖。
- 跨库任务合入采用凭据对账；项目内变化与凭据同事务，任务回执失效不重放业务。详细故障矩阵见 S06。
- 旧程序不能消费新项目；失败回退保留新旧数据各自原件，不降级转换，不运行清理。旧磁盘占用不属于此次实现自动释放的承诺。
- 数据库/包标识数值与最小字段已在实现和 ADR 固化；本机合成基准环境见实施记录。任务正式历史默认不自动按天删除，中间材料按引用与运行状态回收；后续期限策略不在本轮擅自决定。

## 需求到 Story 的验收追踪

- FR32.1：S01/S02/S03/S08，字段保真、无重复权威、版本与空值。
- FR32.2：S02/S05/S07/S08/S09/S10，所有持久入口无插件和变相副本。
- FR32.3：S02/S04/S07/S09/S11，路径保存为引用，日常无探测，导出时校验并按需补齐。
- FR32.4：S07，输入匹配、取消、Strings 与输出保护。
- FR32.5：S05/S06/S09，任务独立与原项目删除后的可用性。
- FR32.6：S05/S06/S08，合入、独立删除、租约与回收。
- FR32.7：S08，版本/快照/项目包完整和白名单归档。
- FR32.8：S03/S04/S06/S10，稀疏保存、完整范围、故障与实测。
- FR32.9：S01/S08/S09/S10，全入口旧格式拒绝且零改写。

## 本轮验证与交付记录

### S11：来源路径引用与按需输入（2026-11-13）

- 状态：本地实现与相关验证完成；本节记录当前任务阶段，不重开 S01～S10。
- 依据：FR32.3/FR32.4 与 ADR-054；用户纠正“不存插件”不等于“不存路径”。
- 范围：建项、添加/更新来源保存引用；项目重开、快照及包保留引用；导出先试引用、失效才补充、手选后同目录补齐、验证成功更新引用；助手保留目录授权。
- 非目标：不恢复插件 artifact，不迁移旧 JSON 工程，不重做多方案复制导出，不将未验证引用变成默认覆盖目标。
- 实施：`storage_contracts` 和 `source_extraction` 捕获引用；SQLite 可选 `source_reference` 表兼容现有 v1 库，读取无表为空、写入按需建表；路径更新核对稳定来源、指纹、旧路径及读令牌，不推进翻译修订；项目包校验显式放行引用表。GUI 后台协调器读当前引用并验证、仅未解决项弹文件选择；通过现有 composition 注入引用服务，助手共用绑定与授权边界。
- 验收：移动/删除原文件不影响打开编辑保存且无源文件访问；有效路径导出不弹选择；同目录匹配只需一次选择；错误内容/Strings/重复候选不误绑定；取消、关窗、切项目/版本与竞争更新不回填；旧 v1 无引用正常使用、包不包含插件本体；输出目标仍由原输出流程决定。
- 验证：存储提取/来源更新/包兼容、真实 Qt 后台选择流程、助手授权与写回测试；全仓 Ruff 检查及格式检查。无网络/LLM，不对用户项目进行迁移或写入。
- [x] 分析和契约：确认生产 SQLite 路径及旧 artifact 不可达；修正现行需求和 ADR。
- [x] 开发：持久化、GUI、助手和相关测试完成并集成；待保存来源不隐式保存译文。
- [x] QA：最终 804 passed、2 deselected；两项排除失败已在任务开始前代码复现。全仓 Ruff/格式通过，差异已审查；完整命令和时序波动见记录。
- [x] 增量：实际变更、兼容边界、验证限制及任务临时文件清理见 [S11 记录](../../docs/changelogs/project-storage-redesign/story-11-source-references/2026-11-13-001-persist-and-resolve-source-paths.md)。

此前仅文档阶段的事实保留在[规划记录](../../docs/changelogs/project-storage-redesign/planning/2026-11-09-001-architecture-and-plan.md)。本轮编码、测试和性能实测见[实施增量](../../docs/changelogs/project-storage-redesign/story-01-to-10/2026-11-09-001-translation-project-store.md)；旧记录不改写为实现完成。
