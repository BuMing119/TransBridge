# ADR-018：Project/Variant/Session 状态所有权与持久化 V2

- **状态**：已接受（2026-08-18）
- **日期**：2026-08-18
- **对应需求**：FR13.1～FR13.7、FR19、FR20.3、NFR2.1、NFR4.1
- **实施契约补录**：2026-10-19～20，第 1、5、6 节明确桌面会话固定绑定、只读浏览、后台目录与执行前精确切换；用户已确认产品行为，本次补录现有实现的跨模块合同，不新增独立技术选型，也不改变原接受日期
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
| Session | SessionAggregate | 可见消息、后端对话历史、会话绑定项目/版本引用、任务引用 | 智能助手会话 |

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

任一步失败时，active pointer 和当前 projection 保持原值。UI 只有在 commit 事件后才更新活动工程及可执行会话的 projection。只读浏览可以独立更新会话列表的选中态，但该选中态只表示正在查看的历史，不得修改活动工程指针或授予执行权限；浏览与激活的具体边界见第 6 节。

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

#### 6.1 桌面固定绑定与两种会话访问（2026-10-19 实施补录）

工程内新建会话时，`SessionWorkspace.current_context()` 从工作台 `active_version_identity` 取得真实工程与版本，交给 `GuiSessionCommandFacade.create_and_activate()`；`SessionSnapshot` 与其 `OwnerRef` 保存同一组稳定 ID。一工程可有多个会话，同一会话不得因工作台导航或名称变更改绑。未打开工程时保留无工程会话能力，不以该兼容路径替代工程内的绑定。

访问分为两个显式合同：

- **浏览**：`GuiSessionCommandFacade.read_conversation(ref, context)` 验证目标记录和 owner，返回目标历史及绑定投影；不按当前工作台筛除可浏览历史，也不调用会话激活、恢复协调、迁移或保存。需要时读取已归档的请求内容，但不得写回归档或目标会话。错误以 `SESSION_RECORD_UNAVAILABLE`、`SESSION_OWNER_MISMATCH` 等有类型诊断返回，不能伪造空会话。
- **执行激活**：`GuiSessionCommandFacade.switch()` 仍通过 `SessionLifecycleService.prepare_switch()` / `commit_switch()`。桌面调用必须传入目标绑定的工程、版本、会话及 owner；原有 `SESSION_CONTEXT_SCOPE_MISMATCH` 等校验继续作为底层边界，不因新增浏览入口而放宽。

`PanelSessions` 仅持有正在查看的值、预览标志、加载 generation 和各会话草稿。跨作用域历史在后台读取；旧读取结果若 generation 过期或面板销毁则不再投影。预览可能与后端活动 Session 不同，不能把面板选中的 ID 当作权威活动会话。`RequestSessionLoading` 在预览时只渲染保存的请求与作用域，不启动 scheduler、不恢复控制调用、不写入历史；旧视图可解除运行观察，但迟到事件仍归原 owner。

#### 6.2 统一执行门与工作台切换协议

`SessionWorkspace` 拥有桌面执行前置条件，`session_execution_access` 为输入、继续、重试、确认与撤销等入口提供共同调用方式。就绪需要同时满足：面板未释放、会话未加载或切换、视图已激活、绑定与工作台工程/版本完全一致，且工作台没有加载失败或 projection 分歧。相同工程的不同版本也视为不匹配。

用户触发执行时按以下顺序推进：

```text
检查正在查看的会话与工作台就绪状态
  -> 若仅为匹配作用域的预览，激活会话
  -> 若作用域不同，确认目标工程/版本及待执行动作
  -> switch_assistant_project(target_context, on_finished)
  -> 沿正常工程生命周期处理保存/放弃/取消并后台准备精确目标
  -> 提交工程切换，安装 Qt 工作台 projection
  -> 复核实际身份、会话 generation 与原待执行目标
  -> 激活目标 Session
  -> 继续原动作；新输入此时才采集目标选择与上下文
```

