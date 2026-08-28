# 项目全来源术语构建、版本管理与统一报告计划

- **状态**：草稿
- **日期**：2026-08-28
- **Feature slug**：`project-terminology-build-versioning-reporting`
- **对应需求**：[FR5.16](../../docs/requirements.md)、FR5.2、FR5.13、FR6.10、FR8、FR24.8、FR26.6/FR26.12
- **架构**：[ADR-034](../../docs/adr/034-project-terminology-build-versioning-reporting.md)（提议）、ADR-016、ADR-017、ADR-018、ADR-019、ADR-027
- **前置 Plan**：`platform-contract-foundation-v2`、`translation-io-kernel-v2`、`project-session-persistence-v2`、`unified-task-translation-runtime-v2`、`terminology-format-compatibility`、`existing-translation-terminology`
- **2026-08-28 收口状态**：S00～S11 的生产组合、GUI 闭环、SQLite、effective 翻译消费和自动化正确性验证已实现；S12 未完成，因为最终工作树尚无全套重测，正式 bundle 的内存与 changelog SHALL 仍失败，compare 快路及 query/history 修复也尚未形成新的聚合证据。

## 目标

- 从当前 Project 登记且启用的全部来源和当前激活 Variant 的完整状态，构建可追溯、可重复、可增量复用的项目级术语分析结果。
- 将自动证据、人工决定、冲突处理、可变草稿和不可变发布版本分离，按 `(project_id, variant_id)` 维护独立版本线。
- 以冻结的 `BuildResult`、`TerminologyReportSnapshot`、`CanonicalDiff` 和 `ChangeLogDocument` 作为 UI、质量 Excel、Markdown/Excel 更新日志的唯一业务事实链。
- 通过项目隔离的 SQLite 仓储、keyset pagination、TaskRuntime 和流式 renderer 满足 FR5.16.33～FR5.16.40 的规模、响应、取消和资源预算。
- 通过窄的 `EffectiveTerminologyPort` 将当前已发布版本接入现有匹配器，同时保留无项目版本时的 legacy fallback。

## 非目标

- 不自动扫描 Project 未登记的目录、插件 master、游戏安装目录或工作台临时集合。
- 不跨 Variant 聚合译名，不自动修改项目译文，不把 `.tbdict` 整库当术语，也不自动同步 ParaTranz 远端术语。
- 不改变 ADR-027 `TermEntry` 的交换模型职责，不把证据、版本、冲突或人工审计塞入 `metadata`。
- 首版质量报告只提供应用内预览和 Excel；不自动生成 JSON/CSV。
- 更新日志只说明术语库版本变化，不把术语决定描述成已经写入游戏文本的成品变化。
- 不用 Excel/Markdown 作为权威存储，不引入全面事件溯源，也不依赖 LLM 生成正式发布说明。

## 当前实现事实与关键约束

- Project schema v3 已保存受校验 `SourceRegistration`/`SourceRelation`，v2 副本迁移、崩溃回退和原文件保留已有集成测试；构建输入从 lifecycle 的活动 Project/Variant 捕获，不读取 `AppContext.slots`。
- 正常 `build_runtime` 已创建项目隔离 SQLite repository factory、权威输入捕获、真实 workload registry、commit guard、UI command/services 和 effective adapter factory；空 registry/unavailable commit 只保留显式注入的降级/合同路径。
- GUI 正常入口已接线 preflight→构建→分页预览→冲突/人工决定→草稿→发布→报告→历史/比较/新版本回退→changelog/retry，长操作通过 TaskRuntime/QThreadPool 边界执行。
- published terminology 已接入 translator、term database、vector identity、proofread/polish/quality gate 与 Smart Assistant。Project/Variant/plugin scope 隔离，suppression/未解决/待复核项阻止同作用域 legacy 回流；无已发布版本、只读或损坏时保留 legacy 行为。
- 发布候选 evidence 与运行时能力已经解耦：GUI、command、TaskRuntime runner 和 translator 不读取性能/迁移证据，也不因缺失 evidence 禁用用户操作；partial publish 仍由构建完整性业务规则明确拒绝。
- 正式 runner 与 bundle evaluator 支持 regular/stress 十场景、五轮 RSS、外部等待分桶、真实 Excel/Markdown renderer、UI supplemental 和 digest-bound 聚合。2026-08-28 bundle SHALL 失败，因此发布候选不能通过 CI/发行验收，但不影响本地术语工作台使用。
- 非流式生产 preflight 在任何来源字节常驻前限制最多 50 个启用来源、单源 64 MiB、总量 256 MiB；文件租约使用 1 MiB 分块哈希与 path-backed snapshot，读取时还有硬上限防止 `stat` 后增长。超限明确返回 `TERMINOLOGY_STREAMING_REQUIRED`，因此当前不宣称支持 200-source stress 生产输入。
- SQLite query/history 使用标量 ref + keyset SQL；相邻版本 compare 直接读取发布事务持久化且 digest-bound 的 canonical diff，旧 schema-v2 资产兼容回退，非相邻版本保留完整重算。
- ADR-034 仍为“提议”；本实现和测试不替代架构接受动作。`openpyxl` 与标准库 `sqlite3` 均为既有依赖，不修改 `uv.lock`。

## 总体落点与责任边界

