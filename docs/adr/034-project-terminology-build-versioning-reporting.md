# ADR-034：项目全来源术语构建、不可变版本与统一报告事实源

- **状态**：提议
- **日期**：2026-08-28
- **对应需求**：[FR5.16](../requirements.md)
- **关联 ADR**：[ADR-016](016-modular-monolith-application-composition.md)、[ADR-017](017-translation-io-kernel-v2.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-027](027-canonical-terminology-format-adapters.md)
- **局部取代**：ADR-006 对“项目持久化一律使用 JSON、SQLite 复杂度不必要”的判断只在 FR5.16 术语分析与版本仓储域被本 ADR 取代；Project/Variant/Session 的 JSON 权威资产继续受 ADR-018 约束

## 背景与约束

FR5.16 的输入不是当前工作台里的单个 `TranslationEntryCollection`，而是当前 Project 登记且启用的全部来源，以及当前激活 Variant 尚未写回来源文件的完整业务状态。当前实现已经具备可复用基础：

- Project V2 文档保存 `sources`、revision、active Variant，Variant V2 保存完整条目状态、来源 namespace/fingerprint 和 revision；
- Translation I/O Kernel 以 `FormatAdapter`、`ParseRequest`、`ParseResult` 和显式 capability 作为格式边界；
- `ExistingTermSeeder` 已实现名称字段确定性抽取、普通文本 LLM 抽取、NFKC/空白规范化、原词与译词同证据定位、取消和基础冲突检测；
- ADR-027 的 `TermEntry` 是当前匹配器和 JSON/CSV/Excel/ParaTranz 适配器共享的规范交换模型；
- ADR-019 的 `TaskRuntime` 已定义 owner、取消屏障、互斥终态和迟到结果隔离。

这些资产不足以直接承担项目术语库：Project source 目前只有宽松描述字典，`primary`/`migration` 角色不能无歧义表达插件、XML 和 STRINGS 的关系；`TermEntry` 不能表达多证据、人工决定、冲突组、草稿、版本和差异；现有动态术语库是原地更新的可变 JSON；现有报告按运行分别渲染，不能作为 FR5.16 的历史事实来源。

FR5.16 还确认了最高 100 万条双语证据、20 万条术语、2 万个冲突组和 50 个历史版本，以及筛选、比较、重复构建和导出的明确时限。由此可以推断，继续把每个版本作为完整 JSON 加载到内存、再由 UI 全量建表，不具备可验证的性能余量。该推断必须由 FR5.16 基准在实现阶段验证；本 ADR 不把尚未测量的速度写成既成事实。

## 决策

### 1. 建立独立的项目术语域，不扩张 AppContext、Collection 或 TermEntry

FR5.16 作为模块化单体中的独立业务域实现。逻辑依赖方向为：

```text
GUI / CLI / Agent / MCP adapters
                ↓
application.terminology use cases
                ↓
terminology domain + application ports
      ↙              ↓               ↘
Project/Variant   FormatAdapter   TerminologyRepository
read ports        / LLM ports     / artifact renderers
```

`AppContext` 只接收可展示 projection 和任务事件，不参与来源枚举、证据合并、冲突裁决或版本提交。`TranslationEntryCollection` 可作为格式适配器产生的临时读模型，但不得成为项目范围或正式术语库的权威所有者。

ADR-027 的 `TermEntry` 保持“供匹配和格式互换使用的单条有效术语 projection”。版本、证据和人工操作不塞入其 `metadata` 伪装成领域模型；项目术语域通过显式适配器把已生效版本投影为 `TermEntry`。

计划新增的包边界如下，文件拆分由后续 plan 在仓库规模门禁内确定：

```text
src/transbridge/application/terminology/      # 纯应用合同、构建/草稿/发布/查询 use case（计划新增）
src/transbridge/persistence/terminology/      # SQLite repository 与 schema migration（计划新增）
src/transbridge/ui/tools/terminology/         # 任务导向 UI adapter 与分页 projection（计划新增）
src/transbridge/bootstrap/                    # 现有 composition root 注入上述端口（现有，计划扩展）
```

应用与领域代码不得导入 PyQt、openpyxl、具体 LLM client 或具体 parser。Excel/Markdown 是 artifact renderer adapter，不拥有统计规则。

### 2. Project 是来源清单与关系的唯一权威，Variant 是当前译文状态的唯一权威

Project source descriptor 从现有宽松字典升级为受校验 `SourceRegistration`，至少包含：

