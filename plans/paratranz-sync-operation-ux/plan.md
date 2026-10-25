# ParaTranz 上传/下载操作页重设计实施计划

- **Feature slug**：`paratranz-sync-operation-ux`
- **状态**：S01～S16 已交付；反馈修复及最终联合回归 625 通过、2 跳过
- **日期**：2026-10-23
- **对应需求**：[FR3.3、FR22.3～FR22.4、FR22.6～FR22.8、FR26.8、NFR1.6](../../docs/requirements.md)
- **架构约束**：[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-021](../../docs/adr/021-ui-presentation-modularization.md)、[ADR-023](../../docs/adr/023-local-project-paratranz-binding.md)
- **承接计划**：[guided-ui-workflows](../guided-ui-workflows/plan.md)、[paratranz-project-binding](../paratranz-project-binding/plan.md)、[paratranz-sync-service-v2](../paratranz-sync-service-v2/plan.md)

## 目标

把同步操作组织为用户能直接理解的 ParaTranz 上传/下载任务页。先选项目和同步方式，再点击“开始对照”，查看影响后正式执行。下载只更新当前工程译文和状态，不写回 ESM/ESP；内容差异是预计更新，无法确定对应关系的条目才暂缓。

完成后应满足：

1. 用户以项目名称识别 ParaTranz 目标，不需要知道或手填项目 ID；ID 只在同名消歧或技术信息中显示。
2. `project_binding`、`true/false`、`abort` 等内部值不进入普通界面。
3. 页面打开和选项变化不自动访问云端；用户点击“开始对照”后在后台检查。对照完成后同一主按钮变为正式动作，检查本身不授权写入。
4. 下载默认采用远端内容更新可靠匹配的本地条目；普通内容差异映射为预计更新数，歧义条目暂缓并保持本地不变，其他已确定条目可以先更新。权限、目标身份、快照过期及事务异常仍阻断执行。
5. 覆盖、删除、跳过和新增影响在最终确认前可见；正式执行继续使用不可变计划、一次性确认、TaskRuntime 和原子本地提交。
6. 任何“历史还原点/备份”文案必须对应真实创建成功的恢复产物，不再用内存快照冒充可回滚备份。
7. 待处理入口支持选择具体云端候选、逐项或批量保留本地、本次跳过，并在同步后继续处理；不能只展示不可操作的冲突明细。
8. 上传复用已确认的云端关联，明确项先执行；冲突项可选择具体目标或逐项/批量保留云端。已有云端记录仅更新译文和状态，保留原文、上下文与 key；成功关联在本地持久化，以支持下载、编辑、上传的往返。

## 非目标

- 不重写 ParaTranz HTTP 客户端、离线 JSON 转换或 EntryKey/ExternalEntryRef 身份模型。
- 不取消执行前的权限、目标修订、远端快照和计划新鲜度校验；同步确认不按固定时间过期，仍绑定单次操作和数据。
- 不在本轮重设计写回和 FOMOD 页面；它们继续使用现有通用计划窗口，后续可复用本轮的自动预检状态机。
- 不通过项目名称猜测或自动改绑目标；选择器仍以远端项目 ID 作为内部稳定身份。
- 不把 ParaTranz 管理页最后浏览项目恢复为同步目标来源。

## 下载闭环修复前的事实与根因

- 名称优先的项目选择、专用同步页、自动后台预检、远端优先策略和真实历史还原点已有实现，入口位于 `ui/operations/paratranz_sync.py` 与 `paratranz_dialog.py`。
- 下载预检把任意 `plan.conflicts > 0` 设为整次阻断；用户看到“请查看变更明细”，界面却没有可操作的处理入口。
- planner 以完整 EntryKey 分组，重复 key、重复远端 ID 无法人工消歧；带原文身份的条目直接跳过，未利用已有远端关联或唯一原文证据。
- 统计将所有 SKIP 显示为“保留”，混淆本地保留和云端独有内容。全冲突及同步后继续处理缺少对应状态。
- executor 的本地候选以 dict 聚合，开放部分执行前必须修正重复本地身份被合并丢失的风险，并按计划选中的远端记录提交。
- executor 超过 500 行，责任审查后保留原有授权检查、重试和事务调度；新匹配与本地候选聚合构建分别放入独立模块，未向执行器加入匹配职责。后续若新增执行模式或持久化策略，先拆分现有事务端口与结果组装。冲突匹配、预检投影和交互各自保持内聚。

## 目标交互

### 页面信息架构

```text
┌ 从 ParaTranz 更新本地翻译 ─────────────────────────────────────┐
│ 云端项目                                                        │
│ Vigilant SE Translation              当前工程已绑定   [更换]    │
│ 本地范围：Vigilant / 默认版本 / 8,300 条翻译内容                 │
│                                                                  │
│ 下载方式                                                        │
│ ● 使用 ParaTranz 内容更新本地（推荐）                            │
│   同键内容以云端为准；本地独有内容保留                           │
│ ○ 保留本地已有内容，只补充云端新增内容                           │
│                                                                  │
│ 恢复保护：下载前自动创建历史还原点                               │
│                                                                  │
│ 预计变化（点击“开始对照”后检查）                                │
│ 更新 8,096  新增 204  保留 0  删除 0  需处理 0                  │
│ [确认需要选择的翻译…]                                            │
│                                                                  │
│                                             [取消] [更新工程译文]│
└──────────────────────────────────────────────────────────────────┘
```

