# Project/Variant 权威变更与保存闭环实施计划

- **Feature slug**：`authoritative-project-mutation-closure`
- **状态**：实现完成（2026-08-30，综合 QA 通过；1 项既有工具数量断言待独立纠偏）
- **日期**：2026-08-30
- **对应需求**：[FR19.9～FR19.13](../../docs/requirements.md)
- **架构约束**：[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-035](../../docs/adr/035-authoritative-project-mutations-and-recoverable-save.md)
- **承接计划**：[project-session-persistence-v2](../project-session-persistence-v2/plan.md)、[paratranz-sync-service-v2](../paratranz-sync-service-v2/plan.md)、[paratranz-sync-operation-ux](../paratranz-sync-operation-ux/plan.md)

## 目标

修复“界面修改成功、手动点保存也显示成功，但重启恢复旧内容”的系统性根因。所有正式修改先提交到活动 Project/Variant 权威聚合，再由生命周期保存；工作台、AI、词典、ParaTranz 和 Smart Assistant 不再各自维护无法持久化的 collection 状态。

完成后应满足：

1. 删除旧来源、添加新插件、导入新译文、保存并重启后，来源清单、全部工作台 slot 与 Variant 条目保持一致。
2. 已有 EntryKey 的词典、AI、ParaTranz 和 Smart Assistant 修改都经统一 working-copy command 增长 Variant revision，顶栏正确显示 dirty。
3. Project/Variant 保存同时执行 persisted revision CAS；故障或崩溃恢复后不会留下半提交，也不会把较新 revision 降级。
4. 保存期间再次编辑、autosave 请求被合并或保存失败时会自动补发；只有权威状态真实持久化后才显示 clean。
5. 迟到后台结果、错误项目/Variant、无法表示的远端 create/delete 和无 V2 facade 的 batch fail-closed，不再静默回退到旧 collection 写入。

## 非目标

- 不改变 Project/Variant JSON schema 的业务所有权，也不把 UI collection 重新定义为持久化权威。
- 不在本轮实现 ParaTranz 对未知远端条目的本地 source 创建/删除语义；缺少可靠 original/context/baseline 时明确拒绝。
- 不重写 AI、Smart Assistant 或 ParaTranz 的业务算法，只替换它们的最终提交边界。
- 不通过保存时扫描 UI 反向构造 Variant，也不恢复被 ADR-018 淘汰的 legacy project persistence。

## 当前实现事实与关键约束

- `GuiProjectCommandFacade.replace_entry_states()` 已能按完整 `EntryKey` 更新已有条目的 translation/stage，但直接在 lifecycle 锁外修改 `VariantAggregate`，无法为长任务提供 active identity/revision CAS。
- `ProjectLifecycleService.save_active()` 只在 aggregate dirty 时保存；因此任何直接修改 `TranslationEntryCollection` 的调用都会成功 no-op。
- `ProjectLifecycleTransactionStore` 对 `LifecycleSave` 无条件先写 Project 后写 Variant，未使用模型中已经捕获的 expected persisted revisions，`VariantRepository` 也没有 conditional save。
- `CurrentProjectOpener` 已准备全部 registered sources/baselines，`ProjectCoordinator._restore_plugin_sources()` 却只选一个来源，并按 local key 叠加 projection。
- `AppContext` 与 `application/projects/provisioning.py` 已超过代码责任阈值；新命令与一致性检查放入独立模块，原类只装配/委托。
- 当前工作树含用户尚未提交的 ParaTranz、provisioning、coordinator 和 workbench 改动；实施必须基于当前内容做局部 patch，不覆盖、重排或回退无关差异。

## Story-01：活动 Project/Variant 原子 working-copy 命令

**目标**：建立唯一 application mutation seam，使所有后续 adapter 都有可验证的正式提交入口。

**验收标准**：