- 稳定且与内容 fingerprint 分离的 `source_id`；
- `enabled`、`format_id`、规范化位置和当前已知 fingerprint；
- 项目内来源种类，以及该来源是否能独立提供双语内容；
- 可选显示名、插件作用域和格式选项。

来源关系作为独立、有稳定 `relation_id` 的 `SourceRelation` 图保存，而不是 descriptor 中的单个 target 字段。关系边至少区分 `translation_for` 和 `localized_member_of`，保存 from/to source、对齐 policy 及其版本；同一来源可进入多条关系，才能表达多插件、多 XML 和 N:M 关系。独立双语身份由 `SourceRegistration` 显式声明，不通过缺少关系边推断。当前以 fingerprint 派生 namespace 的兼容来源不能直接充当长期关系 ID；迁移时为其分配稳定的项目内 `source_id`，同时保留原 namespace/fingerprint 作为追溯信息。

文件名相似、目录相邻、插件 master 引用或工作台同时打开都不是来源关系证据。只有 Project 中的显式关系可将插件原文、迁移源译文、XML 或 STRINGS 组装成一条来源链。关系缺失时，自包含双语来源可按明确的 independent 身份参与；需要依赖目标来源才能成对的来源被诊断并跳过。多个可能目标时返回歧义诊断，不自动选择。现有插件 adapter 的 sibling `Strings/` 自动发现必须在 FR5.16 构建请求中关闭；只有单独登记并进入关系图的 STRINGS 来源可被纳入。

构建开始前由 `TerminologyBuildInputPort` 在 Project lifecycle/repository 的同一一致性边界内捕获一个不可变 `BuildInputSnapshot`，不得先后读取可变的 active/project/source 对象再自行拼接：

```text
project_id + project_revision
variant_id + variant_revision
sorted source descriptors + source relationships
source snapshots/leases + actual fingerprints + adapter/version/capability
build policy + normalization/extractor/config digests
current effective terminology version/content digest
draft identity (or no-draft sentinel) + base version/content digest
draft revision + decision-set digest
```

来源枚举直接读取 ProjectAggregate/Repository，Variant 读取完整 `VariantSnapshot`；不得读取 `AppContext.slots`。所有来源通过 ADR-017 的 read capability gate 和 `ParseRequest` 进入。Variant 状态只按完整 `EntryKey` 与 fingerprint 兼容规则物化，禁止以 local key 或扫描顺序覆盖来源结果。

捕获后源文件由内容寻址的不可变 blob 或受生命周期管理的 lease 支撑解析，adapter 不得在建立 fingerprint 后重新打开原路径读取另一份内容。暂不能提供稳定 lease 的兼容 adapter 至少在解析前后重算摘要并把变化视为 stale/failed，不能声称稳定快照。构建完成和发布前再次校验 Project/Variant revision、来源 fingerprint 与有效版本基线；任何变化都会使结果进入 `stale`，不得冒充当前完整结果。

### 3. 自动证据、术语决定、冲突与人工操作采用不同聚合

项目术语域至少使用下列不可变或版本化概念：

- `BilingualEvidence`：一条有效双语语料，保存项目/Variant、完整来源链、namespace、EntryKey、original、translation、context、stage、插件作用域和来源 fingerprint；
- `TermCandidate`：从一条或多条证据得到的规范化术语对，记录 `deterministic_name` 或 `llm_text` 提取方式；
- `ConflictGroup`：同一规范化原名下的多个非空规范化译名及其证据、风险和处理状态；
- `TermDecision`：面向使用的术语决定，保存原名、译名、项目全局或插件作用域、变体、备注、确认状态和抑制状态；
- `ManualAction`：追加式人工操作，保存操作前后值、操作者、原因、基准版本和 replacement/suppression 关系；
- `BuildResult`：某个稳定输入的不可变分析结果，保存摘要、候选、冲突、排除、诊断、完整性、缓存复用信息和排序键；
- `TerminologyVersion` 与 `CanonicalDiff`：不可变正式版本和发布前持久化的规范差异；`CanonicalDiff` 只表达父子版本之间的 typed change rows，不兼任发布时完整状态快照。
- `TerminologyReportSnapshot` 与 `ChangeLogDocument`：前者固定质量报告所需的构建/人工决定视图；后者固定该术语版本的最终用户摘要、完整维护明细、未解决/新增冲突、当前无证据术语、人工调整摘要和发布业务诊断。renderer 不在输出时重建业务事实。