### 目标选择

- 当前工程已有绑定时直接显示绑定保存的项目名称和“当前工程已绑定”，不发起无意义的目录请求。
- 点击“更换”打开已有“我的项目”目录：名称为主信息，ID 仅作为次要消歧信息；用户不能手填 ID。
- 本次选择与工程绑定分开：仅当选择了不同目标且存在活动本地工程时显示复选框“以后默认使用这个云端项目”。
- 复选框提交真实布尔值；界面永不显示 `true/false`。
- 同名项目允许选择，但列表和技术详情展示 `#ID` 以避免误选；目标卡仍以名称作为主要识别信息。

### 下载策略

- 默认“使用 ParaTranz 内容更新本地”映射为 `ConflictPolicy.PREFER_REMOTE`。同键内容不同计为 `UPDATE_LOCAL`，不计为未解决冲突。
- “保留本地已有内容，只补充云端新增内容”映射为保留本地策略。实施时必须验证远端 tombstone/删除语义与文案一致；若当前 `PREFER_LOCAL` 仍会删除本地项，则需增加独立删除策略，不能用不真实文案掩盖。
- 删除影响单列显示。默认不静默删除本地内容；若用户开启“同步远端删除”，该选择进入 request digest/plan hash 并在按钮旁明确显示删除数量。
- 重复本地键和重复远端 ID 不得任意选择覆盖。同键候选的实际原文、译文、上下文和状态全部一致时，可采用共同内容更新工程，但不猜测或保存某个云端 ID；上传仍需明确对应目标。其他歧义项暂缓。目标/账号/端点变化仍阻断整次提交。

### 主动对照与一次执行确认

```text
打开页面/更换项目/修改策略
          ↓
失效旧计划，保留可编辑选项
          ↓
点击“开始对照”并后台检查
          ↓
   检查中 ──→ 权限/身份/新鲜度阻断：显示原因和修复动作
          ↓
显示预计新增/更新/删除/本地保留/云端未采用/暂缓数量
          ↓
处理待处理项：明确选择候选或保留本地 → 重新预检
          ↓
用户点击一次“更新工程译文”
          ↓
创建真实历史还原点 → 消费一次性确认 → 提交 TaskRuntime
```

- 页面以“开始对照”触发读取；选项变化使旧计划失效，等待用户重新开始对照。
- 对照中主按钮显示“正在对照…”并禁用；通过后变为“更新工程译文”，无变化时显示“工程译文已是最新”。
- “更新工程译文”是唯一正式确认点，按钮附近展示目标名称、更新数和删除数，满足覆盖性计划显式确认要求。
- 用户编辑导致旧预检过期属于正常状态：取消或忽略旧 generation，不弹出 `operation plan changed while preflight was running`。
- 提交瞬间远端或本地再次变化时，显示“内容刚刚发生变化，正在重新检查”，自动刷新一次；持续变化才保留可操作错误。
- 取消预检只释放本页 worker/service/cache，不产生远端写入、本地提交或绑定变更。

### 状态与文案

- `尚未对照` → `先选择同步方式，再开始对照`
- `存在 8096 个未解决冲突` → `预计用云端内容更新 8,096 条本地翻译`
- `PARATRANZ_MEMBER_REQUIRED` → `当前账号无权访问“项目名”`，并提供“更换项目”或“检查账号设置”。
- `PREFLIGHT_STALE` → 不显示技术错误；自动刷新或提示“选项已变化，已重新检查”。
- `Cancel` → `取消`；所有用户可见错误使用中文业务描述，稳定错误码只进入可展开技术详情和日志。

## Story-01：同步请求与计划语义校准

**目标**：先修正“下载差异即冲突”的根因，让 UI 展示的策略真实进入 planner 和 plan hash。

**验收标准**：

- [ ] `SyncRequest`/draft 携带目标展示名称、冲突策略和删除策略；目标 ID 继续作为内部身份。
- [ ] 下载默认策略为 `PREFER_REMOTE`，上传默认策略为 `PREFER_LOCAL`；显式用户选择覆盖默认值。
- [ ] `CreateSyncPlanRequest` 使用 request 中冻结的策略，不再硬编码 `ABORT`。
- [ ] 普通内容差异在下载远端优先模式下产生 `UPDATE_LOCAL`；未消歧身份异常产生 `CONFLICT`，下载时暂缓并保持本地不变，上传/双向同步保留原有语义。
- [ ] 新增、更新、跳过、删除、结构性冲突计数映射为 Qt-free `ParaTranzSyncImpactState`，不靠解析提示字符串恢复数据。
- [ ] 项目名、策略、删除选择和目标来源/修订全部进入 request digest；修改任一项使旧预检与确认失效。