- [x] 新增 typed `ActiveProjectChange`/entry patch 合同，包含 Project/Variant identity 与 expected revisions。
- [x] `ProjectLifecycleService` 在同一锁内校验 context、身份和 revision，完整构造并验证候选对象后一次切换 active；失败时 Project、Variant、baseline 和 projection 均不变。
- [x] translation/stage patch 保留 labels/provenance/tombstone；无实际变化不伪造 revision，实际变化增长 Variant revision。
- [x] 完整 content replacement 校验 EntryKey 唯一、namespace 与 source fingerprints 一致。
- [x] `GuiProjectCommandFacade` 保持现有公开方法，但改为委托统一服务并在成功后只重建一次 projection。

**文件落点**：

- 新增 `src/transbridge/application/projects/mutations.py`：typed change、校验和候选构造，避免继续扩充 `gui_facade.py`。
- 修改 `src/transbridge/application/projects/lifecycle.py`、`ports.py`、`models.py` 与 `__init__.py`：生命周期锁内提交入口。
- 修改 `src/transbridge/application/projects/gui_facade.py`：compatibility facade 委托与 expected revision 捕获。
- 扩展 `tests/application/projects/test_lifecycle.py`、`test_gui_facade.py`，必要时新增 `test_mutations.py`。

**实施与测试策略**：先以纯 `VariantEntryState` 建候选；用活动身份切换、stale revision、重复 key/namespace、回调异常、无变化和成功 dirty 的测试固定原子性。该 Story 完成前不迁移 UI adapter。

## Story-02：双 revision CAS、恢复 journal 与保存真实性

**目标**：让生命周期保存既拒绝陈旧写入，又能从跨 Project/Variant 发布故障中恢复。

**验收标准**：

- [x] Project/Variant repository 都支持同语义 `save_if_revision`，共享 transaction-level mutation lock。
- [x] `LifecycleSave` 在任何写入前校验两份 expected persisted revision；任一 stale 时零写入并返回结构化 conflict。
- [x] dirty 文档通过 verified preimage、事务 manifest、目标 digest/revision 和逐份复读发布；第二写失败可安全补偿。
- [x] 未完成 manifest 在 composition 启动/首次 transaction 前恢复为“目标均完成”或“旧状态一致”；遇到较新外部 revision 时 fail-closed 且不降级。
- [x] 保存 I/O 期间出现新 working-copy revision 时，本次捕获状态可落盘，但 active 继续 dirty；不会先标 clean 再返回冲突。
- [x] aggregate clean 但 projection 摘要不同，手动/自动保存返回 `PROJECTION_AUTHORITY_DIVERGED`。

**文件落点**：

- 修改 `src/transbridge/persistence/v2/repository.py`、`lifecycle_transactions.py` 和 `atomic_documents.py`；恢复逻辑若超过模块责任阈值则新增 `src/transbridge/persistence/v2/project_save_journal.py`。
- 修改 `src/transbridge/application/projects/lifecycle.py` 与 composition 注入的 projection consistency port。
- 扩展 `tests/persistence/v2/test_lifecycle_transactions.py`、`test_repository.py` 和 `tests/application/projects/test_lifecycle.py`；使用 fault-injecting filesystem 模拟每个 staging/replace/read 故障和未完成 journal。

**实施与测试策略**：先加入 repository CAS，再实现同进程补偿，最后补 durable recovery；每一步都验证旧文档字节或两份目标 revision，禁止只断言异常类型。

## Story-03：来源增删、导入与全部来源 hydration

**目标**：让用户截图中的实际流程——删旧翻译、加插件、导入新翻译、保存、重启——完整经过权威来源生命周期。

**验收标准**：

- [x] source preparation 结果可以作为 add/replace command，同步更新 Project source registry、active Variant fingerprints/entries 和 baseline registry。
- [x] remove command 按稳定 source identity/namespace 删除 descriptor、incident relation、baseline 及 Variant 对应条目；其他来源不受影响。
- [x] 导入/迁移按完整 EntryKey 更新明确来源，不跨 namespace 使用 local key 覆盖。
- [x] open/activate 后恢复全部启用且可读取来源的独立 slot；每个 slot 用完整 EntryKey 应用 Variant projection，不再只选择 primary。
- [x] add/remove/import 后手动保存并 reopen 的端到端测试与用户场景一致。

**文件落点**：