证据和决定分别保存。重新构建只能新增、撤销或更新自动证据及其统计，不得改写 `ManualAction` 或人工字段。人工“删除”写为抑制；修改原名写为新 `TermDecision` 并以 `replacement_of` 关联旧术语，旧证据仍可追溯。证据消失时人工决定保留，并投影为“当前无证据/可能过期”。每条人工操作必须从 `RuntimeContext` 固化非空 actor identity；自动证据更新不得借用该身份伪装成人工操作。

稳定身份采用带 schema namespace 的确定性摘要：

- `evidence_id` 由项目、Variant、来源链、EntryKey 和规范化双语内容生成；
- `candidate_id` 由 evidence set、规范化术语对、作用域和提取方法/算法版本生成；
- `term_id` 由项目、Variant 线、作用域和规范化原名生成，译名修改不改变 ID；原名替换产生新 ID；所有 repository 唯一键、`ManualAction`、diff 和报告引用都必须保留该 Variant 线身份；
- `conflict_group_id` 由项目、Variant 线和规范化原名生成；
- `build_key` 由 `BuildInputSnapshot` 的 revisions、排序后 fingerprints、关系、配置和算法版本生成。

运行 `run_id` 仍是一次任务的观测身份，不参与业务稳定 ID。`build_key` 的规范序列化排除时间戳、UI 顺序、临时路径和 run ID。碰撞检测、schema 版本和规范排序是 repository 合同的一部分，不依赖数据库自增 ID 或扫描顺序。

draft revision 只在一个稳定 draft identity 内有意义，不能单独证明人工决定相同。放弃后重建、从历史版本重新建稿或 rebase 都必须产生新的 draft identity 或不同的 base/decision digest；缓存校验需比较这些字段，不能因 revision 数值恰好相同而复用另一份 `BuildResult`。

### 4. 构建采用“捕获—组装—抽取—归并—协调—冻结”流水线

```text
capture authoritative input
  → parse registered sources through FormatAdapters
  → assemble project-level bilingual evidence from explicit relationships
  → apply eligibility policy
  → deterministic extraction
  → optional LLM extraction
  → normalize / deduplicate / group conflicts
  → reconcile with effective version and manual decisions
  → freeze immutable BuildResult
```

资格策略首版固定为原文和译文均非空，并排除 hidden 与 questionable；已锁定、已检查或已审核的非空译文可参与。只有原文的条目不调用自动翻译来制造证据。

规范化规则版本化并进入 `build_key`：原名使用 Unicode NFKC、连续/首尾空白规范化和 casefold；译名使用 NFKC 和空白规范化。不得删除标点或用语义相似度合并。确定性排序至少以规范化原名、作用域、规范化译名和稳定 ID 为键。

现有 `ExistingTermSeeder` 的安全行为通过 adapter 复用，不直接把它的动态库写入路径当作提交边界。LLM 候选必须能把原词和译词定位到同一 `BilingualEvidence`；请求结果晚于取消 lease、不能反序列化或无法定位时只形成诊断。LLM 不可用、关闭或用户选择跳过时，构建继续冻结确定性结果并记录未执行原因。

只复用 `ExistingTermSeeder` 已验证的资格判断、规范化、稳定分批和同证据定位规则；不得复用其单 collection 输入、直接 `dynamic_db` 保存、自建 `ThreadPoolExecutor`、整库已有文本术语即跳过 LLM 或冲突整组丢弃的生命周期。内部来源与 LLM 调度使用 Composition Root 注入的有界 quota executor。

同原名多译名永远先形成冲突组。出现次数、来源优先级或扫描顺序只能作为报告信息，不能自动选定胜者。未处理冲突及其候选不进入强制匹配用的有效术语 projection；人工确认的统一译名或插件特例按显式作用域生效。

TaskRuntime 的执行终态与分析质量分开表达：任务仍只有 `completed/failed/cancelled` 互斥终态；`BuildResult` 另有 `completeness=full|partial`、`freshness=current|stale` 和 `llm=performed|skipped|unavailable|partial`。取消任务不提交或替换正式 `BuildResultRef`；已完成分片可作为 run-scoped 临时分析供只读预览，只有新任务重新验证输入和分片后，才可显式冻结为非正式 candidate。

### 5. 每个 Project/Variant 形成独立版本线，草稿可变、发布版本不可变

当前生效版本指针按 `(project_id, variant_id)` 保存，而不是全项目共用一个指针。这样切换 Variant 不会把另一 Variant 的译名作为当前术语静默消费。版本仍是项目级资产，记录项目与 Variant 的身份和 revision。

每条版本线包含：

