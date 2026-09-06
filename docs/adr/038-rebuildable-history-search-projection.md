# ADR-038：可重建的历史翻译与术语统一搜索投影

- **状态**：提议
- **日期**：2026-09-06
- **对应需求**：[FR29](../requirements.md)
- **关联 ADR**：[ADR-014](014-fomod-translation-memory.md)、[ADR-016](016-modular-monolith-application-composition.md)、[ADR-017](017-translation-io-kernel-v2.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-034](034-project-terminology-build-versioning-reporting.md)、[ADR-035](035-authoritative-project-mutations-and-recoverable-save.md)

## 背景与约束

FR29 需要通过统一搜索窗口检索三种已有资产：Project/Variant 的完整译文、`.tbdict` 翻译记忆和当前生效项目术语；用户可以同时打开多个窗口，并在每个窗口独立选择全部来源、单个 Project 或单个词典。这三类数据具有不同所有者和存储方式：

- Project/Variant 由 ADR-018 的 JSON repository 拥有；Variant 保存完整 `EntryKey` 与译文状态，但不复制原文，原文必须从 Project 登记来源通过格式适配器恢复；
- `.tbdict` 由翻译记忆模块拥有，按 mod 文件分库；
- 项目术语由 ADR-034 的每 Project SQLite repository 拥有，并按 Project/Variant 保存不可变发布版本。

直接把任何一类资产复制成新的可编辑“总库”会制造第四个权威状态，并产生删除、冲突、版本和写回语义。另一方面，每次键入都重新解析所有 Project 来源或遍历全部术语版本无法满足搜索响应预算。搜索必须只读、可追溯、失败隔离，并且不扩张 `AppContext` 或让 PyQt 直接读取存储文件。

## 决策

### 1. 建立独立的只读搜索应用域

新增 `application.history_search`，只拥有规范搜索记录、来源投影、查询、聚合和诊断合同。依赖方向为：

```text
HistorySearchWindow
        ↓
HistorySearchService / HistorySearchTaskEntrypoint
        ↓
HistorySearchIndexPort + HistoryRecordProvider ports
        ↑
Project/Variant provider  TM provider  Terminology provider
        ↓                    ↓                ↓
V2 repositories       .tbdict model     terminology repository
FormatAdapter
```

搜索域不得修改 Project、Variant、翻译记忆、术语版本、当前集合或命中计数。UI 只消费不可变搜索投影，不读取路径、JSON 或 SQL。

### 2. 使用可重建 SQLite 投影，而不是第四个权威存储

搜索索引使用单独 SQLite 文件作为派生读模型。它只保存搜索所需的规范文本、记录类型、语言方向、作用域、状态和来源投影；不保存可被业务写回使用的权威对象。索引可随时删除并由三类权威来源完整重建，不参与 Project/Variant 保存、术语发布或 `.tbdict` 更新事务。

刷新在同目录 staging 数据库中完整构建、校验后原子替换当前索引。失败或取消时保留上一份完整索引并清理本次 staging；不得留下半刷新结果。查询与替换由索引 adapter 的进程内锁协调，避免 Windows 上替换打开数据库文件失败；同一窗口只允许一个刷新任务。

索引 schema 与规范化版本显式记录。记录同时保存用于只读筛选的 Project ID 和 dictionary ID，并维护去重后的可选来源投影；选择 Project 时同时命中其 Variant 翻译与生效术语。版本不兼容时直接重建，不迁移或反向修改权威资产。索引文件损坏时报告诊断并允许刷新重建；损坏索引不得导致任何来源文件被隔离、改名或覆盖。

### 3. Project/Variant 记录由已登记来源恢复原文

Project provider 从只读 Project catalog 枚举可用 Project，以 `ProjectRepository.read_snapshot()` 和 `VariantRepository.read_snapshot()` 读取已保存状态。它只解析 Project 中登记且启用的来源，并通过 ADR-017 `FormatAdapter.parse(ParseRequest)` 得到原文与 `EntryKey`；解析沿用该来源保存的 `format_options`，不得临时扩大到未登记目录或游戏安装目录。

同一 Project 的来源只解析一次，再按完整 `EntryKey` 与各 Variant 保存状态连接。只有非 tombstone、非空译文且能够证明原文对应关系的记录进入索引。来源缺失、变化、格式能力不足、解析失败或 EntryKey 无法对齐时产生来源级诊断并跳过相关记录；不得用 local key、文件名或扫描顺序猜测原文。

索引来源投影保存 Project/Variant 的显示名和稳定 ID，以及可证明的来源/插件标识。绝对来源路径只用于受控读取和诊断，不进入普通结果摘要。

### 4. 翻译记忆与术语使用只读投影

`.tbdict` provider 按文件独立读取并用现有 `Dictionary` 模型校验。一个文件损坏只产生一个诊断；搜索刷新不得调用会改名隔离损坏文件的维护型加载路径。原文和译文非空的词条进入只读索引；旧数据中 disabled 的记录仍可被人工搜索，但明确显示 disabled，且不改变其自动套用资格。来源使用词典 `mod_file_id`、scope、locale 和条目来源字段。

术语 provider 对每个可用 Project 的每个已保存 Variant 查询当前 effective version，只纳入 `TermDecision.is_effective` 的决定。草稿、历史版本、suppressed、review-required 和 unresolved 决定不进入首版普通结果。项目作用域和插件作用域使用不同 scope key。

