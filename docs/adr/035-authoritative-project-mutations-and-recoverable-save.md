# ADR-035：统一 Project/Variant 权威变更与可恢复保存

- **状态**：已接受
- **日期**：2026-08-30
- **对应需求**：[FR19.9～FR19.13](../requirements.md)
- **关联 ADR**：[ADR-016](016-modular-monolith-application-composition.md)、[ADR-017](017-translation-io-kernel-v2.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)
- **补充**：本 ADR 细化 ADR-018 已确定的 Project/Variant 权威边界、revision 与多文档提交语义；不改变文件格式或 Project/Variant 的所有权

## 背景与问题

当前正式保存已经写入 V2 `ProjectDto` 与 `VariantSnapshot`，但仍有多条用户操作沿用旧工作台模型：它们直接替换 `AppContext.collection`、修改 `TranslationEntryCollection` 或工作台 slot，而没有提交 `VariantAggregate`。顶栏“保存”随后看到权威聚合仍为 clean，便成功 no-op；界面中的修改只存在于 projection，重启后自然由旧 Variant 状态覆盖。

该缺口不是单一按钮问题。工作台来源增删、导入/迁移、词典套用、AI 的批量与后台路径、ParaTranz 下载以及 Smart Assistant 均存在相同风险。另有三个相关问题会放大数据丢失：

- Project 与 Variant 保存没有同时校验捕获的 persisted revision，第二次写入失败时可能留下跨文档半提交；
- 自动保存合并请求后没有可靠地在活动保存结束时补发，保存期间的新 revision 可能继续保持 dirty；
- 打开项目的后端已经读取全部来源，但 UI hydration 只恢复一个选中的来源，造成多来源项目看似“回到旧状态”。

`AppContext` 和部分 provisioning 模块已经超过仓库责任阈值，因此不能继续把新业务入口堆进这些类。统一修复需要新增窄 application service，并让 UI、同步和任务 adapter 依赖它。

## 决策

### 1. Project/Variant 聚合是唯一正式写入点

所有会改变来源或翻译状态的应用操作必须形成显式 command，再由一个 application 层服务提交：

```text
UI / task / sync adapter
          ↓ typed command + expected active identity/revision
AuthoritativeProjectMutationService
          ↓
ProjectDto + VariantAggregate + BaselineRegistry
          ↓ successful commit
AppContext / workbench projection rebuild
```

`TranslationEntryCollection`、工作台 slot、Qt signal 和 `AppContext.collection` 继续作为兼容 projection，但不得拥有正式修改。`collection_changed` 只通知读模型变化，不得直接把生命周期标成 dirty，也不得作为保存时抓取 UI 内容的触发器。

应用服务按数据范围提供两组命令：

1. **Variant 状态命令**：对已存在完整 `EntryKey` 的 translation、stage、label、revision/provenance 等受支持状态做一次 CAS 提交。词典、AI、Smart Assistant 和只更新已有条目的 ParaTranz 合并走此入口。
2. **来源集合命令**：添加、移除或替换一个已准备来源时，同时生成受校验的 Project source descriptor、`SourceBaseline` 与新的 Variant source fingerprints/entries；Project、Variant 与 baseline registry 只在整个命令验证成功后一起切换。

命令必须绑定 `project_id`、`variant_id`、expected Project revision 和 expected Variant revision。活动身份或 revision 已变化时返回 conflict，不把迟到结果应用到新打开的项目。兼容调用方可通过薄 adapter 映射旧模型，但不得双写聚合和 collection。

### 2. 完整 EntryKey 与 source identity 贯穿多来源生命周期

Project 的 `sources` 是来源登记权威，Variant 的 `source_fingerprints` 与完整 `EntryKey` 是内容权威。添加来源必须先经现有 source preparation port 得到稳定 namespace、fingerprint 和 baseline；重复 namespace、未验证 fingerprint、跨来源重复 EntryKey 或不兼容能力均在提交前拒绝。

