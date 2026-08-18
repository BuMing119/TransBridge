# ADR-018：Project/Variant/Session 状态所有权与持久化 V2

- **状态**：已接受（2026-08-18）
- **日期**：2026-08-18
- **对应需求**：FR19、FR20.3、NFR2.1、NFR4.1
- **关联 ADR**：ADR-002、ADR-006、ADR-008、ADR-011、ADR-016、ADR-017、ADR-019
- **承接根因**：R-008、R-021～R-026、R-030～R-032、R-047
- **部分取代**：ADR-006 的 AppContext 所有权、Variant overlay、Stage 推导和无模式迁移决策；ADR-008 D13～D17 的 UI 协调式 Session 切换

## 背景与约束

ADR-006 选择 JSON 和 Project/Variant 资产是可保留的，但其实现把 workspace、project、variant、filter、label 和 collection 状态继续集中到 AppContext，并仅持久化非空 translation 与 labels。当前运行时可复现空 Variant 保留旧译文、清空译文后旧缓存复活；Stage、provenance 和 revision 未持久化。

ADR-008 的 SessionManager 保存可见消息，但启动恢复和切换主要由 Panel/ChatWidget 协调，后端推理历史、任务 owner 与保存失败没有事务边界。Project/Variant 与 Smart Assistant Session 是两个生命周期域，不能继续依赖“当前全局对象”隐式关联。

## 决策

### 1. 明确四种状态作用域

| 作用域 | 权威所有者 | 内容 | 生命周期 |
|---|---|---|---|
| Process | AppRuntime | 配置快照、capability、共享 adapter、TaskRuntime | 进程 |
| Project | ProjectAggregate | sources、source fingerprint、活动 Variant、项目策略 | 打开项目 |
| Variant | VariantAggregate | 完整条目业务状态、标签库、revision、provenance | 项目内版本 |
| Session | SessionAggregate | 可见消息、后端对话历史、活动项目/版本引用、任务引用 | 智能助手会话 |

AppContext 降级为 GUI projection/facade，只缓存当前视图所需的只读快照和发射 Qt 信号；它不再是上述状态的权威写入点。

### 2. Repository 与 Unit of Work

Application use case 通过端口访问状态：

```text
WorkspaceRepository
ProjectRepository
VariantRepository
SessionRepository
UnitOfWork
```

Repository 返回聚合或不可变快照；修改必须在 UnitOfWork 内完成，并带 expected revision。一个生命周期操作涉及多个文件时，先写入同一 staging transaction，全部校验成功后再更新 current/active 指针。

JSON 继续作为初始存储实现。Repository 端口不暴露 JSON 路径或字典，使未来在达到性能门槛时可替换存储而不改变 use case。

### 3. Variant 采用完整替换式物化

Variant V2 snapshot 对其已知来源命名空间保存完整业务状态：

- EntryKey；
- translation，包括空字符串；
- Stage；
- entry labels 与 label library；
- provenance、revision、更新时间；
- source fingerprint 和 schema version；
- 显式删除/清空 tombstone（需要与“从未提供”区分时）。

加载或切换 Variant 时，对目标来源执行 replace materialization：先把内存条目恢复到 source snapshot 基线，再应用目标 Variant 的完整状态。不得把目标 Variant 作为只覆盖非空值的 overlay。

保存时对完整 VariantAggregate 生成新 snapshot；筛选视图不得直接作为保存输入。空译文和空标签集合必须写入，防止旧值复活。

### 4. 多源命名空间与 fingerprint

每个 Project source SHALL 有稳定 source_id、format_id、规范化位置、内容 fingerprint 和迁移历史。Variant 中的 EntryKey 包含 source namespace；不同源的相同 local_key 不得覆盖。

重新定位文件时先比较 fingerprint 与 format metadata：

- 内容等价：更新位置，不改变身份；
- 可迁移：生成 key mapping 和 needs-review 诊断；
- 不兼容：阻止自动套用，要求显式迁移。

### 5. 两阶段生命周期切换