**文件落点**：

- 修改 `src/transbridge/ui/operations/production_support.py` 中的同步 request/target 解析。
- 从 `src/transbridge/ui/operations/production.py` 抽取 ParaTranz 同步组合到新的内聚模块，例如 `src/transbridge/ui/operations/paratranz_sync.py`；原 composition root 只装配 feature adapter。
- 新增 Qt-free `src/transbridge/ui/operations/paratranz_view.py`。
- 仅在删除语义无法由现有策略真实表达时，修改 `src/transbridge/application/sync/models.py`、`planner.py` 和 `use_case.py`，增加显式删除策略并纳入 plan hash。

**实施步骤**：

1. 冻结下载/上传的默认策略和用户文案映射。
2. 让 target resolver 接收目录选择器返回的 `project_id + project_name` typed choice，不再从文本框解析 ID。
3. 把 request policy 传入 planning use case，映射 `SyncPlan.counts` 为用户影响摘要。
4. 区分可按策略解决的内容差异与不可安全配对的结构性冲突。
5. 保留 Agent/MCP 对显式 `project_id` 的兼容，不把 GUI 的名称优先展示变成领域身份变化。

**测试策略**：

- 扩展 `tests/contracts/paratranz/test_sync_plan_confirmation.py` 和 `test_sync_execution.py`，覆盖远端优先、保留本地、删除选择、结构性冲突和 plan hash。
- 扩展 `tests/ui/operations/test_production_facade.py`、`test_paratranz_target_binding.py`，断言生产 adapter 不再固定传入 `ABORT`，且项目名/策略进入 digest。
- 保留 Agent/MCP plan DTO 兼容测试，防止 GUI 调整污染其他入口。

## Story-02：项目选择器复用与任务化下载页

**目标**：用项目名、选择控件和用户任务语言替代技术文本表单。

**验收标准**：

- [ ] 下载页标题为“从 ParaTranz 更新本地翻译”，上传页使用对应任务语言；不显示 `project_binding`。
- [ ] 当前目标以项目名为主信息；ID 仅用于同名消歧、tooltip/技术详情，不提供手填输入框。
- [ ] “设为工程默认”使用 `QCheckBox`，只在本次目标与工程绑定不同且可持久化时出现。
- [ ] 策略使用带解释的单选项；危险删除选项与高频主动作分层。
- [ ] 范围显示本地工程、翻译版本和“8,300 条翻译内容”，不使用“本地对象”等内部术语。
- [ ] 页脚只有“取消”和一个主动作；没有“运行预检”“返回编辑”“确认并开始”。
- [ ] 520～800 px 宽度、125%/150% DPI、长项目名、五位数计数和中文字体下不截断主信息。

**文件落点**：

- 从 `src/transbridge/ui/workbench/remote_target_view.py` 提取可复用的项目目录选择器到独立模块，例如 `src/transbridge/ui/paratranz/project_picker.py`；Workbench 绑定页和操作页通过不同文案/提交 adapter 复用。
- 新增 `src/transbridge/ui/operations/paratranz_dialog.py`，仅渲染 typed ParaTranz sync state 并发出用户 intent。
- 修改 `src/transbridge/ui/operations/coordinator.py`/`facade.py`，允许按 OperationKind 注入专用 dialog factory；写回/FOMOD 保持现有 dialog。

**实施步骤**：

1. 将项目目录加载、搜索、取消、配置 revision 隔离与“绑定到工程”动作拆开。
2. 建立 ParaTranz sync 专用 ViewPort/ViewState，避免继续扩充只能表示文本框的通用 `EditableFieldState`。
3. 实现目标卡、范围摘要、策略卡、恢复保护、影响摘要和精简页脚。
4. 将选择结果以 typed value 提交给 coordinator；所有 label/value 映射集中在 Presenter。
5. 补齐可访问名称、默认焦点、Enter 主动作和 Esc 取消；界面文案全部进入现有 i18n 资源。

**测试策略**：

- 新增项目选择器的名称优先、同名消歧、取消、配置变化和迟到结果 UI 测试。
- 新增 ParaTranz dialog 的控件类型、可见性、中文文案、焦点、键盘和单主动作测试。
- 扩展 `tests/ui/operations/test_operation_plan_layout_stability.py` 与 accessibility/theme matrix，覆盖长项目名、DPI 和浅/深主题。

## Story-03：对照与单次确认状态机

**目标**：保留后端两阶段安全合同，对照完成后由一次正式确认启动执行；S13、S15 按真实反馈修订确认时效与读取触发方式。

**验收标准**：