- 新增 `src/transbridge/application/terminology/models.py`、`identity.py`、`ports.py`：纯 Python 领域值、稳定身份、查询/存储/解析/LLM/时钟合同；不得导入 PyQt、openpyxl、sqlite3 或具体 LLM client。
- 新增 `src/transbridge/application/terminology/input_capture.py`、`corpus.py`、`extraction.py`、`reducer.py`、`build.py`：权威输入捕获、来源关系组装、资格筛选、抽取和全局归并。
- 新增 `src/transbridge/application/terminology/drafts.py`、`versions.py`、`diff.py`、`narrative.py`、`reports.py`、`effective.py`：人工决定、版本发布、规范差异、冻结报告和消费 projection。
- 新增 `src/transbridge/persistence/terminology/`：项目隔离 SQLite 路径、schema/migration、事务仓储、分页查询、缓存和 artifact ledger；该包不替代 Project/Variant JSON repository。
- 新增 `src/transbridge/ui/tools/terminology/`：任务导向的 presenter/view/controller、分页 model 和 TaskRuntime adapter；`ui/context.py` 只增加窄的 use-case 获取/投影委托，必要时优先放到独立 adapter。
- 扩展 `src/transbridge/bootstrap/persistence.py` 与 `bootstrap/composition.py`：构造 repository、use cases、renderers 和 effective loader；不把业务规则放进 composition root。
- 新增 `tests/application/terminology/`、`tests/persistence/terminology/`、`tests/contracts/terminology/`、`tests/ui/tools/terminology/`、`tests/integration/terminology/`、`tests/performance/terminology/`，避免把新域测试混入既有翻译报告或动态术语测试。

## Story 00：固定性能基准环境与可复现数据合同

- **详细设计**：[story-00-reproducible-benchmark-contract.md](stories/story-00-reproducible-benchmark-contract.md)

### 验收标准

- 在实现性能优化前记录参考设备 CPU 型号、核心数、Windows 版本、内存、磁盘类型、Python/TransBridge build 和测量工具版本。
- 固定常规/压力数据集的生成种子、真实 adapter 可读格式组合、来源/证据/术语/冲突/版本规模，以及冷缓存、热缓存、LLM 排除计时和 5 次重复运行规程。
- 基准结果可区分 capture/parse/assemble/extract/reduce/persist/query/report/changelog、外部 LLM 等待和外部 I/O 等待；证据落在发布候选可保留的位置。
- 校准只固定测量口径，不修改 FR5.16.33～FR5.16.40 的已确认预算。

### 文件落点与实施步骤

- 新增 `tests/performance/terminology/dataset.py` 和 `tests/performance/terminology/measure.py`，生成真实 ESP/XML/STRINGS/JSON adapter 输入与版本历史；复用 `tests/performance/measure.py` 的通用计时约定，不把百万条固定二进制夹具提交进仓库。
- 新增 `scripts/benchmark_project_terminology.py`，提供冷/热、全量/重复/10% 变化、查询、版本比较和导出场景，输出机器可读 manifest。
- 新增 `docs/test-reports/terminology-benchmarks/README.md`，记录参考硬件、命令、数据 seed、缓存清理边界和证据保留规则；每次发布候选结果另存带日期文档。

### 测试策略

- 小规模 smoke 验证相同 seed 产生相同来源 fingerprint、预期冲突数和 canonical digest。
- 性能测试默认标记 `slow`；CI 运行缩小规模的合同门禁，完整常规/压力基准在指定 Windows 参考设备运行。

### 依赖与边界

- 无代码依赖，必须在 Story 03 之前固定规程；实际耗时断言在 Story 12 才启用。

## Story 01：Project 来源注册、关系图迁移与权威输入捕获

- **详细设计**：[story-01-source-registry-authoritative-input-capture.md](stories/story-01-source-registry-authoritative-input-capture.md)

### 验收标准

