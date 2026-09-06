# ADR-039：可切换译名方案与非破坏性译文投影

- **状态**：已接受（首版于 2026-09-06 实现）
- **日期**：2026-09-06
- **对应需求**：[FR5.18](../requirements.md)
- **关联 ADR**：[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-027](027-canonical-terminology-format-adapters.md)、[ADR-034](034-project-terminology-build-versioning-reporting.md)、[ADR-035](035-authoritative-project-mutations-and-recoverable-save.md)

## 背景与约束

同一份翻译内容可能需要遵循多套目标译名约定，例如 Skyrim 的不同本体汉化、地区或社群译名、团队/发行规范、系列旧译与新译，以及整合包兼容约定。这些成品原则上只有受控术语不同，普通正文、人工润色、stage 与来源状态应完全一致；用户需要在一个 Project 中快速切换后预览、交给 AI 使用或写出成品。

当前实现的权威边界与该目标存在四个差距：

- `VariantEntryState.translation` 保存最终普通字符串，没有术语出现位置或逻辑术语身份；
- `term_id`、构建、草稿、正式版本和 effective pointer 均绑定 `(project_id, variant_id)`，复制 Variant 会复制整份正文并产生同步漂移；
- `TerminologyLookupContext` 与 AI 的 `TerminologyRunSnapshotRef` 只有 Project、Variant、插件和术语版本上下文，没有目标译名方案；
- 写出 lease 只固定内容 Variant revision，无法证明写出期间使用的是同一配置档版本。

FR5.18 要求切换不改正文、不调用 LLM、不执行无边界中文全文替换，且 A→B→A 可逆。配置档内冲突与配置档间预期差异也必须分别表达。

产品界面统一称为“译名方案”，并使用“项目译文”“应用修改”等普通用户语言。“本体汉化适配”仅作为首个使用模板。为保持数据库、任务快照和插件兼容，内部模块、类型、表和元数据继续使用 `TerminologyProfile` / `terminology_profile_*` 技术名称。

## 决策

### 1. 配置档是独立于内容 Variant 的项目术语维度

新增独立的 `application.terminology_profiles` 业务边界。Project 仍拥有内容来源和 Variant；现有 `application.terminology` 仍拥有共同术语证据、决定和不可变版本；新边界只拥有：

- 配置档目录、显示名、停用状态和当前有效版本；
- 以稳定逻辑术语键为索引的配置档译名；
- 内容 Variant 当前选择的配置档；
- 已确认的术语出现位置和配置档条目特例；
- 固定共同正文与配置档版本的派生快照、诊断和差异摘要。

配置档不得伪装成 Project Variant，也不得复用 ADR-027 `TermEntry.variants`。同一配置档可以被同一 Project 的多个内容 Variant 选择，但每次选择和派生仍显式绑定内容 Variant identity/revision。

### 2. 逻辑术语键跨配置档稳定，现有 TermDecision 保持共同术语权威

逻辑术语键由规范化原文和作用域组成，不包含配置档身份或目标译名。现有已发布 `TermDecision` 继续提供共同术语集合、作用域、采用/抑制状态和来源追溯；配置档版本保存逻辑术语键到目标译名的完整映射。

配置档之间相同逻辑键的不同目标译名是正常差异。某个配置档缺少已采用逻辑术语时，该术语在该配置档中为“未适配”，不得回退到另一个配置档。为兼容旧项目，系统未选择配置档时继续使用现有共同术语版本和普通正文。

### 3. 派生投影只读且可复现，不改变共同正文

新增纯应用服务 `TerminologyProfileProjectionService`：输入固定的 Project/Variant identity、Variant revision、共同术语快照、配置档版本、条目普通译文、可选确认绑定和特例，输出 `ProfileProjectionSnapshot` 与逐条 `ProjectedTranslation`。

投影优先级固定为：

```text
配置档条目特例
  → 已确认且仍有效的术语出现位置
  → 可唯一证明的安全自动识别位置
  → 保留共同正文并产生未适配/歧义诊断
```

确认绑定至少保存完整 `EntryKey`、共同正文 entry revision、逻辑术语键、字符范围和绑定时文本。正文 revision、范围文本、术语或配置档版本不再匹配时绑定为 stale，不执行替换。多个替换必须先验证范围有效且互不重叠，再从右向左应用；任一条目的候选存在重叠、多义或位置不唯一时只保留该条共同正文，不产生部分替换。

安全自动识别只把“来源原文明确命中逻辑术语，且共同译文中某个已知配置档译名唯一出现并能无重叠归属”视为可证明。它是只读候选，不自动写成确认绑定。普通中文搜索替换、语义相似度和 LLM 判断不属于投影正确性边界。

### 4. 配置档版本不可变，选择是可变但原子的运行上下文