移除来源同时删除 Project descriptor、对应 baseline 以及 Variant 中该 namespace 的 fingerprint/entries。导入或迁移只更新明确映射的 EntryKey，禁止用 local key 跨 namespace 覆盖。

重新打开项目时，coordinator 按 Project source 顺序恢复每个可读取来源的独立 slot，再用完整 `EntryKey` 叠加 Variant 状态。单来源选择 helper 只能用于兼容显示偏好，不能裁剪权威来源集合。某个来源无法读取时保留 Project 登记并返回诊断，不以空 slot 或其他来源替代。

### 3. working-copy commit 与持久保存分离

前台、后台、批量和同步完成时先通过同一 mutation service 提交 working copy，使 Variant revision 增长并保持 dirty；是否立即落盘由调用动作的明确策略决定：

- 普通编辑、词典、工作台增删和 AI working-copy 操作提交后保持 dirty，用户可手动保存；
- 明确名为“保存翻译”或同步事务的动作可在 working-copy commit 成功后调用 lifecycle save；
- checkpoint、临时预览或 UI collection 替换不等于正式提交；
- 取消默认不提交。若产品动作明确允许部分提交，则只提交已经通过身份/revision 校验的子集，并返回 partial outcome 与 dirty 状态。

长任务在开始时捕获 active identity 和 Variant revision；最终提交在生命周期锁内再次验证。无法映射到已有 EntryKey 的 ParaTranz create/delete 或缺少 V2 facade 的 batch 不再回退到旧内存写路径，而是在 preflight fail-closed，并给出可操作诊断。

### 4. 保存必须验证两份 persisted revision 并可恢复跨文档故障

`LifecycleSave` 捕获 Project 与 Variant 的 expected persisted revision。提交按以下协议执行：

1. 在一个 process-wide mutation lock 内读取并校验两份正式文档；任一 revision 不匹配时零写入返回 conflict；
2. 为将被覆盖的正式文档创建并复读校验事务备份，同时写入包含 transaction id、目标 revision、旧摘要和阶段的 durable journal；
3. 通过各自 repository 的 revision-CAS 发布目标文档；每次发布后复读并校验目标 identity/revision/digest；
4. 两份文档均验证后把 journal 标为 committed，再清理可丢弃 staging；
5. 进程内第二次发布失败时，只在当前文档仍等于本事务刚写入的目标 digest/revision 时恢复第一份旧文档；若已出现更高 revision，禁止降级覆盖并保留 journal 供恢复；
6. composition 启动时扫描未完成 journal：若两份目标都已发布则完成提交；若仍可安全恢复则回滚；若存在更新的外部 revision 则进入只读 conflict/quarantine 诊断，不猜测覆盖。

Project 与 Variant repository 均提供相同的 `save_if_revision` 合同和结构化 conflict。单文件内部继续使用 verified staging + atomic replace；journal 只解决两个原子替换之间的崩溃窗口，不把 UI active pointer 当作事务真相。

保存成功后，生命周期仅把本次捕获的 revision 标记 persisted。若 working copy 在 I/O 期间出现新 revision，本次保存可成功保存旧快照，但活动对象仍为 dirty，并立即安排下一次保存；不得先把活动对象错误地标成 clean 再返回 conflict。

### 5. 手动保存与自动保存共享真实性规则

保存入口在 authoritative 模式下先验证 projection 与聚合身份/条目摘要一致。如果 aggregate clean 但 projection 存在未提交差异，返回 `PROJECTION_AUTHORITY_DIVERGED`，不显示“已保存”。真正 clean 的 no-op 可以成功。

自动保存 manager 为每次异步保存安装完成回调。活动保存期间收到的 coalesced request、保存失败、保存冲突或完成后仍 dirty，均在回调中重新启动 debounce；只有 `context.dirty` 为 false 时停止。主窗口不得因为收到成功结果就无条件清除 save-dirty，而应读取 lifecycle/projection 的最终 dirty 状态。

### 6. 迁移通过 adapter 完成，不保留双权威

统一修复顺序为：