- 一个可选 mutable draft，绑定 `base_version_id` 和 draft revision；
- 任意数量不可变 `TerminologyVersion`；
- 一个 current effective version pointer；
- 每个版本的 parent、内容摘要、完整性、构建引用、规范差异、`ChangeLogDocumentRef` 和独立 artifact ledger 引用。版本业务事实不可变；导出成功/失败、路径和重试次数属于可更新的运行 ledger，不反向修改版本内容或 `ChangeLogDocument`。

编辑只改变 draft 并追加 `ManualAction`。自动保存使用 expected draft revision，不创建正式版本。draft 打开后若 effective version 或 Variant 已变化，必须显式 rebase 或放弃 draft，不得静默替换基线。发布执行：

```text
validate build/draft/base/revisions and commit guard
  → materialize proposed effective terms
  → compute canonical diff against parent (first version against empty set)
  → freeze version-bound conflict/no-evidence/manual/diagnostic projections
  → project and freeze immutable ChangeLogDocument
  → persist version + term state + evidence refs + diff + ChangeLogDocument
            + per-format artifact ledger(pending)
  → atomically move current effective pointer
  → commit transaction
  → render Markdown and Excel changelogs from the persisted ChangeLogDocumentRef
```

规范差异、版本发布事实投影或 `ChangeLogDocument` 无法生成、校验或持久化时事务整体失败，旧有效指针不变。版本事务成功后，Markdown/Excel 更新日志分别自动尝试；任一格式导出失败不回滚版本，artifact ledger 记录失败目标、诊断与重试状态。重建始终读取该版本已保存的 `ChangeLogDocumentRef`，不重新扫描来源，也不读取当前 draft、冲突或人工决定。

“回退”实现为以历史版本内容创建一个以当前有效版本为 parent 的新版本，并产生正常 diff；不得移动指针后删除中间历史。选择历史版本作为编辑基线同样创建新 draft，不修改历史版本。

完整构建可按正常发布策略成为 effective version。部分完成或已停止结果默认只能预览、导出质量报告或保存为非正式 candidate；若产品保留“部分完成发布”，必须使用单独的显式 commit policy、结果导向确认和完整性标识，不能复用默认发布动作。`stale` 结果在任何策略下都不能成为当前有效版本。

### 6. 术语仓储使用按项目隔离的 SQLite adapter，Project/Variant 仍使用 JSON

新增 `TerminologyRepositoryPort`，use case 不暴露 SQL、路径或表。首个 adapter 使用 Python 标准库 `sqlite3`，按 Project 隔离术语分析、草稿、版本、差异和 artifact metadata。采用该 adapter 的原因是：

- 单事务可原子写入版本、差异和 effective pointer；
- 可通过索引和 keyset pagination 查询 5 万至 20 万术语，而不要求 UI 加载全表；
- 自动证据、人工操作、版本 membership 和 artifact 状态适合关系约束；
- 内容摘要和规范状态可去重，逻辑完整快照不等于每版复制完整 JSON；
- `sqlite3` 随 Python 提供，不新增运行时依赖。

数据库至少启用 schema version、外键、唯一约束、校验摘要和受控 migration/backup。日志模式由 adapter 根据已验证的本地文件系统能力选择；不得假定网络路径支持 WAL 或原子 rename。正式语义是不可变完整版本，即使物理实现采用内容寻址行和 version membership 去重，也不得让后续写入改变历史查询结果。

SQLite 只承接 FR5.16 的查询密集型子域，不替代 ADR-018 的 Project/Variant/Session JSON repository。术语 effective pointer 保存在同一术语数据库事务内，从而避免发布时跨 JSON 与 SQLite 更新两个权威指针。Project 生命周期通过端口定位该资产，具体磁盘位置由现有 persistence root/`RepositoryPaths` 管理，不暴露给 UI。

### 7. BuildResult 与冻结的报告投影组成唯一质量事实链

`BuildResult` 冻结后只通过 `BuildResultRef` 读取。摘要计数、排除原因、冲突风险、构建捕获时的人工一致性和稳定排序在冻结前计算一次并持久化；UI 与 Excel renderer 不得各自重新扫描候选或实现第二套冲突算法。

构建后又发生人工调整时，不回写旧 `BuildResult`。报告 use case 以 `BuildResultRef + pinned decision/draft identity/base/digest/revision` 冻结新的 `TerminologyReportSnapshot`；不存在 draft 时使用显式 no-draft identity，而不是省略身份字段。应用内预览和四表 Excel 必须共同切换到同一个 report snapshot。