- Project schema 使用受校验 `SourceRegistration` 与独立 `SourceRelation`；每个来源具有稳定、与内容 fingerprint 分离的 `source_id`、`enabled`、`format_id`、规范化位置、来源种类、双语能力、可选插件作用域和格式选项。
- `translation_for`、`localized_member_of` 关系具有稳定 `relation_id`、from/to、对齐 policy/version；N:M 关系可表达，歧义不自动选择。
- V2 `primary/migration` 项迁移后获得稳定项目内 ID；只有可证明唯一的关系自动建立，无法证明的项保留且产生待配置诊断。迁移失败保留已验证备份并不覆盖原 Project。
- `capture_build_input()` 在同一生命周期/repository 一致性边界内返回不可变 `BuildInputSnapshot`，包含 ADR-034 指定的 Project/Variant revision、排序后的来源/关系、受控 source snapshot/lease、actual fingerprint、adapter/capability、配置 digest、effective version、draft identity/base/revision/decision digest。
- 未打开 Project、无激活 Variant、无启用来源、需要关系但缺失、多个可能目标或 adapter capability 不足时返回结构化诊断；不读取 `AppContext`。
- FR5.16 的插件解析显式禁止 sibling `Strings/` 自动发现；来源在指纹捕获后变化则结果标记 stale/failed，不把另一份路径内容当同一快照。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/projects/source_registry.py`，承载 registration/relation 值、校验和 Project DTO projection，避免继续扩张 638 行的 `provisioning.py`。
- 修改 `src/transbridge/persistence/v2/schema.py`、`migration.py`、`repository.py`：引入下一 schema 版本的 V2→新版本迁移链、备份/校验/quarantine；同时验证 relation 引用、稳定 ID、重复位置和循环/自引用 policy。
- 修改 `src/transbridge/application/projects/provisioning.py` 与 `src/transbridge/persistence/project_provisioning.py`：委托新 source registry 构造器，停止用 `primary/migration` 作为长期关系语义，保留旧请求 facade 的兼容映射。
- 新增 `src/transbridge/application/terminology/input_capture.py` 和 Project/Variant/source lease ports；通过 `RepositoryPaths` 增加项目术语资产定位，不把路径暴露给 UI。
- 为 `PluginFormatAdapter` 增加显式 parse option/capability，FR5.16 传入“仅已登记本地化来源”，既有普通解析默认行为保持兼容。

### 测试策略

- Project schema/migration fixtures：无关系、自包含双语、单插件多 XML、多插件 N:M、旧 namespace=fingerprint、重复/悬空 relation、恶意 ID/path、迁移故障与备份恢复。
- input capture 合同：只读取 Project 登记来源；Variant 未落盘译文覆盖同 `EntryKey`；其他 Variant 隔离；revision/fingerprint 在捕获前后变化；lease adapter 与摘要复核 adapter parity。
- plugin adapter 回归：普通解析仍可自动发现 Strings；FR5.16 请求只纳入显式登记的 Strings。

### 依赖与边界

- 依赖 Story 00 的数据合同；不创建术语候选或正式版本。

## Story 02：术语领域模型、稳定身份与仓储端口基线

- **详细设计**：[story-02-domain-model-stable-identity-repository-ports.md](stories/story-02-domain-model-stable-identity-repository-ports.md)

### 验收标准

- 明确定义 `BilingualEvidence`、`TermCandidate`、`ConflictGroup`、`TermDecision`、`ManualAction`、`BuildResult/Ref`、`DraftRef`、`TerminologyVersion/Ref`、`CanonicalDiff`、`TerminologyReportSnapshot/Ref`、`ChangeLogDocument/Ref` 和 artifact ledger 合同。
- `evidence_id/candidate_id/term_id/conflict_group_id/build_key` 使用带 schema namespace 的 canonical serialization；排除时间戳、UI 顺序、路径临时名和 run ID，并对摘要碰撞做内容复核。
- `term_id` 保留 Project/Variant 线和作用域身份；改译名不改 ID，改原名产生 replacement；draft cache identity 同时比较 draft ID、base/content digest、revision 和 decision-set digest。
- 模型校验禁止空 actor 冒充人工操作、禁止 unresolved/suppressed 项进入 effective projection、禁止 stale BuildResult 发布。
- 内存 repository 实现和 SQLite 端口共享合同测试，能够证明不可变对象、expected revision、分页 cursor 绑定和版本指针语义。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology/models.py`、`identity.py`、`errors.py`、`ports.py`、`in_memory.py`。
- 把 normalization version、canonical field order、scope 和 typed change enum 固定为公共合同；`TermEntry` 仅在 `effective.py` 边界出现。
- 定义 summary/paged term/paged conflict/manual/evidence drill-down、version history/compare、snapshot-bound keyset cursor 和 `CURSOR_STALE` 错误。

### 测试策略

- property/参数化测试覆盖输入顺序重排、Unicode NFKC/空白/casefold、标点不合并、Variant/作用域隔离、摘要碰撞模拟、模型非法状态。
- repository contract suite 同时运行内存 adapter，后续 Story 04 接入 SQLite adapter。

### 依赖与边界

- 可与 Story 01 同期实现值对象部分，但 `BuildInputSnapshot` 最终字段以 Story 01 为准；不接入 UI 或现有匹配器。

## Story 03：全量构建内核与项目级双语证据归并

- **详细设计**：[story-03-full-build-kernel-evidence-reduction.md](stories/story-03-full-build-kernel-evidence-reduction.md)

### 验收标准

- 流水线严格执行 capture → parse registered sources → assemble evidence → eligibility → deterministic/optional LLM extraction → normalize/deduplicate/conflict group → reconcile manual/effective baseline → freeze。
- 插件原文、关联迁移源、STRINGS 和当前 Variant 状态按完整 `EntryKey`、fingerprint 兼容规则和显式关系形成一条来源链；不能按 local key 或扫描顺序覆盖。
- 首版只接受原文译文均非空且非 hidden/questionable 的证据；排除原因、失败/跳过来源、耗时和完整性进入结果。
- 同规范原名同译名合并全部证据并稳定累计；同原名多译名必建冲突组，不按频次/来源优先级选胜者；人工决定保留并把新增矛盾标为待复核。
- LLM 关闭/不可用/跳过/部分失败不阻断确定性结果；候选无法定位到同一证据、反序列化失败或迟于取消时只产生诊断。
- 相同输入全量构建产生相同候选、冲突、计数、排序和 canonical digest。

### 文件落点与实施步骤