- 新增或扩展 `src/transbridge/application/projects/source_mutations.py`，复用现有 `ProjectSourcePreparationPort`/registry helpers；不继续扩充超过阈值的 provisioning 模块。
- 修改 `src/transbridge/persistence/v2/baselines.py`，提供 snapshot/replace/restore 的批量边界。
- 修改 `src/transbridge/persistence/current_project.py`、`ui/coordinators/project_coordinator.py`、`ui/workbench/widget.py` 及导入/迁移 coordinator，使 hydration 携带全部来源。
- 扩展 `tests/application/projects/`、`tests/persistence/`、`tests/ui/test_workbench_slices.py` 与 current-project reopen 测试。

**边界条件**：同 namespace 重复来源、某来源不可读、metadata-only source、folded translation、其他 Variant 的延迟 materialize、同 local key 跨 namespace并存。

## Story-04：编辑、词典、AI 与 Smart Assistant adapter 迁移

**目标**：把只修改已有条目的工具统一委托 Story-01 command，并明确 working-copy 与保存动作。

**验收标准**：

- [x] 词典应用、AI 单条/批量/后台/润色/混合和 Smart Assistant 的编辑/标记/解析/词典/翻译/润色/校对，不再以 collection 原地修改作为最终成功边界。
- [x] 普通工具完成后 Variant dirty；明确“保存翻译”动作在 commit 后调用 lifecycle save/snapshot，并只在持久化成功后报告成功。
- [x] 取消与迟到结果不提交；显式 partial policy 只提交已验证 EntryKey 子集并返回 partial outcome。
- [x] facade 不可用、active 身份变化或 revision stale 时失败可见，projection 不展示不可保存结果。

**文件落点**：

- 修改 `src/transbridge/ui/version_persistence.py`，拆分 working-copy commit 与 commit+save/snapshot。
- 修改 dictionary coordinator、`src/transbridge/ui/tools/ai_translator/` 的结果提交 adapter、`src/transbridge/ui/tools/smart_assistant/` 的 session/tool binding。
- 扩展 `tests/ui/tools/`、Smart Assistant 工具测试和后台 GUI 操作测试；加入 commit/save/reopen 断言。

**实施与测试策略**：对每类 adapter 用 fake command facade 断言完整 EntryKey、捕获 revision、调用次数和错误传播；至少各保留一条真实 lifecycle reopen 集成测试。

## Story-05：ParaTranz planned/legacy 路径与 autosave 补发

**目标**：阻止同步和自动保存绕过同一权威边界。

**验收标准**：

- [x] planned download 的 local UOW 通过 expected Variant revision command 提交，不再执行 `context.collection = ...` 作为事务。
- [x] active identity 或本地 hash/revision 变化时执行返回 `LOCAL_TRANSACTION_FAILED`/stale；新 Project/Variant 不被迟到结果污染。
- [x] V2 create/delete local、缺少 command facade 的 batch 或任何 legacy fallback 在 preflight fail-closed；支持的 existing-entry update 正常工作。
- [x] autosave 每次异步保存都有 completion callback；coalesced、失败、冲突和完成后仍 dirty 都重启 debounce。
- [x] 主窗口不因“保存调用返回 success”无条件清除 dirty，手动与自动保存使用同一最终状态。

**文件落点**：

- 修改 `src/transbridge/ui/operations/production_support.py`、`paratranz_sync.py`、现有 download card 与 application sync callback UOW seam。
- 修改 autosave manager、主窗口 save completion handler 及相关 composition。
- 扩展 `tests/contracts/paratranz/test_sync_execution.py`、`tests/ui/operations/test_production_facade.py`、`tests/ui/test_background_gui_operations.py` 和 autosave/main-window tests。

**边界条件**：remote plan 中新增/删除、batch、facade 缺失、commit callback 抛错、保存中连续两次编辑、窗口切换、取消后迟到完成。

## Story-06：统一回归门禁与兼容清理

**目标**：证明所有已识别写路径都已闭合，并防止以后重新引入 projection-only mutation。

**验收标准**：