- [ ] 页面首次打开不访问云端；点击“开始对照”后预检，项目/策略/删除选项变化后旧计划失效。
- [ ] 同一 generation 最多一个活动预检；旧 worker 可取消则取消，不可取消则忽略迟到结果并安全释放 service/cache。
- [ ] 预检 ready 前主动作禁用且原因可见；ready 后一次点击只提交一个 command、一个 Run ID。
- [ ] 最终按钮消费与当前 request digest/target revision 绑定的一次性确认 token；重复点击、迟到 signal 和 token 重放均不重复提交。
- [ ] 用户编辑造成的 stale 结果不弹技术错误；执行前真实快照变化触发可理解的自动重检/恢复路径。
- [ ] 预检和取消继续保持零远端写入、零本地正式提交、零隐式绑定。

**文件落点**：

- 修改 `src/transbridge/ui/operations/coordinator.py`，增加专用自动预检 controller/generation/cancellation，而不是把网络生命周期放进 View。
- 必要时扩展 `plan_presenter.py` 的 Qt-free 状态事件，但保留现有 plan hash、owner 和 confirmation authority。
- 修改 `preflight_view.py` 或新增 ParaTranz 专用预检 projection，以 typed impact/repair action 表达结果。

**实施步骤**：

1. 把现有 `apply_edits → preflight worker → confirm` 调用链封装为明确的 editing/checking/ready/blocked/submitting 状态。
2. 字段变化时生成新 revision，取消/忽略旧 generation；用户开始对照后运行只读 preflight。
3. ready 后缓存与当前 digest 匹配的 token 和 typed impact；View 不接触 token 内容。
4. 用户点击主动作时再次核对 revision，消费 token 并提交；stale 时刷新而不是执行。
5. 把 `PREFLIGHT_STALE`、identity mismatch、成员/认证/网络错误映射为用户语言和修复 intent。

**测试策略**：

- 扩展 `tests/ui/operations/test_operation_plan_coordinator_async.py`，覆盖主动对照、快速改选、迟到结果、取消、重复点击、窗口销毁和 worker 清理。
- 扩展 `test_operation_plan_presenter.py`，覆盖一次性 token、revision/digest 变化和 stale 安全失败。
- 增加可观察旅程断言：从页面 ready 到任务开始只有一次用户点击，不出现额外确认模态框。

## Story-04：恢复保护、结果文案与端到端验收

**目标**：让“可恢复”和“原子合并”成为真实能力，并验证 8,000+ 条目的实际下载旅程。

**验收标准**：

- [ ] 会覆盖或删除本地内容的下载在提交 TaskRuntime 前创建包含本次计划输入的真实历史还原点；创建失败时不开始下载。
- [ ] 还原点名称包含任务类型、时间和短 Run/plan identity，结果页可定位该还原点；不包含凭据或远端敏感数据。
- [ ] 只新增且无破坏性影响的计划可按明确规则跳过额外还原点，但界面不得声称已创建。
- [ ] 原子 UoW 失败不留下半合并集合；成功、部分失败、取消和 stale 均显示用户语言计数及下一步。
- [ ] 8,300 条本地内容、8,096 条远端更新的 fixture 下，主动对照显示更新而非冲突，单次确认后产生一个下载 Run。
- [ ] guided/compact 模式能力等价，窗口关闭、项目/账号/端点变化和任务完成后无 worker、service、subscription 泄漏。

**文件落点**：

- 复用并按需提取 `src/transbridge/ui/version_persistence.py` 的版本快照边界；不得复制 AI Translator 的私有运行状态机。
- 在 ParaTranz sync operation adapter 中增加恢复保护编排，保持 application sync executor 和 TaskRuntime 为正式执行权威。
- 扩展 operation result/任务投影的中文摘要和还原点 reference。
- 新增/扩展 `tests/ui/ux/test_current_user_journeys.py`、operation production tests、sync contract tests和生命周期测试。

**实施步骤**：

1. 用与计划相同的本地 snapshot/revision 创建下载前还原点，验证其内容和 active project/version identity。
2. 只有还原点成功且当前 plan 仍新鲜时才消费确认并提交任务。
3. 将 SyncPlan counts 和 OperationResult counts 映射为“新增/更新/保留/删除/失败”，隐藏原始枚举。
4. 建立 8,300/8,096 规模 fixture，验证正常旅程、结构性冲突、网络错误、取消和提交前变化。
5. 运行相关静态检查、格式检查和 Windows Qt 生命周期/布局门禁。

**测试策略**：

- 快照失败、快照内容不匹配、项目切换、计划过期均断言零正式同步副作用。
- 下载成功断言一次原子本地替换、一个 Run ID、正确结果计数和可用还原点。
- 取消/错误断言旧本地集合保持一致；真实网络测试继续使用受控服务，不读取用户 Token。

## Story-05：可靠匹配与安全部分执行

**状态**：已完成；证据见本计划末尾的 S05～S07 验证记录。

**验收**：