### 5. 规范化、聚合与不同译法合同

搜索规范化统一执行 Unicode NFKC、换行统一、首尾空白移除和 `casefold()`；它只用于匹配与稳定分组，不改写展示原文。包含查询对 `%`、`_` 和 escape 字符做转义。

展示聚合键为：

```text
record_kind + normalized_original + normalized_translation
+ source_locale + target_locale + effective_scope
```

聚合只合并来源投影，不合并底层资产。不同 record kind 永不合并。相同原文在相同 kind、语言方向和作用域内出现多个译文时，各译文保持独立结果并标记不同译法；术语的 project/plugin scope 不互相构成冲突。

### 6. 后台刷新、同步查询与 UI 生命周期

完整刷新属于 ADR-019 长任务，通过进程级 `TaskRuntime` 提交，拥有独立 owner、取消 token、进度和互斥终态。刷新 workload 只在成功末尾替换派生索引；窗口在刷新完成前把刷新动作切换为取消动作，不并发提交第二次刷新。

索引就绪后的有界查询是同步 application use case；UI 使用短防抖并在 Qt 线程池执行查询，通过 generation 丢弃过期结果。窗口关闭时注销任务订阅并使 UI generation 失效；后台刷新可以由 TaskRuntime 正常结束或取消，但不得回调已销毁控件。

窗口通过现有 Action Catalog、Intent composition 和 ToolWindows 打开。入口不要求当前 Project，因为目标是跨全部本地资产搜索；每次触发入口都创建一个无主窗口 owner 的独立顶级窗口，由 ToolWindows 跟踪全部活动实例并在主窗口退出时统一释放。Windows 下每个实例在首次显示前复用公共 taskbar helper 设置唯一的窗口级 `AppUserModelID` 和 `WS_EX_APPWINDOW`，避免与主窗口或其他搜索实例合并为同一任务栏缩略图组；关闭时清理显式身份，非 Windows 平台保持普通独立顶级窗口语义。每个窗口独立持有关键词、类型和来源范围，默认空关键词、全部类型、全部来源，并展示有界首屏；实例标题使用进程内序号便于任务栏辨认。首版动作只有刷新、范围/类型筛选、选择、展开来源和复制译文。

## 关键契约

- `SourceRecord`：一条来源特定的只读记录，包含 kind、双语文本、locale、有效 scope、状态和一个 `HistorySourceRef`。
- `HistorySearchHit`：按聚合键形成的展示结果，包含全部来源与 `has_alternatives`。
- `HistorySearchIndexPort.replace/query/scopes/status`：原子替换派生快照、执行空关键词浏览或有界包含查询、列举 Project/词典范围并报告是否已就绪。
- `HistorySearchProvider.collect(cancellation)`：从一个权威来源域产生记录与诊断；provider 失败被刷新服务隔离。
- `HistorySearchTaskEntrypoint.refresh(owner)`：把完整刷新提交到 TaskRuntime；UI 不自行创建业务线程。

错误分为：索引不可用、Project/Variant 不可读、来源不可读/变化/不支持、词典损坏、术语仓储不可读、刷新取消和内部错误。部分来源失败的刷新可提交为带诊断的完整派生快照；只有索引构建或原子替换失败才保留旧快照并使任务失败。

## 备选方案

### 每次键入直接扇出查询三套存储

`.tbdict` 和术语可直接遍历，但 Project 原文需要重新解析登记来源；延迟、线程和失败语义会随每次按键重复，拒绝。

### 窗口打开时建立纯内存快照

实现简单且无派生文件，但大量 Project/Variant 与术语会长期占用 Python 对象内存，每次进程重启都必须重新解析全部来源。保留为测试 adapter，不作为生产方案。

### 把全部记录导入现有翻译记忆或术语库

会混淆完整句段与术语、丢失 Variant/作用域语义，并使只读搜索产生业务写入，拒绝。

### 首版只搜索 `.tbdict`

无法找到用户已经保存但从未“存为词典”的译文，违背 FR29 的核心目标，拒绝。

## 影响与风险

- 正面：搜索响应与来源解析解耦；三类资产继续保持单一权威；重复译对可聚合且来源不丢失。
- 成本：新增一个可重建 SQLite adapter、刷新 workload 和跨 Project 读取 provider。
- 风险：全量首次刷新可能较慢。通过 TaskRuntime、进度、取消和保留旧快照降低影响；后续可在不改变查询合同的前提下增加按 revision/fingerprint 的增量刷新。
- 风险：源文件变化导致 Variant 原文无法证明。严格跳过并诊断，不能为了覆盖率采用 local key 猜测。
- 风险：SQLite 前导通配包含查询无法完全利用普通 B-tree。首版以有界结果和可复现基准验证；若规模超过预算，可在同一端口后引入已验证的 FTS/ngram adapter，不改变权威存储。

## 迁移与回退

1. 新增搜索应用合同和 SQLite 生产 adapter；不修改任何权威 schema。
2. 接入三类 provider 和后台刷新，首次打开时在无索引或显式刷新时构建。
3. 接入独立窗口与菜单 intent；旧翻译词典和术语工作台入口保持不变。
4. 若生产索引出现问题，禁用 FR29 入口或删除派生索引即可回退；Project、Variant、`.tbdict` 和术语数据无需回滚。
5. 增量刷新、历史术语版本、远端 ParaTranz 和“采用译文”均通过后续独立需求扩展，不在本 ADR 中预留隐式写路径。