- 新增 `corpus.py`：关系图遍历、evidence assembler、Variant overlay、资格策略和 per-source timing。
- 新增 `extraction.py`：定义 extractor port；用窄 adapter 复用 `ExistingTermSeeder` 的纯规则和 LLM batch contract，不调用其 `seed()`/dynamic DB/私有线程池。必要时先把可复用纯函数抽到新小模块，再由旧 seeder 委托，保持旧行为回归。
- 新增 `reducer.py`：SQLite 无关的 canonical reducer、冲突风险分类、manual/effective reconciliation、稳定 summary。
- 新增 `build.py`：组织阶段并冻结 `BuildResult`；顶层按来源/分片消费，不累积全部 `ParseResult`。

### 测试策略

- FR5.16 验收场景中的全项目枚举、多插件/XML、Variant overlay、同译合并、三译冲突、插件特例、已有权威术语冲突、无 LLM、单源失败、全源失败和输入重排。
- spy extractor 验证只有原文条目不调用 LLM、同证据定位、稳定 batch、skip 后继续；旧 `ExistingTermSeeder` 聚焦回归保持通过。
- canonical golden tests 对全量结果与后续增量结果提供基线。

### 依赖与边界

- 依赖 Story 01～02；先以内存 repository 建立正确性基线，不做性能捷径。

## Story 04：项目隔离 SQLite 仓储、事务、分页与缓存

- **详细设计**：[story-04-sqlite-repository-pagination-cache.md](stories/story-04-sqlite-repository-pagination-cache.md)

### 验收标准

- 每个 Project 使用独立 SQLite 资产，开启 schema version、foreign keys、唯一/校验约束、受控 migration/backup/integrity check；日志模式根据已验证的本地文件系统能力选择，不假定网络路径可用 WAL。
- 一次事务可原子写入 build facts、draft/manual action、version membership、CanonicalDiff、ChangeLogDocument、artifact ledger 初态和 effective pointer；失败时旧 pointer 与历史完整不变。
- 逻辑版本不可变；物理内容寻址/版本 membership 去重不能让后续写入改变历史查询结果。
- summary、term/conflict/manual/evidence、history/compare 使用 snapshot-bound keyset pagination；cursor 绑定 snapshot digest、query fingerprint、sort key 和稳定 ID，条件或快照变化返回 `CURSOR_STALE`。
- `build_key`、parse fragment 和 extraction fragment 三层缓存可丢弃；正式 version/diff/manual/changelog 不受缓存或普通报告清理影响。
- 数据库损坏、未来 schema、迁移失败、空间不足进入只读诊断/阻止发布，不以空库覆盖。

### 文件落点与实施步骤

- 新增 `src/transbridge/persistence/terminology/paths.py`、`connection.py`、`schema.py`、`migration.py`、`repository.py`、`queries.py`、`cache.py`、`artifacts.py`。
- 在 `RepositoryPaths` 或独立受根目录约束的 terminology path adapter 中定位 `projects/<project>/terminology/`，对数据库、backup、staging、artifacts 做 canonical root guard。
- 建立迁移 manifest 与事务 fault-injection seam；大批量写入使用明确 chunk/transaction，查询索引由常规规模 query plan 测试约束。

### 测试策略

- 复用 Story 02 repository contract；增加真实 sqlite temp DB 的 transaction fault injection、foreign key、digest tamper、迁移 backup、只读恢复、空间预检和并发 expected revision 冲突。
- 分页测试覆盖插入相同排序键、筛选/排序变化、snapshot 切换、版本比较和不重不漏。
- `EXPLAIN QUERY PLAN` 或等价检查确保常用筛选/排序不退化为无界 Python 全表加载。

### 依赖与边界

- 依赖 Story 02～03；不得把 SQL row 或数据库路径返回到 use case/UI。

## Story 05：内容键增量构建与全量等价性

- **详细设计**：[story-05-content-key-incremental-equivalence.md](stories/story-05-content-key-incremental-equivalence.md)

### 验收标准

- 完全相同输入在验证 Project/Variant/source/effective/draft 基线后复用 `BuildResultRef`，不重新 parse 或调用 LLM。
- 单个来源变化时只重算受影响的关系连通分量和 parse/evidence/extraction fragment；全局 normalization、冲突、人工协调和 summary 仍走与全量相同的 reducer。
- 关系、adapter/version、parse options、normalization/extractor/prompt/model/config、draft identity/base/decision digest 任一变化均使对应缓存失效。
- 增量与相同输入的无缓存全量构建 canonical digest 完全一致；异常/损坏 cache 自动回退全量，不改变业务结果。
- 结果报告复用/重算来源和分片数量；缓存清理不删除正式历史事实。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology/incremental.py`，计算关系组件 digest 和 recompute plan。
- 扩展 `build.py/reducer.py` 消费新旧 fragments 的完整逻辑集合；扩展 SQLite cache adapter 保存内容键、schema/version 和验证摘要。

### 测试策略

- 修改 0%、单来源、≤10% evidence、关系 policy、Variant revision、extractor 配置、draft 重建/rebase 等矩阵；记录 parse/LLM spy 调用数。
- 每个增量案例与清空 cache 后的全量结果比较 canonical digest、分页内容、冲突/summary 和排序。

### 依赖与边界

- 依赖 Story 03～04；性能预算由 Story 12 验证，正确性不因缓存妥协。

## Story 06：TaskRuntime workload、进度、取消与 stale 屏障

- **详细设计**：[story-06-task-runtime-cancellation-stale-guards.md](stories/story-06-task-runtime-cancellation-stale-guards.md)

### 验收标准

- 注册 `terminology.build`、`terminology.publish`、`terminology.report.render`、`terminology.changelog.render`，owner 固定 Project/Variant，build JobSpec fingerprint 使用 `build_key`。
- 进度使用稳定业务阶段、完成/总来源或批次、当前对象、复用/重算数；LLM 提交/完成/等待/重试/耗时单独统计，连续 2 秒无计数变化仍有 heartbeat。
- 用户停止后 500ms 内可投影“正在停止”，立即停止补充来源/LLM 批次，并在 3 秒内进入用户可见 cancelled；不可中断调用只在隔离区清理。
- worker、fragment、LLM 和 renderer 的迟到结果在写入 BuildResult/draft/version/artifact ledger 前验证 cancellation token、run lease 和 expected revision。
- 构建结束与发布前重验 Project/Variant/source fingerprint/effective/base/draft/build freshness；变化结果为 stale 且不可发布。
- TaskRuntime execution terminal 与 BuildResult `completeness/freshness/llm` 质量维度分离。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology/workloads.py` 和 `runtime.py`，封装 workload request/result、TaskRuntime commit guard 和 heartbeat；使用 composition root 已有 bounded backend/quota，不自建 executor。
- 新增 `src/transbridge/ui/tools/terminology/task_adapter.py`，只把 TaskEvent 转为 UI projection，不继承 QThread 承载业务。
- 在 `bootstrap/composition.py` 注册 use case/workload，资源关闭进入现有 lifecycle。