- [x] 下载通过唯一远端关联或同键、原文等证据自动匹配；候选仍有歧义时不任意选第一项。
- [x] 决定包含本地与候选快照指纹；内容变化后的旧选择失效，其他安全项提交后未变冲突可以继续处理。
- [x] 已确定项一次原子提交，暂缓项保持条目、原文、译文、状态、关联及修订不变；重复身份不得因 dict 压缩丢失。
- [x] 原文限定身份、重复远端键/ID、重复本地键、取消、事务失败、过期计划和重试都有回归证据。
- [x] 上传/双向同步接口和已有安全语义保持兼容，业务正文不新增到通用计划导出。

**落点与实施**：`application/sync/` 中新增下载匹配/决定模型和本地候选聚合构建模块；`planner.py`、`use_case.py` 接入下载路径；`executor.py` 使用计划选定的候选并保留完整集合。

**验证**：`tests/contracts/paratranz/` 中匹配、执行、回滚、授权及新增部分下载测试。

## Story-06：可操作复核与继续处理

**状态**：已完成；依赖 S05 数据契约。

**验收**：

- [x] 主界面提供实际“确认 N 条翻译”入口；复核页比较两边的原文、译文及补充说明，记录标识按需展开。
- [x] 用户明确选中候选后才能采用云端；可多选、筛选并批量保留本地，也可恢复为暂缓。
- [x] 应用选择仅更新请求并重新预检；最终主按钮确认后才写本地。取消复核不产生副作用。
- [x] 全冲突不能误报最新；存在安全更新时可先执行；执行后保留处理入口，支持复核、重检和再次提交。
- [x] 用模型列表处理数千条待处理项，不为每行创建候选控件；忙碌期间禁止更改。

**落点与实施**：`ui/operations/sync_review_view.py`、独立复核对话框及模型；`paratranz_dialog.py` 接入入口并提取完整展示职责，保持主模块体积受控。

**验证**：真实 Qt 控件交互、选择/批量/取消/陈旧结果、全冲突、执行后继续、长文本与数千项测试。

## Story-07：生产链路、统计与交付

**状态**：已完成；依赖 S05～S06。

**验收**：

- [x] `SyncRequest → CreateSyncPlanRequest → preflight → UI → 决定 → digest → 授权 → TaskRuntime` 全链路贯通。
- [x] 本地保留、云端未采用、暂缓分别统计；执行结果清楚显示完成与暂缓，正常暂缓不冒充执行失败。
- [x] V2 权威工程的部分下载可以重开恢复，手动决定后再次提交；原有恢复点与并发修订保护有效。
- [x] 运行相关 contracts、UI、助手入口兼容测试及全库 Ruff 检查，记录真实结果并清理本轮临时目录。

**落点与实施**：`production_support.py`、`paratranz_sync.py`、预检 DTO/mapper 与结果文案；新增生产 adapter 与权威存储集成回归；FR22.3 和本计划作为当前契约。

## Story-08：按确认关联上传并保护云端来源

**状态**：已完成；用户于 2026-10-20 授权上传往返修复。

**当前缺口**：下载已保存的远端引用未被上传匹配使用；原文限定身份被整体跳过，普通 key 有差异则提交全部字段；上传结果中的远端 ID 未保存到本地。

**验收**：

- [x] 优先按同一云端项目的可靠远端 ID 定位，支持原文限定身份和云端 key 改名；失效、重复、占用或共享关联不得静默重绑。
- [x] 没有关联时按唯一身份和原文证据匹配；仍有歧义时暂缓，普通确定的本地独有项可单独列为新增。
- [x] 更新已有云端记录的真实请求仅包含译文和状态，不发送原文、上下文、key 或顺序；取消上传的 context-only refresh。
- [x] 上传成功后使用现有本地 UoW 原子保存成功项的关联，未执行/失败项保持本地不变；关联保存失败仍保留远端成功事实。
- [x] 授权、新鲜度、取消、部分失败与重试保持可判定，不能因未存关联重复新增。

**落点与步骤**：在 `application/sync/` 中实现上传规划和复核契约；从执行器提取完整远端写入/本地事务职责；`application/ports/paratranz.py` 与 `paratranz/service.py` 增加仅译文/状态更新边界。保留下载公共接口，双向同步维持既有显式语义。

**验证**：领域合同与真实 service 请求字段测试；重复/失效引用、关联保存、部分失败、取消、重试和云端变更测试。

## Story-09：上传可操作复核与继续处理

**状态**：已完成；依赖 S08 的候选与决定契约。

**验收**：

- [x] 复用内容导航与比较页；上传显示“用我的译文更新这条”，说明只更新云端译文和状态；下载显示“采用这条云端译文”。
- [x] 可以选择具体云端目标、逐项或筛选批量保留云端、恢复暂缓；不默认选中候选。
- [x] 人工决定绑定方向、作用域与所见内容，应用决定后重新预检，确认后才执行。
- [x] 确定项可先上传；完成后继续处理剩余项，全部暂缓不得显示一致；忙碌、取消和异常状态真实。

**落点与步骤**：`sync_review_*` 复用方向无关的模型，提取方向文案；`paratranz_dialog.py`、`sync_preflight_presentation.py`、`sync_result.py` 贯通两种方向。生产预检拆到独立模块，避免主适配器继续膨胀。