每个配置档有一个可选草稿、任意数量不可变版本和一个 current effective pointer。首版管理操作可以通过受控发布命令一次提交完整映射；后续逐项草稿编辑不能改变不可变语义。

当前选择按 `(project_id, variant_id)` 保存，只引用配置档 identity；实际任务或写出必须再固定当时的 profile version/content digest。切换先完整读取和验证目标配置档及首屏投影，再原子更新选择和 UI snapshot；失败继续使用旧选择和旧投影。

### 5. 复用每 Project SQLite，但通过独立 repository adapter 隔离责任

配置档目录、不可变版本、映射、特例、确认绑定和当前选择存入 ADR-034 已建立的每 Project SQLite 资产。新增表使用外键、唯一约束和内容摘要；schema migration 继续走现有备份优先、integrity check、future schema 只读和事务回滚流程。

`SqliteTerminologyProfileRepository` 是独立 adapter，通过现有项目 repository 暴露窄属性或 provider；不得把配置档 SQL、投影算法或 UI 状态继续加入已经较大的 `SqliteTerminologyRepository` 主类。配置档版本与现有术语版本各自不可变，不通过跨数据库双写维护一致性。

### 6. AI 消费使用组合后的有效术语快照

在 `EffectiveTerminologySnapshotPort` 与 `ProjectTerminologyAdapter` 之间增加 `ProfiledEffectiveTerminologySnapshotPort`。它读取共同术语快照和当前选择的配置档固定版本，按逻辑术语键投影 `TermDecision.translation`；缺失配置档映射的决定被显式遮蔽，不允许 legacy fallback 恢复另一套译名。

组合快照的版本 identity 与 content digest 同时覆盖共同术语版本和配置档版本。新 AI 运行首次 freeze 时解析当前选择，恢复时必须按组合 identity 读取完全相同的两份版本。运行开始后的配置档切换或发布只影响新任务。旧 checkpoint 的普通共同术语 version ID 继续按未选择配置档的旧语义恢复。

### 7. UI 通过独立控制器显示派生译文

新增 `TerminologyProfileBar` 和窄 `TerminologyProfileUiController`，放在工作台现有项目/内容上下文区域。控制器从应用 use case 读取当前 Project/Variant 的目录和固定投影，发出完整选择状态；工作台与 AI 翻译窗口绑定同一个控制器，任一入口切换都会同步另一入口。AI 在配置页签上方固定展示“译名方案”，与“术语来源”页签明确分离；运行摘要显示当前方案，任务开始后仍由既有快照边界固定版本。不把配置档状态、缓存或 SQL 加入 800 行以上的 `AppContext`。

`TranslationTable` 接收一个只读显示译文 resolver，排序/选择所持有的仍是原 `TranslationEntry`。存在配置档派生且显示值不同于共同正文时，表格内联编辑不得把派生字符串写回共同正文；用户通过共同正文编辑器修改正文，或在配置档管理界面修改术语/特例。未选择配置档时保持现有编辑行为。

配置档管理首版提供创建、复制、重命名、停用、映射编辑、选择和版本发布。配置档差异和历史识别只消费应用层快照/报告，不在 Qt View 中重新实现匹配或统计。

### 8. 写出使用投影后的临时条目，不修改 collection

格式 adapter 继续只接收普通 translation entry，不感知配置档。写出 use case 在创建 `WriteRequest` 前固定内容 Variant revision、共同术语版本、配置档版本、绑定/特例 digest，并用 `ProfileProjectionSnapshot` 生成仅属于本次写出的临时条目 tuple。

写出结束前同时验证原有 export lease 与配置档快照仍可恢复；输入变化时整体失败或继续使用已完整捕获的旧快照，不能重新跟随 current。临时条目不得写回 `TranslationEntryCollection`、Variant JSON 或 UI projection。

### 9. 兼容、迁移与逐步启用

升级数据库只新增空配置档表，不为旧项目猜测译名方案身份。未创建或未选择配置档时：

- 工作台显示原 `entry.translation`；
- AI 使用现有 Project/Variant effective terminology；
- 写出使用原 collection；
- ParaTranz 同步保持 FR5.17 行为。

用户首次创建配置档时可以从当前共同术语版本生成完整初始映射，并把当前正文声明为该配置档的识别基线。安全自动识别产生候选和诊断，不能修改正文。配置档同步到 ParaTranz 必须使用显式独立远端映射；本 ADR 首版不改变现有 FR5.17 同步线，未建立配置档远端映射时禁止把 profile overlay 写入当前共同术语远端。

### 10. 术语来源只作为创建方案时的一次性输入

AI 的动态词库、ParaTranz、本地 JSON、CSV 和 Excel 是运行时术语来源，不等同于译名方案。用户从其中一个来源创建方案时，应用层先固定该来源的单次读取结果，再与当前已发布项目术语快照合成完整映射：