Project、Variant 和 Session 的打开/切换/关闭统一执行：

```text
prepare target
  -> validate schema/references/capabilities
  -> collect and persist current aggregate
  -> stop or detach incompatible jobs
  -> materialize target in isolation
  -> atomically commit active pointer
  -> publish projection event
```

任一步失败时，active pointer 和当前 projection 保持原值。UI 只有在 commit 事件后才更新高亮或当前会话 ID。

### 6. Session 恢复与 owner 隔离

Session V2 持久化同时保存：

- UI 可见 messages；
- ConversationManager/LLM 所需规范 history；
- SessionController 的可恢复状态或明确不可恢复原因；
- project_id、variant_id、配置/Profile 引用；
- 活动/可恢复 JobRef 与最后观察游标；
- schema version、revision、created/last-active 时间。

Session 切换先保存当前 SessionAggregate，再加载目标 aggregate，最后变更 active session。旧 Session 的事件通过 owner_id/run_id 过滤；迟到回调只写入原 owner 的事件流，不得更新当前会话 projection。

SessionController 非法状态转换 SHALL 返回有类型的 domain error；不得依赖会被 `python -O` 移除的 `assert`。

### 7. 模式验证、迁移、备份与隔离

Workspace、Project、Variant、Session、checkpoint 和 report 文件 SHALL 包含 `schema_version`。加载顺序为：解析 → schema/type 校验 → 语义引用校验 → 必要迁移 → 聚合构造。

- 可迁移旧数据：先备份原文件，再在 staging 中迁移并记录 migration report。
- 损坏或不可迁移数据：移入 quarantine 或保持原地只读，生成可定位诊断；不得当作空数据继续并覆盖原文件。
- V1→V2 Variant 迁移：非空译文可转换；缺失 Stage/provenance 的字段标记为 inferred/unknown，不能伪造历史；首次 V2 保存后使用完整快照。
- Session 文件名和内部 session_id 必须一致且通过 ID 校验，禁止由文件内容注入路径。

### 8. Projection 与 dirty 语义

Step2、Agent、Task Monitor、项目栏和 SessionList 均订阅聚合事件形成 projection。projection MAY 本地缓存筛选、搜索和布局，但不得反向成为业务数据源。

Dirty 由聚合 revision 与已持久化 revision 比较得出，不由零散 UI 信号猜测。所有 mutation 成功提交后统一更新 dirty/projection；失败不改变 revision。

## 备选方案

### 继续扩展 AppContext

短期方便 Qt 组件，但无法为 MCP/CLI 提供 headless 状态，且会继续混合视图与业务所有权，拒绝。

### 全部迁移到 SQLite

事务能力更强，但本轮可以用 repository + staging JSON 达到一致性，立即迁移会扩大风险。保留为达到可测性能/并发阈值后的 adapter 选项。

### Variant overlay

节省文件空间，但清空、删除和版本隔离依赖复杂 tombstone 合并，当前已产生数据复活缺陷。选择完整快照和替换式物化。

## 影响与风险

- 正面：清空语义、版本隔离、Session 恢复和失败回滚有统一合同。
- 成本：V1 数据需要可审计迁移，AppContext/Panel 的写路径需逐步改为 use case。
- 风险：完整 snapshot 在大项目中写放大。先通过原子 JSON 和增量内存聚合满足 NFR；若实测超预算，再以同一 repository port 引入日志式或数据库 adapter。

## 迁移与回退

1. 新建 V2 schema/validator/repository，与 V1 reader 并存。
2. 先修复 Variant replace/clear 语义并补 characterization tests。
3. 引入 Project/Variant use case，将 MainWindow 切换流程改为两阶段提交。
4. 引入 Session V2 聚合和 owner 隔离，再迁移 Panel/ChatWidget。
5. V2 写入始终保留 V1 备份；若迁移失败，回退到 V1 只读模式并阻止覆盖。
6. AppContext facade 只有在所有入口与 projection 完成迁移后才可缩减或删除字段。