**验证**：真实 Qt 选择/批量/筛选/取消、陈旧结果、完成后继续，以及既有下载回归。

## Story-10：生产往返、持久化与交付验证

**状态**：已完成；依赖 S08～S09。

**验收**：

- [x] 真实 V2 工程经下载、实际本地编辑、上传后，云端译文/状态更新且原文/上下文/key 不变；本地保存重开仍保有精确关联。
- [x] 验证云端 key 改名、同 key 多原文、手选目标、部分执行后继续以及同文关联持久化。
- [x] 验证确认后内容/目标变化、部分失败和关联提交异常，明确区分远端已提交与本地关联未保存。
- [x] 相关合同、UI、生产适配及助手入口回归通过；完成 Ruff、增量记录与本轮临时文件清理。

**落点与步骤**：`production_support.py`、`sync_preparation.py`、`paratranz_sync.py` 接入上传决定与权威关联提交；新增 `test_paratranz_upload_roundtrip.py`，真实 service 仅将 HTTP endpoint 替换为内存 fixture；更新 FR22.3、FR22.4 与 ADR-045 的当前同步合同。

**风险和边界**：远端逐项写入不能整批原子回滚；显示逐项成功/失败并安全重检。本轮不新增自动背景同步或隐式三方合并，不更改离线格式，不重写来源文件，不操作用户实际云端项目。

## Story-11：面向翻译用户的逐条确认

**状态**：已完成；承接用户对复核窗口“太开发流程向”的反馈。

**目标**：用内容和选择后的结果组织界面，让用户不用理解匹配、候选和预检流程也能完成确认。

**验收**：

- [x] 左侧以翻译内容识别条目，右侧展示单条问题及两边的原文、译文、补充说明；编号移入可展开的记录信息。
- [x] 云端选项展示内容摘要；只浏览不会产生决定，无法采用的内容仍可查看并说明原因。
- [x] 上传和下载动作明确说明替换哪一边；无法找到对应内容时有可执行的跳过入口。
- [x] 已选择、尚未决定与实际写入分开表达；完成选择返回主窗口自动检查，正式确认后才执行，取消不保存本轮选择。
- [x] 保留筛选、多选及批量跳过、撤销选择、下条导航；数千条使用模型，不逐条创建控件。
- [x] 验证双向交互、取消、空结果、长文本、不可选内容、批量过滤和真实中文窗口截图。

**落点与步骤**：重新组织 `sync_review_dialog.py`；将单条比较面板和列表绘制分别提取为独立 UI 职责，保留 `sync_review_model.py` 的选择校验和批量通知；更新投影及主窗口相关文案，补充真实 Qt 回归。领域匹配、请求摘要、远端写入和本地持久化合同不变，无迁移。

**交付证据**：联合 `tests/ui/operations` 与 `tests/contracts/paratranz` 343 通过、1 个真实账户测试按配置跳过；全库 Ruff 和格式检查通过。含 32 项逐条确认交互及 4 项真实主题小窗口测试，已检查上传/下载实际渲染截图。详见 [S11 增量记录](../../docs/changelogs/paratranz-sync-operation-ux/story-11/2026-10-20-001-readable-translation-review.md)。

## Story-12：同步检查原因统计与逐条诊断日志

**状态**：已完成；用户明确本轮只补日志，不添加报告、导出或界面入口。批量匹配修复等用户重新检查后的真实数据再分析。

**验收**：

- [x] 上传、下载每次完成检查后记录 INFO 级摘要：独立检查编号、方向、项目、计划、影响数量、各类未解决原因的数量及中文解释。
- [x] 每条未解决项记录具体原因、本地标识、已有云端关联、候选数量及候选编号、可用性与匹配证据；不采样遗漏数千条记录。
- [x] 原因数量与计划冲突数一致；已解决项不冒充冲突，重复本地条目说明实际出现次数；零冲突也留摘要。
- [x] 日志中每次重新检查可区分，过期选择后的重新计算有标记；失败检查不留下伪造的成功诊断。
- [x] 使用现有应用日志，结构化内容转义换行，不输出 Token、配置或原文译文正文；有原文指纹与相等比较，上下文先脱敏后截取 240 字符并附完整值指纹和长度。
- [x] 不改变匹配、同步确认和执行行为，不增加云端写入；覆盖两方向、失效、异常、零项及 2,181 条完整记录，诊断投影异常保留已生成计划并记录异常。

**落点与步骤**：新增独立 Qt-free `paratranz/sync_diagnostics.py` 负责日志及证据投影，`ui/operations/sync_preparation.py` 在已有后台预检链路接入；新增日志单测与生产预检回归。复用现有日志文件与配置，不生成独立报告，不更改公共计划导出和业务正文。