### 测试策略

- 在 parse、relation reduce、LLM wait、report 和 publish 各阶段取消；验证不再调度、3 秒终态、迟到 commit 被拒绝。
- Project/Variant revision、来源文件、effective version、draft revision 在运行中变化的 stale/failure matrix。
- TaskRuntime 合同测试验证 completed/failed/cancelled 互斥、commit permit 与业务 optimistic guard 缺一不可。

### 依赖与边界

- 依赖 Story 03～05；取消后的 run-scoped 临时预览不能直接冻结为正式结果。

## Story 07：草稿、人工决定与冲突处理

- **详细设计**：[story-07-drafts-decisions-conflicts.md](stories/story-07-drafts-decisions-conflicts.md)

### 验收标准

- 每条 `(project_id, variant_id)` 版本线最多一个 mutable draft；draft 绑定 base version/content digest 和 expected revision，自动保存不创建正式版本。
- 人工支持修改译名、添加术语、调整 scope/variant/备注、统一译名、插件特例、忽略冲突、抑制/重新启用；每项追加 `ManualAction`，固定非空 actor、前后值、原因、基准版本和 replacement/suppression 关系。
- 改原名创建新 `TermDecision` 并 replacement 旧项；删除表达为抑制，证据不删除。重建不会覆盖人工字段或恢复已抑制项。
- 新证据与人工决定冲突时保留人工当前值并标记新增待复核；证据消失时保留决定并标记“当前无证据/可能过期”。
- effective/base/Variant 变化时拒绝静默覆盖 draft，提供 rebase、以历史版建新稿或放弃；这些动作产生新的 draft identity 或不同 digest。

### 文件落点与实施步骤

- 新增 `drafts.py`、`decisions.py`、`conflicts.py`，将 command validation、action append、rebase proposal 和 effective materialization 分开。
- 扩展 SQLite repository 的 draft/action/reconciliation transaction 和 query projection；actor 从 `RequestContext/RuntimeContext` 捕获。

### 测试策略

- expected revision 并发编辑、空 actor、改译名/改原名/抑制/重启、插件特例遮蔽、证据消失/恢复、新冲突、放弃后相同 revision 数值不误用 cache。
- 关闭草稿/取消冲突处理不改变 effective version；所有历史 action 可追溯且自动证据更新不产生 ManualAction。

### 依赖与边界

- 依赖 Story 04～06；尚不移动 effective pointer。

## Story 08：不可变发布、规范差异与冻结更新日志文档

- **详细设计**：[story-08-immutable-publish-diff-changelog.md](stories/story-08-immutable-publish-diff-changelog.md)

### 验收标准

- 发布在一个 SQLite transaction 内验证 build/draft/base/revisions/run permit，物化 proposed terms，计算 parent diff，冻结版本冲突/无证据/人工/诊断 projection，生成并持久化 `ChangeLogDocument`，最后移动 `(project, variant)` effective pointer。
- 首版对空库 diff；后续 typed changes 至少区分新增、抑制、译名修改、原名替换、scope/属性变化、冲突状态变化、重新启用和仅证据变化。
- diff、发布事实 projection、narrative document 或事务失败时旧 pointer 不变；stale 一律拒绝，partial 默认拒绝，显式 partial policy 保留为后续开关且必须结果导向确认。
- `ChangeNarrativeProjector` 确定性生成最终用户术语更新说明和维护者完整明细，固定 locale、template/schema version、message args 和 digest；不调用 LLM，不从导出文件或当前来源重算。
- 回退以历史内容为基础、以当前 effective 为 parent 发布新版本；不移动到旧 pointer 或删除中间历史。
- 发布事务成功后 Markdown/Excel artifact ledger 为 pending；外部导出失败不回滚版本，并可从同一 document ref 重试。

### 文件落点与实施步骤