壳层适配器 `assistant_project_navigation.switch_assistant_project()` 不包含 LLM 工具能力。它调用 `ProjectCoordinator.open_project_identity()`，后者复用已有 `ProjectOpenOperation` 与 dirty 决策，再调用 `CurrentProjectOpener.prepare_identity()`；该入口使用明确的目标 `variant_id`，验证版本确属目标工程，不能回退到工程最近的活动版本。完成回调至多消费一次，只有 Qt 工作台已安装并匹配目标且没有加载失败，才可报告可继续；仅 worker 完成或磁盘活动指针变化并不够。

取消、繁忙、缺失目标、准备失败或安装失败均不得恢复待执行动作；会话草稿保留。打开失败可能涉及原有生命周期的恢复/诊断路径，不以回调的布尔值掩盖半就绪状态。若用户在切换中更换了浏览会话或关闭面板，即使工程操作随后完成，也不得执行过期的原动作。排队的输入、控制和自动续行在实际执行前再次验证身份与视图 generation，避免确认后工程变化的竞态。暂停/取消仍按原任务 owner 处理，无须为停止任务先切工程。

#### 6.3 目录投影、旧历史与故障边界

`SessionCatalogEntry` 增加可选 `project_id` / `variant_id`，缺失字段默认为空；已有 Session 模式已经包含绑定字段，本次不迁移或移动会话文件。`GuiSessionCommandFacade.list_sessions()` 从经过验证的会话记录读取归属，目录字段仅是索引提示。`SessionScopeCache` 只在源记录签名未变化时复用读取结果；文件变化、缺失或无可靠签名时重新验证，不能让旧缓存赋予已删除记录一个可执行身份。

`SessionDirectory` 在后台读取目录和工程标签，Qt 线程只安装完整的最新代际快照；连续刷新合并，过期结果与关闭后的结果丢弃，失败保留已有列表并反馈诊断。启动恢复须等到有效目录结果，不能因读取失败自动创建新会话。展示缓存不能替代会话读取、激活和精确工程打开时的权威校验。

`session_project_labels()` 只读取 Project 元数据、版本目录和版本文件存在性，供分组、名称及失效原因展示，不加载翻译条目，也不是最终打开校验。工程或版本失效不妨碍读取仍完整的会话历史，但执行门必须阻止操作。缺少完整绑定的旧记录保留“未归属”状态，不从目录名称、当前工程或历史文本推断；工程已打开时仅供浏览并提示新建对话。无工程工作台上的无工程会话保留既有行为，不能跨入工程上下文。

真正缺少 owner 的合法旧格式记录沿用现有兼容规则，在内存中关联当前请求 owner 并标记降级恢复；共享 `session_snapshot_for_context()` 供只读预览和生命周期适配使用。显式保存的 owner 始终有效，即使记录还带有过时的 `owner_scope_missing` 诊断也不得改归属。兼容处理保留原 Project/Variant，预览不迁移或写回文件。

这些合同落实 [FR13.1～FR13.7](../requirements.md)；实现及验收落点见 [assistant-ui-remediation 第五阶段 S24～S26](../../plans/assistant-ui-remediation/plan.md) 与 [专项 QA](../test-reports/assistant-project-conversations-qa-2026-10-19.md)。系统默认外框、侧栏层次和主题表现遵守 FR13.8 及现有 UI Foundation，不改变本 ADR 的持久化所有权。

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

### 只显示当前工程会话，或由 AI 自主切换本地工程

2026-10-19 用户确认采用“全局分组浏览、执行前确认切换”。只显示当前工程会话会限制查阅其他历史；把本地切换直接交给 AI 工具会使工作台身份在对话执行中改变，扩大跨上下文风险。当前选择让用户浏览与执行授权分离，复用既有工程打开流程；`switch_paratranz_project` 继续表示同步目标变更，不承担本地工程切换。

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
7. 2026-10-19 桌面接入不批量改绑旧会话，不升级 Session schema，不对只读浏览执行迁移；新增目录字段兼容旧目录缺失值。回退这次 UI 接线时保留原会话记录与稳定绑定，不将目录字段降级或删除作为回退步骤。即使暂时退回仅当前工程的界面，也必须保留底层 owner/工程/版本校验；没有重绑或推断旧归属的自动回退路径。