版本更新日志使用另一条事实链：发布事务把 `CanonicalDiff`、版本绑定的未解决/新增冲突、当前无证据术语、纳入本版本的人工调整摘要和发布业务诊断交给确定性的 `ChangeNarrativeProjector`，冻结为 `ChangeLogDocument`。该 projector 在导出文件之前工作，不解析或改写已经生成的 Markdown/Excel，也不调用 LLM。它只做业务分类、用户语言转换和稳定排序，不得增加、删除或重新推断 diff 事实。

`ChangeLogDocument` 同时保存两层 projection：

1. **最终用户术语更新说明**：使用“新增统一译名”“调整推荐译名”“不再推荐”“仅在某插件中使用”等业务类别和自然语言句式，不暴露 change type、term ID、digest 或数据库字段；
2. **翻译者/维护者完整明细**：保存全部 typed change rows、精确前后值、作用域、证据变化、冲突、人工操作和诊断，满足审计与逐项核对。

文档同时固定 narrative schema、发布说明 locale、模板版本/摘要和稳定 message arguments；正式自然语言不在 renderer 中临时推断。若未来支持同一版本的多 locale 更新说明，每个 locale 使用独立 document/artifact identity，不能用另一 locale 覆盖既有文档。

最终用户层必须明确这是“术语库更新说明”，不能把术语决定冒充已经写入发布成品的文本变化。没有实际翻译产物差异证据时，使用“后续翻译将优先采用”“不再推荐使用”等表述，不能声称“游戏文本已经修改”。真正面向汉化包成品的发布说明需要独立的 `TranslationReleaseDiff → UserReleaseNotesDocument`，不属于本 ADR。

单一事实来源不要求把百万级证据永久装入一个 Python 对象。Repository 提供绑定同一 snapshot ref 的只读 summary、paged term rows、paged conflict rows、manual action rows 和 evidence drill-down；所有查询携带同一 snapshot revision/content digest。分页使用 snapshot-bound keyset cursor；cursor 至少绑定 snapshot、query fingerprint、稳定排序键和最后一个稳定 ID，snapshot 或查询条件变化时返回 `CURSOR_STALE`，不得在新快照上继续旧页码。

质量 Excel renderer 固定生成“构建摘要”“术语对照”“同名异译”“人工调整记录”四张表，零行时仍写表头。大数据使用 write-only/流式写出并遵守 Excel 行容量；超过容量时按确定性分卷或拆表，并在摘要记录分卷清单，不截断。所有用户来源字符串在写入单元格前执行公式注入防护，不能把以公式前缀开头的原文、译文或备注当作公式执行。

更新日志 renderer 只消费 `ChangeLogDocumentRef`。该文档可以是带内容摘要的不可变 manifest，引用绑定同一 document digest 的 summary、consumer narrative、typed change rows、conflict/no-evidence、manual summary 和 diagnostic 分页区段，不要求把 5 万条差异同时装入 Python 对象。Markdown 首屏和 Excel 首表优先展示最终用户摘要，完整维护明细放在后续章节或工作表；两种格式语义内容一致，但布局不必相同。

质量报告渲染失败不改变 BuildResult；更新日志渲染失败不改变已提交版本或 `ChangeLogDocument`。导出路径使用显式 overwrite/rename policy，默认不静默覆盖用户文件。artifact ledger 记录内容摘要、renderer/version、目标与错误，使相同 document 和 renderer 版本的重建可验证一致；外部文件导出错误只进入 ledger 和发布结果，不反向写入不可变文档。

### 8. 已发布术语通过窄端口接入现有匹配器

新增带查找上下文的 `EffectiveTerminologyPort`：

```text
snapshot(project_id, variant_id, version_id | current) -> EffectiveTerminologySnapshot
resolve(term, TerminologyLookupContext(project, variant, source/plugin scope)) -> TermEntry | None
```

snapshot 包含可正式使用的已采用/人工确认术语，以及按作用域生效的 suppression/shadow decision；“待复核”可以出现在质量报告，但不进入强制匹配 projection。未解决冲突和已抑制术语不投影为 `TermEntry`，但抑制决定必须阻止 legacy fallback 在同一作用域把该术语重新引入。插件特例只在匹配 lookup context 时覆盖项目全局项。允许项再投影为 ADR-027 `TermEntry`，供 `TermDatabaseManager`、翻译、后处理和向量索引消费。

当前 `TermDatabaseManager.load_all()/resolve_term()` 是无 Project/plugin context 的平面兼容 API，不能直接加载插件作用域版本后依赖 last-write-wins。迁移期新增 context-aware resolve/match 路径并先切换翻译与后处理调用方；旧 API 继续只返回 legacy 来源或项目全局兼容项，绝不泄漏插件特例。项目版本 loader 经合同测试后再进入默认组合根。