- [x] 新增静态合同测试，扫描生产 adapter 中对 authoritative `context.collection` 赋值和 `TranslationEntry` 原地写入；仅允许列明的 projection builder/legacy non-project 边界。
- [x] 用户场景端到端测试覆盖“删旧来源→加新插件→导入→保存→关闭→重开”。
- [x] 工作台、词典、AI、Smart Assistant、ParaTranz、autosave 和 fault recovery 聚焦测试全部通过。
- [x] `uv run ruff check src tests` 与 `uv run ruff format --check src tests` 通过；若全量 pytest 因既有无关问题未跑或失败，交付明确列出。
- [x] 删除本任务产生的临时文件；不改动用户 translation data、缓存、依赖锁或无关工作树内容。

## 实施与 QA 结果

- 用户场景、Project/Variant authority、journal fault recovery、来源 hydration、AI/助手取消与 ParaTranz CAS 的广覆盖回归为 `992 passed, 1 failed`；唯一失败是本任务前已存在的工具总数断言仍期望 50，当前注册表实际为 64。
- `uv run ruff check src tests` 与 `uv run ruff format --check src tests` 全部通过（1068 files）。
- 多 Variant 工程删除/同身份换源在缺少跨 Variant 持久化事务时明确返回 `PROJECT_SOURCE_MULTI_VARIANT_MIGRATION_REQUIRED`，不会留下不可打开的版本。
- 多个独立 OS 进程同时写同一 persistence root 尚无跨进程文件锁；桌面应用单进程内的 revision+digest CAS、durable journal 与崩溃恢复已闭环。

**文件落点**：新增 `tests/contracts/projects/test_authoritative_mutation_paths.py` 与用户场景集成测试；只在已有兼容 helper 已无调用者时删除它们。

## 依赖顺序与并行安排

```text
S01 authoritative working-copy command
  ├─→ S03 source lifecycle + hydration
  ├─→ S04 dictionary/AI/assistant adapters
  └─→ S05 ParaTranz authoritative commit
S02 persistence CAS/recovery ───────────────┐
S03 + S04 + S05 + S02 ────────────────────→ S06 QA gate
```

S01 的公共合同由主会话先固定。之后 S02、S04 和 S05 可在互不重叠的模块上并行；S03 与当前用户工作树重叠最多，由主会话串联处理。任何 agent 不修改同一个生产文件，集成与最终测试由主会话完成。

## 迁移、兼容与回退

- `GuiProjectCommandFacade` 保持公开 API，旧调用方逐步改为传 expected revision；迁移期省略值时 facade 在调用瞬间捕获 active revision，但长任务必须显式传入。
- 旧 Project source descriptor 通过现有 legacy registry 字段推导 namespace；下一次成功 source mutation 写回规范化 identity。无法无歧义推导时拒绝 remove，不猜测。
- 新 journal 只包含 Project/Variant JSON 的 preimage/target 元数据，不改变业务 schema。回退旧代码时 committed 正式文件仍可读取；未完成 journal 由新版恢复工具处理，不由旧代码删除。
- 若某 adapter 迁移未完成，临时策略是 fail-closed 并保留现有数据，不保留 projection-only fallback。

## 风险与假设

- 假设当前 uncommitted ParaTranz/UI 改动属于用户并需完整保留；所有 patch 以当前内容为准。
- 恢复 journal 依赖 persistence root 为同一受控本地文件系统；网络文件系统只使用已验证的 filesystem capabilities，否则保存前拒绝宣称 crash-safe。
- 多来源 UI hydration 可能暴露过去被单来源选择隐藏的同 local key；所有 overlay 必须升级为完整 EntryKey 后再移除旧 helper。
- 本计划规模较大但不需要新的依赖或 schema major migration；若实现发现 source identity 无法从现有 v3 descriptor可靠恢复，只追加兼容字段和显式 migration，不重写用户项目。

## 未决问题

- ParaTranz create/delete local 需要怎样的 original/context/source baseline 属于后续产品能力；本计划按 fail-closed 处理。
- 部分 AI 结果是否默认提交由各现有动作合同决定；没有明确 partial policy 时一律不提交。