- 新增 `versions.py`、`diff.py`、`narrative.py`、`publish.py`；将 diff engine、事实 projection、自然语言业务分类和 transaction coordinator 分开。
- 扩展 SQLite version/diff/document membership、effective pointer 和 ledger schema；版本内容、document 不受 report/cache GC。

### 测试策略

- 首版/相邻版本 diff、每种 typed change、只证据变化、人工来源标记、首次/最近发布版本、transaction 每一步 fault injection。
- stale/partial/base/draft/effective 并发冲突；回退后完整链保留；Variant A 发布不改变 Variant B pointer。
- narrative golden tests 验证最终用户文案不暴露内部字段、不误称游戏文本已修改，维护明细与 typed rows 一一对应。

### 依赖与边界

- 依赖 Story 07；ADR-034 接受后才开放正式 publish 组合。

## Story 09：统一质量报告、Markdown/Excel 更新日志与 artifact ledger

- **详细设计**：[story-09-reporting-renderers-artifact-ledger.md](stories/story-09-reporting-renderers-artifact-ledger.md)

### 验收标准

- `TerminologyReportSnapshot` 由 `BuildResultRef + pinned draft/no-draft identity/base/digest/revision` 冻结；构建后人工调整产生新 snapshot，不回写旧 BuildResult。
- UI preview 与质量 Excel 读取同一 snapshot ref；Excel 固定“构建摘要”“术语对照”“同名异译”“人工调整记录”四表，零数据仍有完整表头。
- 大表采用 write-only/流式分页，超过 Excel 行容量确定性拆表/分卷并在摘要记录清单，不静默截断；所有用户字符串做公式注入防护。
- Markdown 与 Excel 更新日志只读取同一 `ChangeLogDocumentRef`，都包含最终用户摘要和完整维护明细；布局可不同但 typed facts、计数和 narrative message 一致。
- 默认不覆盖同名用户文件；ledger 保存 document/snapshot digest、renderer/version、目标、状态、诊断和重试次数。质量报告失败不改变 BuildResult，更新日志失败不改变 version/document。
- 相同 document、格式和 renderer version 的重建内容可验证一致；版本仍存在时对应日志不被普通报告清理。

### 文件落点与实施步骤

- 新增 `reports.py`、`report_queries.py`；新增 `src/transbridge/persistence/terminology/report_snapshot.py` 保存 manifest/分页区段。
- 新增 `src/transbridge/application/terminology/renderers/quality_excel.py`、`changelog_markdown.py`、`changelog_excel.py`、`spreadsheet_safety.py`；只做布局/编码，不重算业务分类。
- 扩展 `artifacts.py` 的 overwrite/rename/retry policy 和生命周期查询。

### 测试策略

- 四表 schema、空表、5 万术语/5 千冲突流式输出、公式前缀、非法 Unicode/超长单元格、Excel 上限分卷、目标已存在与磁盘写失败。
- 从版本发布后修改/删除当前来源和 draft，再重建 changelog，验证仍由旧 document 得到同一语义内容。
- renderer parity 合同对比 Markdown/Excel 的稳定 manifest，而不是脆弱比较视觉布局。

### 依赖与边界

- 依赖 Story 04、07～08；不复用翻译报告轮转策略删除版本日志。

## Story 10：EffectiveTerminologyPort 与现有匹配器迁移

- **详细设计**：[story-10-effective-terminology-matcher-migration.md](stories/story-10-effective-terminology-matcher-migration.md)

### 验收标准

- `snapshot(project, variant, version|current)` 和 `resolve(term, TerminologyLookupContext)` 只返回已采用/人工确认且作用域适用的 ADR-027 `TermEntry` projection。
- unresolved、待复核和 suppressed 不进入强制匹配；suppression/shadow decision 会阻止 legacy fallback 在同一作用域重新引入该词。
- 插件特例只在 matching plugin context 覆盖项目全局项；旧 `load_all()/resolve_term()` 继续只返回 legacy 或全局兼容项，不泄漏插件特例。
- 无 effective version 时现有 dynamic/ParaTranz/JSON/CSV/Excel 优先级保持；存在项目版本时项目版本只在项目和作用域内为最高优先级，未覆盖项可 fallback。
- 翻译、后处理和向量索引的 context-aware 调用方迁移后通过合同测试；不反写或同步 ParaTranz。

### 文件落点与实施步骤

- 新增 `effective.py`、`src/transbridge/ai_translator/project_terminology_adapter.py`。
- 对 `TermDatabaseManager` 只增加窄 loader/context-aware 委托；把翻译与 proofread/postprocess 的 call sites 迁移到显式 lookup context，避免继续增长 745 行主类时优先抽出现有 matching facade。
- 在 bootstrap 注入 current Project/Variant effective loader；只在该 Project/Variant 存在已发布版本时启用，否则保持 legacy fallback。

### 测试策略

- global/plugin scope、suppression、未解决冲突、无版本 fallback、损坏版本只读诊断、Variant 切换、legacy parity。
- 翻译/后处理/向量索引合同验证同一 lookup context 得到一致 projection，旧无 context API 不出现插件特例。

### 依赖与边界

- 依赖 Story 08；首个成功发布版本后才对该 Project/Variant 启用。

## Story 11：横向对象导向术语工作台与渐进 projection

- **详细设计**：[story-11-terminology-workbench-progressive-projection.md](stories/story-11-terminology-workbench-progressive-projection.md)

### 验收标准