当当前 Project/Variant 尚无 effective version 时，现有动态/ParaTranz/JSON/CSV/Excel 来源优先级继续工作。存在 effective version 后，项目版本作为项目内最高优先级来源，但只覆盖匹配作用域内的术语；现有来源仍可作为构建时权威冲突参照和未覆盖项 fallback。不得把项目版本反写或同步到 ParaTranz，除非另一个显式同步需求授权。

首次发布以空项目版本为 diff 基线，同时读取现有有效术语作为冲突参照；不静默把所有 legacy 动态术语标记为人工决定。若需要迁入，使用显式“导入到草稿”操作并记录来源和人工确认。

### 9. 所有长阶段接入 TaskRuntime，正式写入受 revision 与 run lease 双重保护

至少定义 `terminology.build`、`terminology.publish`、`terminology.report.render` 和 `terminology.changelog.render` workload。GUI 的线程或 Qt signal 只是 ADR-019 TaskEvent adapter，业务 workload 不继承 `QThread`。

构建任务的 `JobSpec.input_fingerprint` 使用 `build_key`，owner 固定 Project/Variant。进度阶段使用稳定业务阶段和数量字段，外部 LLM 的提交数、完成数、等待、重试和耗时单独报告。两秒没有数量进展时仍发当前阶段/对象 heartbeat。

取消时 TaskRuntime 先进入 `cancelling`，停止补充新来源和 LLM 队列。所有 worker、解析 fragment、LLM 请求和 renderer 结果在进入 BuildResult/draft/version 前验证 cancellation token、run lease 和 expected revision；迟到结果只能写入 run-scoped/CAS staging 或记录诊断，不能进入预览、draft 或 version。UI 可在 500ms 内投影“正在停止”，用户可见任务在 3 秒内进入已停止；不可中断外部调用被隔离清理，不能延迟写入。

发布的重计算阶段可在后台执行，但最终 repository transaction 只有在 TaskRuntime run commit permit 与 repository 的 Project revision、Variant revision、source graph/fingerprint、active/base version、draft revision 和 build freshness optimistic guard 全部仍匹配时才提交。TaskRuntime 屏障是必要条件，不能替代业务 revision 校验。

### 10. 精确复用和增量构建由内容键驱动，结果必须与全量构建等价

Repository 保存三层可丢弃缓存：

1. `build_key → BuildResultRef`，完全相同输入直接校验并复用，不重复调用 LLM；
2. `source fingerprint + format adapter/version + parse options → parsed fragment`；
3. `evidence digest + extractor/prompt/profile/model/config digest → extraction fragment`。

来源关系或对齐策略进入关系组件 digest。部分来源变化时只重算受影响的关系连通分量及其 parse/evidence/extraction fragment，但全局 normalization、conflict grouping、人工决定协调和摘要必须由同一个 global reducer 基于新旧 fragment 的完整逻辑集合重新得到确定性结果。增量和全量必须走同一 reducer 并比较 canonical digest，不能用抽样、截断、“只追加不撤销”或顺序敏感计数换取速度。

为满足 100 万证据的内存预算，来源按分片解析、持久化后立即释放，不在顶层 job 同时保留所有 `ParseResult`、`SourceSnapshot` 和 Python evidence 对象。现有 tuple 型 adapter 可以按来源串行包装；单来源也可能超预算的格式必须实现并通过 ADR-017 streaming capability 后，才可宣称支持压力基准。global reducer 使用 SQLite 索引、分桶或受控外排实现，具体策略由性能 plan 和基准确定。

缓存不是历史事实来源，可按空间策略清理；正式版本、CanonicalDiff、ManualAction 和与版本绑定的更新日志不得被自动 GC，也不随普通缓存或质量报告轮转删除。空间不足在发布前预检，transaction 失败不留下半版本。

### 11. UI 使用任务语言和渐进 projection，不暴露内部对象

UI 围绕“构建术语库、检查异译、人工调整、发布新版、查看历史、导出报告/更新日志”组织。默认页面读取 summary 和首屏分页，不创建包含全部术语/证据的 Qt item；搜索、筛选、排序和版本比较通过 repository query port 执行并支持取消或替换旧查询。

内部 `build_key`、fingerprint、namespace、canonical diff、tombstone、run_id 和 SQL 状态只在“来源详情/技术详情”中按需投影。主界面使用 FR5.16 已确认的业务用语，如“本次更新”“不再使用”“当前项目中暂未找到使用位置”。错误先给结果、数据影响和建议动作，再提供可复制诊断。