- 只为当前有效 `TermDecision` 建立方案映射，`base_translation` 固定为当前项目译名；
- 唯一命中的来源译名成为方案目标译名，来源缺失、异译冲突或无法确定作用域时保留当前项目译名；
- 来源中不存在于项目术语快照的条目只进入预览统计，不进入方案，不改变共同术语权威；
- 生成结果是独立快照，之后来源文件、动态词库或 ParaTranz 的变化不会修改既有方案；
- 创建操作产生带完整内容的草稿并立即发布，是否选择为当前方案是单独、显式的用户决定。

项目术语工作台是方案创建、选择和管理的主入口，并提供独立来源选择器；AI 翻译任务只消费当前方案并提供前往工作台的快捷入口。单源加载仍复用 AI 术语基础设施边界，预览和合成规则属于 Qt 无关的应用服务，工作台不复制远端客户端或格式读取职责。ParaTranz 和大文件读取在工作线程执行；读取、预览取消或校验失败发生在持久化之前。

## 关键契约与错误语义

- 配置档不存在、停用、无 effective version或版本损坏：选择失败，保留上一完整选择和视图。
- 共同术语版本不存在：允许管理空配置档，但不能声称完成术语适配；AI/写出保持兼容旧路径。
- 映射缺失：该逻辑术语被标为未适配并遮蔽不兼容 fallback，正文投影保留原字符串。
- 绑定 stale、重叠、越界或文本不符：整条 entry 不应用自动替换，返回可定位诊断。
- 配置档条目特例存在：完整替代该条派生译文，不再在特例字符串上执行术语二次替换。
- AI 组合版本无法恢复：任务安全失败，不跟随当前配置档或共同术语最新版。
- UI 切换期间出现新请求：旧请求结果按 generation 丢弃，只有最后一次完整快照可见。
- 写出输入变化：不发布混合成品；已捕获的完整不可变快照可继续，否则整体失效并要求重试。

## 备选方案

### 每套目标译名复制一个 Project Variant

能复用现有版本隔离，但复制全部正文、stage、标签和来源状态，共同修改需要 N 次同步，并与“正文只维护一份”冲突，拒绝作为权威模型。

### 切换时直接在中文译文中全文替换

实现简单，但无法可靠处理长短词、多义、人工改写、重复切换和回滚，且会把术语选择伪装成正文编辑，拒绝。

### 把多套译名放入 TermEntry.variants

ADR-027 的 `variants` 表达来源术语的匹配变体，当前 matcher 会把它们映射到同一个目标译名；复用会改变既有格式兼容并无法表达配置档选择，拒绝。

### 在每个格式 adapter 内应用配置档

会产生多套替换和诊断规则，也让 GUI、AI、插件/XML/JSON 写出结果不一致，拒绝。adapter 只消费已经投影好的普通条目。

### 在共同正文中永久保存可见占位符

能稳定切换，但占位符容易泄漏到编辑、搜索、ParaTranz 和写出格式，破坏当前文件保真与用户体验，拒绝。结构化绑定独立保存。

## 影响与风险

- **正面**：正文真正单份维护；配置档切换可逆；AI、校对和导出可冻结同一组合；配置档间差异不污染术语冲突。
- **成本**：新增 profile repository、组合快照、派生投影、UI 控制器、写出装饰和 schema migration。
- **历史识别漏配**：宁可保留原文并要求复核，不以覆盖率换取错误替换。
- **位置绑定易陈旧**：绑定携带 entry revision 和原片段，正文编辑后 fail closed；后续可通过编辑器命令精确更新或重新确认。
- **组合版本复杂度**：共同术语与配置档各自不可变，运行时只持有组合引用，不复制或修改原版本。
- **大项目切换性能**：首屏按需投影、全量统计后台化；缓存键覆盖全部语义输入，缓存可丢弃且结果与冷计算一致。
- **UI 误编辑风险**：派生值不同于共同正文时禁用直接写回；配置档术语和条目特例通过明确命令编辑。

## 迁移与回退

1. SQLite schema 新增配置档表并在迁移前创建带摘要的数据库备份；旧表和 payload 不改写。
2. 未选择配置档的兼容路径先保持默认，新增应用/持久化合同和纯投影测试。
3. 接入组合 effective snapshot；旧 AI checkpoint 使用普通 version ID，新 checkpoint 使用可解析、摘要绑定的组合 identity。
4. 接入工作台 selector 和只读显示 resolver；配置档关闭或不可用时恢复共同正文显示。
5. 写出入口在已有 export lease 外增加组合快照固定；任何失败继续使用原未配置档写出路径或阻止显式配置档写出。
6. 回退代码版本时，新表保留且不被旧版本访问；Project/Variant JSON 和共同正文没有被迁移或改写，因此旧版本仍可按原语义打开项目。