1. 新增窄 mutation service、CAS repository 与 lifecycle save 回归测试；
2. 让既有 `GuiProjectCommandFacade` 委托新服务，并保留稳定公开方法；
3. 迁移工作台来源/导入、多来源 hydration；
4. 迁移词典、AI、Smart Assistant 与 ParaTranz adapter；
5. 增加静态/合同测试，禁止生产代码直接替换 authoritative 项目的 collection 或在旧集合上原地提交；
6. 删除已无调用者的兼容 dirty signal 与 fallback。若某条兼容路径暂时只能 fail-closed，必须返回明确能力诊断，不得静默成功。

## 关键失败语义

| 场景 | 结果 |
|---|---|
| active Project/Variant 与任务捕获身份不同 | conflict；working copy 和 projection 不变 |
| expected aggregate/persisted revision 不同 | conflict；零正式写入 |
| projection 修改但 aggregate clean | `PROJECTION_AUTHORITY_DIVERGED`；保存失败且保持用户可见警告 |
| 来源准备或完整 EntryKey 映射不完整 | prerequisite/input failure；不登记半个来源 |
| 跨文档第二次发布失败 | 安全补偿或留下可恢复 journal；不得报告成功 |
| 保存期间产生新 Variant revision | 已捕获 revision 可持久化；活动对象继续 dirty并自动补发 |
| 后台/批量结果迟到、取消或目标已切换 | 不提交；partial 仅在显式策略下提交已验证子集 |
| V2 同步需要 create/delete 或缺少正式 command facade | preflight 拒绝，不回退旧 collection 修改 |

## 备选方案

### 连接 collection_changed 并直接 mark dirty

这会让保存按钮亮起，却仍没有可持久化的 Project/Variant ChangeSet；保存可能继续 no-op，拒绝。

### 保存时从 UI collection 反向抓取

UI projection 缺失 source descriptor、fingerprint、完整 provenance、标签库和可靠 namespace，且多窗口/后台任务可能产生不同 projection；反向抓取会建立第二个权威，拒绝。

### 为每个窗口分别实现保存

会继续产生前台、AI、同步和 Smart Assistant 不同语义，也无法统一并发校验，拒绝。

### 只做进程内锁和补偿

可覆盖普通异常，但不能覆盖两个文件替换之间的进程中止。它可作为 journal 落地前的受限中间步骤，但不得宣称满足 FR19.12 的崩溃恢复。

## 影响与风险

- **正面**：保存按钮只保存真实权威状态；各工具修改在重启后保持一致；多来源项目不再被单来源 UI hydration 裁剪；陈旧后台结果不能污染新项目。
- **成本**：需要迁移多组 legacy adapter，并为 source mutation 建立新的应用合同与恢复测试。
- **风险：一次改动范围较大**。缓解：先固定 application/persistence contract，再按 adapter 分组迁移；每组增加 save/reopen 回归测试。
- **风险：现有工作树包含并行功能改动**。缓解：所有修改以当前文件为基线做局部 patch，不回退或覆盖无关差异；在重叠模块优先新增窄模块和委托点。
- **风险：兼容功能短期被 fail-closed**。缓解：只有无法保证 V2 正式持久化的 create/delete/batch 才拒绝，并提供明确诊断；已有 EntryKey 的更新优先恢复。

## 验证条件

本 ADR 只有在以下证据同时通过后才可转为“已接受”：

- 删除来源、添加新插件、导入译文、手动保存、关闭并重新打开后，Project sources、全部 slots 和 Variant 条目与保存前一致；
- 词典、AI 单条/批量、Smart Assistant 与 ParaTranz 的可支持更新都能经同一 command 提交并在 reopen 后保持；
- aggregate clean + projection divergence 不再返回保存成功；autosave 在合并请求和保存中继续编辑时自动补发；
- Project/Variant 任一陈旧 revision 都零写入拒绝；任一故障注入与模拟中止后可恢复一致文档；
- 生产 adapter 不再直接把 authoritative 项目的 collection 替换当作正式提交；相关 focused tests、Ruff check 和 format check 通过。