GUI、CLI、Agent 和 MCP 共享同一 use case 与权限校验。首版可以只向 GUI 暴露完整交互，但其他入口不能通过自行读取术语数据库绕过版本、作用域或冲突规则。

## 关键契约与错误语义

| 边界 | 成功值 | 关键失败/冲突语义 |
|---|---|---|
| `capture_build_input` | `BuildInputSnapshot` | 无 Project/Variant、无启用来源、capability 不足或 Variant/source fingerprint 不兼容时返回结构化先决条件/冲突 |
| `build` | immutable `BuildResultRef` | 单来源失败可为 partial；全部不可读为 failed；取消为 cancelled；revision/fingerprint 改变为 stale result，不可发布 |
| `open/save_draft` | revisioned `DraftRef` | base/effective version 或 draft revision 变化时拒绝覆盖，并保留双方内容供用户处理 |
| `publish` | `TerminologyVersionRef + ChangeLogDocumentRef` | diff、发布事实投影、ChangeLogDocument 或 transaction 失败不移动 effective pointer；stale 构建拒绝；partial 默认拒绝或要求显式 policy |
| `render_quality_report` | artifact refs | renderer 失败不改变 BuildResult、draft 或版本 |
| `render_changelog` | Markdown/Excel artifact refs | 失败只更新 artifact ledger，可从持久化 ChangeLogDocumentRef 重试，不回滚版本或重算业务事实 |
| `effective_snapshot` | version-bound `TermEntry` projection | 不存在版本时返回“无项目版本”而非空库覆盖 legacy 来源；损坏版本进入只读诊断，不猜测恢复 |

## 备选方案

### 扩展 DynamicTermDatabase 并继续原地写 JSON

改动小，但无法表达不可变历史、多证据、冲突组、人工操作、规范差异和原子 effective pointer，也不能支持分页查询，拒绝。

### 每个版本保存一份完整 JSON/Excel 快照

可读性好，但 20 万术语、证据关系和 50 个版本会产生写放大，并要求 UI/比较器重复加载大文件。Excel 应是派生工件，不应成为事务存储，拒绝作为权威仓储。

### 把 FR5.16 状态加入 Project/Variant JSON

会使 Project/Variant 聚合承担查询密集的另一生命周期，并让版本发布跨多个 JSON 文件更新，扩大 ADR-018 的事务和迁移风险，拒绝。

### 直接扩展 ADR-027 TermEntry

会把匹配交换模型变成含版本、证据、人工审计和报告字段的上帝对象，破坏现有格式适配和调用兼容，拒绝。

### 使用事件溯源重建所有版本

ManualAction 适合追加审计，但自动证据量巨大，且 FR5.16 要求快速历史浏览与确定性重建。采用不可变版本快照语义、规范 diff 和人工 action log 的混合模型，不引入全面事件溯源。

### 直接把 CanonicalDiff 渲染为用户更新日志

实现简单，但 diff 的 change type、内部标识和逐行前后值适合审计，不足以表达当前仍未解决的冲突、当前无证据术语和最终用户能理解的影响；也容易把术语规范变化误写成成品文本已经变化。保留 diff 作为审计事实，增加确定性 narrative projection 和冻结的 `ChangeLogDocument`。

### 导出 Markdown/Excel 后再转写，或使用 LLM 生成发布说明

文件后处理会让 Markdown 与 Excel 产生两套事实和丢失 typed fields；LLM 文案不可复现，并可能夸大实际影响。拒绝。业务转写必须在 renderer 之前由版本化、可测试的 `ChangeNarrativeProjector` 完成。

## 影响与风险

- **正面**：项目范围不再受 UI 已打开集合影响；自动证据与人工决定可独立演进；版本、回退、报告和更新日志共享可审计事实；大表可分页和索引查询。
- **成本**：需要新增项目 source relationship schema、SQLite migration、版本 repository、分页 projection 和现有术语匹配 adapter。
- **风险：双权威术语来源**。缓解：`EffectiveTerminologyPort` 是项目版本的唯一消费端口；`TermDatabaseManager` 只通过 loader adapter 合并，不自行读取数据库。
- **风险：SQLite 文件损坏或迁移失败**。缓解：schema 校验、事务、备份、校验摘要、只读恢复和 quarantine；不得以空库覆盖。
- **风险：内容寻址摘要或 normalization 变化导致身份漂移**。缓解：所有算法带 schema/version，升级通过显式 migration 或新 build，不静默重解释历史。
- **风险：增量缓存产生与全量不同结果**。缓解：缓存可丢弃、digest 覆盖所有语义输入、合同测试比较 canonical digest，异常时回退全量构建。
- **风险：更新说明文案与审计事实漂移或误导最终用户**。缓解：narrative projector 只接受冻结的 typed facts，使用版本化确定性模板并保存 document digest；最终用户层明确术语库与翻译成品的边界，完整明细始终保留原始 diff 语义。
- **风险：术语域变成新 Manager 大类**。缓解：按 input capture、corpus assembly、analysis、draft/version、query、render ports 拆分，每个模块保持单一责任。