- 桌面窗口以横向导航和单一主工作区呈现“概览、术语、版本、报告”四个业务区域，不显示编号步骤、流程箭头或六个并列页签。
- 创建/更新术语库、不同译法决定、人工调整、发布、比较、恢复和导出作为对应内容旁的上下文操作；首次称“创建术语库”，已有内容称“更新术语库”。
- 空数据或任务运行时仍展示当前 Project、Variant、来源范围、已有术语和版本资产；内部阶段和运行诊断代码只在进度或可复制技术详情中出现。
- summary/terms/conflicts/manual/history/compare 都用异步 keyset pagination；不创建全量 Qt item。搜索、筛选、排序可取消并丢弃旧查询结果。
- 创建/更新、发布和导出只显示一条 TaskRuntime 进度、连续 heartbeat 和停止；点击/取消/切区/打开历史在 500ms 内反馈，主线程单段工作低于 200ms。
- partial、stale、抑制、回退和覆盖确认使用 FR5.16 的业务语言，明确影响、历史保留和恢复方式；发布成功但日志失败给出重试入口。

### 文件落点与实施步骤

- 保留 `presenter.py`、`view_models.py`、`paged_models.py` 和现有业务 view 的窄职责；以 `QStackedWidget` 横向 shell 重构 `window.py`，并按需新增 overview/terms/versions 组合 view，避免继续扩大窗口类。
- 从现有 workbench action/presenter 注入一个窄 launcher；不向 694 行 `ui/context.py` 或 692/592 行 Step 类加入工作流状态。
- 通过 application use cases 完成所有查询/命令；GUI、CLI、Agent、MCP 不直接读取 SQLite。首版完整交互只接 GUI，其他入口仅暴露安全的 use-case capability 或明确未支持诊断。

### 测试策略

- presenter/Qt tests 覆盖 preflight、全流程、空数据、partial/stale、取消、查询替换、cursor stale、发布/日志分离状态、回退/抑制文案和技术详情复制。
- 大页模型测试证明只持有可见窗口/有限 cache；窗口销毁释放订阅和后台 query ownership。
- UI responsiveness probe 在缩小数据集验证事件循环不被 parse/reduce/render 阻塞。

### 依赖与边界

- 依赖 Story 06～10；不得在 View 中实现统计、冲突选择、diff 或 artifact 命名规则。

## Story 12：性能收口、迁移演练与发布验证

- **详细设计**：[story-12-performance-migration-release-gates.md](stories/story-12-performance-migration-release-gates.md)

### 验收标准

- 在 Story 00 固定的参考设备和真实 adapter 数据上，逐项满足 FR5.16.33～FR5.16.40；报告区分冷启动、热缓存、LLM 等待、外部 I/O 和 renderer。
- 常规/压力构建、重复复用、≤10% 增量、5 次内存稳定性、查询/历史/比较、质量 Excel、5 万项 changelog、取消响应均留下可复验 manifest。
- 全量与增量 canonical digest 一致，性能优化不抽样、不截断、不跳过证据；超过格式容量明确分卷。
- Project source migration 和 terminology SQLite migration 在真实副本演练；失败可回退代码并保留 SQLite 只读资产，不能反向降级覆盖新数据。
- 性能、迁移和稳定性证据只作为 CI/发行验收输入，不进入应用运行时 composition，也不禁用本地用户操作；partial publish 由构建完整性业务规则独立控制。
- 相关聚焦、集成、性能、全仓 Ruff 和格式检查完成，发布候选 QA 记录兼容/回退和所有未满足预算。

### 文件落点与实施步骤

- 完成 `tests/performance/terminology/` budget assertions 和 `scripts/benchmark_project_terminology.py` manifest/report 输出。
- 新增 `docs/test-reports/terminology-benchmarks/<date>-release-candidate.md`；若任何 SHALL 预算未达成，不标记 Plan 或发布候选完成。
- 增加迁移副本、磁盘不足、数据库损坏、网络路径/无 WAL、崩溃恢复和 artifact retry 的端到端测试。

### 测试策略

- 聚焦：`uv run pytest tests/application/terminology tests/persistence/terminology tests/contracts/terminology -q`。
- 集成/UI：`uv run pytest tests/integration/terminology tests/ui/tools/terminology -q`。
- 性能：`uv run pytest tests/performance/terminology -m slow -q`，以及固定参考设备上的 benchmark 脚本常规/压力 profile。
- 回归：Project V2 migration/lifecycle、I/O adapters、TaskRuntime、ExistingTermSeeder、TermDatabaseManager、翻译/后处理报告相关测试。
- 静态：`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`、`git diff --check`。

### 依赖与边界

- 依赖全部前序 Story；完整压力基准不以普通 CI runner 的不稳定墙钟时间替代参考设备证据。

## 依赖顺序与可交付阶段

```text
S00 ──→ S01 ──┐
  └────→ S02 ─┴→ S03 → S04 → S05 → S06 → S07 → S08 ─┬→ S09
                                                       ├→ S10
                                                       └→ S11
                                      S09 + S10 + S11 ─→ S12
```