**交付证据**：新增日志合同 19 项、生产预检 19 项；联合 `tests/ui/operations`、`tests/contracts/paratranz`、`tests/paratranz`、数据边界与日志配置测试共 512 通过、1 个真实账户测试按配置跳过。全库 Ruff 与格式检查通过，未连接真实云端。详见 [S12 增量记录](../../docs/changelogs/paratranz-sync-operation-ux/story-12/2026-10-20-001-sync-diagnostic-logging.md)。

## Story-13：按操作与数据校验确认及执行诊断

**状态**：已完成；用户已明确授权本次反馈修复。

**验收**：

- [x] ParaTranz 两层确认均不设固定倒计时；等待或超过 118 秒的读取不使未变化的计划失效。其他操作的确认合同保持兼容。
- [x] 操作、目标、选项、本地/远端数据变化仍阻止旧计划；确认一次性消费，不可重放。
- [x] 准备、授权、恢复点、执行、新鲜度、提交及结束均有可关联的执行日志；异常记录真实阶段、原因和堆栈，界面取得具体失败结果。
- [x] 日志沿用现有脱敏，不输出凭据及翻译正文；无独立报告或导出入口。

**落点与验证**：`application/security/hitl.py`、`application/sync/use_case.py`、`task_adapter.py`、`executor.py`、`ui/operations/plan_presenter.py` 和 `sync_result.py`。使用可控时钟及错误注入覆盖长读取、重放、数据变化、授权/恢复点/快照/事务失败。

## Story-14：重复内容与任务日志错位匹配

**状态**：已完成。

**验收**：

- [x] 重复候选确实具有相同原文、译文、上下文和状态时，下载采用共同内容，不要求逐条选择，也不保存任意候选 ID。
- [x] 不同译文或状态、失效引用、重复 ID、已占用目标保持可操作冲突；上传不自动选第一项、不广播更新重复项。
- [x] `QUST:CNAM` 只在同一任务记录内以完全相同原文识别序号移动；不按文本前缀、长度、模糊相似或跨任务匹配。
- [x] 唯一匹配保留真实关联；有明确关联时继续复用，并覆盖候选顺序变化和多次往返。

**落点与验证**：`application/sync/` 中独立匹配职责及下载/上传规划、本地候选构建；合同测试验证 54 个重复项、不同状态/译文、同任务跨序号、跨任务隔离、选择失效与安全部分执行。

## Story-15：主动开始对照与工程更新范围

**状态**：已完成。

**验收**：

- [x] 打开窗口可先选项目和同步方式，不自动进行完整云端读取；“开始对照”后后台执行检查。
- [x] 更改选项清除旧影响与确认；对照中不能误执行，取消和迟到结果安全清理。复核选择后重检仍不写数据。
- [x] 下载标题、按钮和范围说明明确“更新工程译文和状态”，不写回 ESM/ESP；上传仍只更新已有云端译文和状态。
- [x] 真实 Qt 控件验证初始状态、选项修改、取消、失败重试和完成后继续处理。

**落点与验证**：`ui/operations/paratranz_dialog.py`、展示投影、`coordinator.py`、`paratranz_sync.py` 与相应 UI 测试。模块超限时按完整展示职责提取，不扩展通用控制器职责。

## Story-16：反馈往返与交付回归

**状态**：已完成；625 项联合回归通过、2 项按环境跳过。

**验收**：

- [x] 真实 V2 工程经任务日志错位下载、编辑、上传并保存重开，更新正确云端记录，原文、上下文和 key 保持不变。
- [x] 重复内容下载不产生伪关联，后续上传提供明确目标选择，确认后持久化且再次上传不重复创建。
- [x] 长读取与执行日志覆盖生产入口；相关合同、UI、助手兼容回归通过；本次相关 92 个文件 Ruff/格式检查通过。全库检查仅余其他任务中两个 UI 测试文件的格式问题，未修改无关工作。
- [x] 更新需求、计划及增量记录，清理本轮临时文件；不操作用户真实云端项目或插件文件。

**交付证据**：最终联合回归 625 通过、2 跳过；本次相关 92 个文件 Ruff 与格式检查通过。全库检查存在其他任务两个测试文件的格式问题，未改无关工作。含 118.147 秒读取与一小时等待模拟、真实 V2 往返、54 条共同内容及 53+1 状态分组、执行日志脱敏、字体和小窗口实渲染。详见 [S13～S16 增量记录](../../docs/changelogs/paratranz-sync-operation-ux/story-13-to-16/2026-10-23-001-feedback-sync-roundtrip.md)。

## 依赖顺序

1. S01 先修正 request/policy/impact 的事实来源。
2. S02 可在 S01 Qt-free state 冻结后实现项目选择和页面布局。
3. S03 依赖 S01 的 digest 和 S02 的 typed intent，完成自动预检与一次确认。
4. S04 最后接入真实恢复保护和完整旅程；未通过前不得在界面显示“已创建还原点”。
5. 本轮在已有 S01～S04 实现上完成 S05；S06 在数据契约确定后并行实现；S07 最后贯通并复验。
6. 上传续作先冻结 S08 领域接口，S09 并行构建交互，S10 贯通生产权威与实际保存重开后交付。