## 迁移与回退

1. 先扩展并迁移 Project source descriptor，为现有 primary/migration 来源生成稳定 `source_id`；只有可证明唯一的关系才自动迁移，其他关系进入待配置诊断。
2. 引入纯领域模型、normalization/stable-ID 合同和内存 repository，复用现有 `ExistingTermSeeder` 作为 extractor adapter，建立全量构建基线。
3. 引入 SQLite adapter、schema migration、备份与分页查询；用 FR5.16 常规/压力数据验证后再启用为正式仓储。
4. 接入 TaskRuntime 和 GUI 预览，初期只允许只读分析与质量报告，不改变现有动态术语库。
5. 启用 draft、diff 和 publish；项目没有 effective version 时仍走旧术语来源。首个成功版本发布后，才为该 Project/Variant 启用项目版本 loader。
6. 启用历史、回退、`ChangeNarrativeProjector`、冻结的 `ChangeLogDocument` 与自动更新日志；以最终用户摘要和完整维护明细的合同测试验证两种 renderer 语义一致，再确认版本/日志生命周期、重建和空间预检，之后才开放部分完成发布策略。
7. 回退代码版本时，无法读取项目版本则继续使用旧术语来源；SQLite 资产保持只读，不反向降级或删除。若新构建器失败，已有 published version 仍可通过稳定 projection 被消费。

正式实施计划开始前须按 FR5.16.33 固定参考 CPU、基准数据集、冷/热缓存流程、LLM 排除计时和产物保留位置。该校准只建立可复验测量条件，不改变已经确认的性能预算。

## 2026-08-28 实施一致性复审

当前实现与本 ADR 的核心方向一致：Project source graph 与 Variant 是权威输入；术语域独立于 PyQt；每 Project SQLite、每 Project/Variant version line、不可变版本/报告/changelog、TaskRuntime commit guard 和有效术语窄端口均已落入生产 composition。发布候选 evidence 仅由 CI/发行工具消费，不参与 GUI 或运行时能力判断；published terminology 已进入 translator、term database、vector 和后处理消费链。

以下证据尚不满足接受条件，因此 ADR 状态继续为“提议”：

- 2026-08-28 regular/stress 20 场景与 UI supplemental 已完成并聚合，但 regular/stress 峰值额外 RSS 分别约 1.21/4.79 GiB，超过 1/2.5 GiB；原始 regular 查询、历史、比较和 changelog 也超预算。
- SQLite query/history 已在正式运行后改为标量 ref + keyset SQL，不再先解码完整父 payload；独立五轮复测已进入 0.5/2 秒预算。相邻版本 compare 使用发布时持久化且 digest-bound 的 canonical diff 快路，非相邻版本仍完整重算；这些最终代码尚未重跑整套正式场景，changelog 和增量相对全量耗时仍有未关闭证据。
- 当前 tuple adapter 没有完成通用 streaming。生产 preflight 已在任何读取前拒绝超过 50 个启用来源、单源 64 MiB 或总量 256 MiB，并返回 `TERMINOLOGY_STREAMING_REQUIRED`；来源文件按 1 MiB 分块哈希并保留路径快照，不再常驻原始 bytes。完整业务对象图仍约有 28 倍放大，因此不会宣称支持 200-source stress 生产输入，regular 内存预算也仍待最终测量。
- partial publish 仍是明确非目标并由构建完整性业务规则拒绝；完整结果发布不依赖它。

迁移/回退语义已有 Project v2→v3 与 SQLite 副本、future schema 只读、corrupt 保留、无 WAL、崩溃回滚、发布事务和 artifact retry 测试。代码回退时无法读取项目版本便回退 legacy，SQLite 与已发布资产保持只读，不反向降级或删除。只有最终构建身份上的所有 SHALL、迁移和 release checks 形成 digest-bound 证据后，才能重新评审 ADR 接受状态；该证据不进入最终用户运行时。