- **阶段 A（只读正确性）**：S00～S04，完成权威输入、全量构建和可查询事实存储，但不修改 effective terminology。
- **阶段 B（可靠运行与增量）**：S05～S06，完成缓存等价、进度、取消和 stale 屏障，可开放只读预览/质量报告试用。
- **阶段 C（可控维护与发布）**：S07～S09，完成草稿、版本事务、diff、冻结文档和派生工件。
- **阶段 D（产品闭环）**：S10～S11，接入实际术语消费和任务导向 UI。
- **阶段 E（发行验收）**：S12，所有已确认性能与迁移证据通过后才允许形成正式发布候选；不改变已安装应用的运行时能力。

## 需求追溯

- FR5.16.1～5：S01、S06、S11。
- FR5.16.6～13：S02～S05、S07。
- FR5.16.14～20：S06、S09、S11。
- FR5.16.21～22：全局边界、S01、S10。
- FR5.16.23～29：S07～S09。
- FR5.16.30～32：S11。
- FR5.16.33～40：S00、S04～S06、S09、S11～S12。

## 迁移、兼容与回退

- Project JSON 先备份再从当前 V2 source descriptor 迁移到下一 schema；不能证明的关系不猜测，保留来源并要求配置。旧 `primary/migration` 请求通过兼容 facade 转换，新写入只产生规范 registration/relation。
- SQLite 只承接 FR5.16 子域。Project/Variant/Session JSON、现有 snapshot、动态术语 JSON 和外部术语文件保持原格式与原生命周期。
- 无 effective project terminology version 时完全走旧术语来源；存在已发布项目版本时按 Project/Variant 启用 loader。SQLite 读取失败时安全回退 legacy，不删除、不降级。
- 首次发布以空项目版本为 diff 基线，legacy 术语只作为冲突参照/fallback；不会静默迁成人工决定。未来“导入到草稿”必须是单独显式命令。
- 代码回退不得把新 Project schema 或 SQLite 资产写回旧格式。旧版本无法理解的新 schema 应安全拒绝/只读，而不是覆盖。
- artifact export 是提交后的可重试副作用；删除失败文件或覆盖用户文件必须遵守显式 policy，不参与版本事务回滚。

## 主要风险与控制

- **来源关系迁移错误**：仅迁移可证明关系，其他项诊断；所有自动关系有 migration evidence 和可复核 projection。
- **双权威术语来源**：只有 `EffectiveTerminologyPort` 读取项目版本；匹配器不直接读 SQLite，legacy fallback 受 scope/suppression 控制。
- **增量结果漂移**：缓存可丢弃，所有语义输入进 digest，每个增量测试都和全量 canonical digest 对照。
- **SQLite 损坏/网络文件系统语义**：integrity check、backup、只读恢复、受控 journal mode；损坏库绝不退化为空库覆盖。
- **百万证据内存峰值**：per-source/stream fragment 入库后释放、SQLite reducer/keyset query、write-only renderer；单来源不具备 streaming capability 时不得宣称支持压力规模。
- **取消后迟到写入**：TaskRuntime run lease、cancellation token、CAS staging 和 repository expected revision 四层检查。
- **更新说明误导或漂移**：narrative projector 只读冻结 typed facts，模板版本化；用户层明确“术语库更新”而非“游戏文本已修改”。
- **新 God Manager**：按 capture/corpus/extraction/reducer/cache/draft/version/query/render 分文件，单模块/类达到仓库门禁时先做责任复审。

## 未决问题与明确假设

- **未决：ADR 状态**。ADR-034 仍为“提议”；本 Plan 假设其结构决策将被接受。若 source graph、SQLite 或冻结 narrative 被否决，须先回到 `bm-arch` 更新 ADR，再调整本 Plan。
- **未决：最终构建参考证据**。已记录本机 CPU、NVMe SSD、Windows 11、内存和正式场景产物，但正式场景后仍有代码修复，且未记录管理员级文件缓存清除与防病毒状态；最终工作树必须重新校准，不能复用旧 bundle 开门禁。
- **未决：streaming capability**。当前 adapter 多数返回 tuple 型完整结果；生产 preflight 在读取前拒绝超过 50 个来源、单源 64 MiB 或总量 256 MiB。只有实现并验证 streaming 后才可扩大宣称规模。
- **假设：首版 partial publish 默认关闭**。部分结果可预览、导出质量报告或保存非正式 candidate；正式 partial publish 只有在独立显式 policy、结果导向确认和 QA 证据完成后开放。
- **假设：首版正式交互入口为 GUI**。CLI/Agent/MCP 共享 use case 和权限边界，但不要求首版提供与 GUI 等价的交互界面，也不能直接绕过仓储规则。
- **假设：发布说明 locale 首版跟随当前产品 locale 并冻结在 document identity**。未来增加多 locale 时新增独立 document/artifact identity，不覆盖旧文档。

## 完成定义

- FR5.16 所有验收场景有自动化测试或明确的参考设备手工/性能证据，并能追溯到对应 Story。
- Project 迁移、SQLite transaction、取消/stale、人工决定保留、版本/回退、报告与 changelog 重建均有故障注入回归。
- UI、Excel、Markdown 不实现第二套统计/diff/narrative 规则；所有派生结果可追溯到冻结 ref 和 digest。
- 全量/增量等价、常规/压力性能、5 次内存稳定性和导出容量验证全部通过；未通过时 Plan 与发布候选保持未完成，但不得在用户运行时制造功能阻塞。
- 相关测试、Ruff check/format 和 `git diff --check` 通过；文档记录实际验证命令、兼容影响、迁移方式和安全回退路径。