## 兼容、迁移与回退

- Agent/MCP 继续接受显式 `project_id`；GUI 不手填 ID 只是展示层变化。
- 旧 Project binding 已保存 `project_name`，无需数据迁移；名称为空的旧数据在重新验证目录后补足展示名，不按名称猜目标。
- 现有 `OperationPlanPresenter`、confirmation authority、SyncPlan hash、TaskRuntime 和 executor 保留；新页面是专用 adapter，不建立第二套业务权威。
- 可按 composition factory 回退 ParaTranz 专用 dialog 到旧通用 dialog，但不得回退到硬编码 `ABORT`、最后浏览项目或静默跳过确认。
- 若真实历史还原点暂不可可靠实现，界面必须移除相应承诺并明确“失败前原子保护，不提供成功后的撤销”，不能保留误导文案。

## 风险与控制

- **项目重名**：名称优先但 ID 保留为次要消歧信息；内部身份始终使用 ID。
- **大项目读取耗时**：由用户主动开始对照，选项变化只失效旧计划；使用 generation 和有界缓存忽略迟到结果，不引入 polling。
- **远端优先覆盖错误对象**：request digest 冻结目标、范围、策略、删除选择和人工决定；歧义项保持本地不变，身份/权限/新鲜度失配仍拒绝提交。
- **打开页面后远端持续变化**：提交前 freshness 校验保留；自动重检有次数上限，避免无限循环。
- **专用 UI 与通用框架分叉**：只分叉 View 和 interaction controller，plan/confirmation/task/result 权威合同继续共享。
- **模块继续膨胀**：ParaTranz 同步组合从 556 行的 `production.py` 完整抽取，不创建 generic helpers/mixin 杂物层。

## 验证命令

实现时先运行 focused tests，再执行：

```text
uv run pytest tests/contracts/paratranz/test_sync_plan_confirmation.py tests/contracts/paratranz/test_sync_execution.py -q
uv run pytest tests/ui/operations -q
uv run pytest tests/ui/ux/test_current_user_journeys.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

真实 ParaTranz 联机冒烟只在用户显式提供测试账号/项目时执行；默认使用受控服务 fixture，不读取生产凭据。

### S05～S07 验证记录（2026-10-20）

- 使用仓库 Python 3.12 环境，以 `uv run --no-sync --no-cache` 避开受限的用户缓存，不安装依赖。
- 最终定向联合回归：`tests/contracts/paratranz`、`tests/ui/operations`、`tests/smart_assistant/tools/test_paratranz_sync_plan.py`、`tests/smart_assistant/tools/test_paratranz_tools.py`，283 通过、1 个真实账户测试按配置跳过。
- 覆盖 V2 权威工程实际保存重开、legacy 原文限定身份、部分下载后继续处理、过期决定、占用候选、真实 Qt 控件及 2,181 项筛选批量保留；中文窗口完成离屏截图检查。
- 全库 Ruff 检查通过；全库格式检查发现其他工作中的 `tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py` 尚需格式化，本轮相关目录格式通过，不改动无关文件。
- 没有进行真实 ParaTranz 联机写入或全库 pytest；没有修改文件格式，不需要数据迁移。
- 完整命令、文件范围与边界见 [S05～S07 增量记录](../../docs/changelogs/paratranz-sync-operation-ux/story-05-to-07/2026-10-20-001-actionable-download-review.md)。

### S08～S10 交付证据（2026-10-20）

- 最终联合 `tests/contracts/paratranz`、`tests/ui/operations`、`tests/paratranz` 与两个助手同步工具文件：461 通过、1 个真实账户测试跳过。
- 新上传合同 30 项、真实 V2 生产往返 12 项、请求决定 3 项；两复核窗口测试文件合计 65 项，均包含于上述联合回归。
- 全库 Ruff 检查通过，本轮相关 81 个文件格式通过。全库格式末次复查受其他任务的 `ui/coordinators/project_creation_operation.py` 待格式化修改影响；本轮不改无关文件。
- 执行器、主同步适配器、主窗口分别为 380、380、467 行；远端执行、事务端口、关联保存、结果和预检已按责任提取。
- 中文窗口已渲染核验；本轮临时文件已清理，没有真实云端写入、数据迁移、安装包构建或 Git 提交。
- 具体文件和完整命令见 [S08～S10 增量记录](../../docs/changelogs/paratranz-sync-operation-ux/story-08-to-10/2026-10-20-001-upload-roundtrip.md)。

## 已确认约束

- 下载默认使用 `PREFER_REMOTE` 更新可靠匹配项，保留本地作为明确次选项；上传默认以本地译文和状态更新已确认目标。
- 项目名称为主要展示信息，ID 用于身份匹配与重名消歧，不能按名称猜测目标。
- 下载默认不删除本地；legacy 删除需独立选择并列出影响，V2 来源条目集合始终保留。
- 已有历史还原点保存包含未保存修改的当前内容；恢复能力不可用时不作承诺，创建失败时停止正式下载。
