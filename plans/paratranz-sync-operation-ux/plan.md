# ParaTranz 上传/下载操作页重设计实施计划

- **Feature slug**：`paratranz-sync-operation-ux`
- **状态**：草稿
- **日期**：2026-08-30
- **对应需求**：[FR3.3、FR22.3～FR22.4、FR22.6～FR22.8、FR26.8、NFR1.6](../../docs/requirements.md)
- **架构约束**：[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-021](../../docs/adr/021-ui-presentation-modularization.md)、[ADR-023](../../docs/adr/023-local-project-paratranz-binding.md)
- **承接计划**：[guided-ui-workflows](../guided-ui-workflows/plan.md)、[paratranz-project-binding](../paratranz-project-binding/plan.md)、[paratranz-sync-service-v2](../paratranz-sync-service-v2/plan.md)

## 目标

把当前暴露内部 ID、枚举值和手动预检步骤的通用操作计划窗口，改成用户能直接理解的 ParaTranz 上传/下载任务页。下载场景以“从选定云端项目更新当前本地翻译”为默认意图；内容不同是预计更新，而不是默认阻断原因。

完成后应满足：

1. 用户以项目名称识别 ParaTranz 目标，不需要知道或手填项目 ID；ID 只在同名消歧或技术信息中显示。
2. `project_binding`、`true/false`、`abort` 等内部值不进入普通界面。
3. 页面打开和选项变化时自动后台预检；预检通过后只保留一个正式主动作，用户无需先点“运行预检”再点“确认并开始”。
4. 下载默认采用远端内容更新同键本地条目；普通内容差异映射为预计更新数，只有重复键、重复远端引用、权限/身份异常等真正无法安全执行的问题才阻断。
5. 覆盖、删除、跳过和新增影响在最终确认前可见；正式执行继续使用不可变计划、一次性确认、TaskRuntime 和原子本地提交。
6. 任何“历史还原点/备份”文案必须对应真实创建成功的恢复产物，不再用内存快照冒充可回滚备份。

## 非目标

- 不重写 ParaTranz HTTP 客户端、离线 JSON 转换或 EntryKey/ExternalEntryRef 身份模型。
- 不取消执行前的权限、目标修订、远端快照和计划新鲜度校验；只取消用户手动触发预检的额外步骤。
- 不在本轮重设计写回和 FOMOD 页面；它们继续使用现有通用计划窗口，后续可复用本轮的自动预检状态机。
- 不通过项目名称猜测或自动改绑目标；选择器仍以远端项目 ID 作为内部稳定身份。
- 不把 ParaTranz 管理页最后浏览项目恢复为同步目标来源。

## 当前实现事实与根因

- `ui/operations/production.py` 创建下载计划时把目标渲染为 `ParaTranz 项目 #ID（project_binding）`，并生成两个文本字段：项目 ID 和 `true/false`。
- `ui/operations/plan_dialog.py` 只能把所有可编辑项渲染成 `QLineEdit`，因此布尔值、选择器和策略都退化为技术文本输入。
- `ui/operations/coordinator.py` 要求用户手动“运行预检”，预检通过后再“确认并开始”；编辑态、预检态和提交态同时堆在一个页脚中。
- `ui/operations/production.py` 在创建同步计划时硬编码 `ConflictPolicy.ABORT`。因此下载中同键内容不同会变成 `CONFLICT`，而不是已有 planner 支持的 `UPDATE_LOCAL`，造成大批“未解决冲突”阻断。
- `ui/workbench/remote_target_view.py` 已经有异步项目目录和按名称搜索的选择器，但其文案和提交动作被绑定场景写死，操作计划没有复用。
- `ui/operations/production.py` 已有 556 行，超过仓库 500 行责任审查阈值；本功能不得继续往该模块堆入 ParaTranz 专属 View/状态机，应抽取完整同步切片。

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
│ 预计变化（自动检查）                                             │
│ 更新 8,096  新增 204  保留 0  删除 0  需处理 0                  │
│ [查看变更明细]                                                   │
│                                                                  │
│                                             [取消] [下载并更新本地]│
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
- 重复本地键、重复远端键、重复远端 ID、目标/账号/端点变化等仍是结构性阻断，不得被“远端优先”强行覆盖。

### 自动预检与一次确认

```text
打开页面/更换项目/修改策略
          ↓
自动失效旧计划并后台预检
          ↓
   检查中 ──→ 阻断：显示原因和唯一修复动作
          ↓
显示预计新增/更新/删除/保留/需处理数量
          ↓
用户点击一次“下载并更新本地”
          ↓
创建真实历史还原点 → 消费一次性确认 → 提交 TaskRuntime
```

- 页面没有“运行预检”和“返回编辑”；选项始终可编辑，任何改动都会自动刷新计划。
- 预检中主按钮显示“正在检查…”并禁用；通过后变为“下载并更新本地”，无变化时显示“本地已是最新”。
- “下载并更新本地”是唯一正式确认点，按钮附近展示目标名称、更新数和删除数，满足覆盖性计划显式确认要求。
- 用户编辑导致旧预检过期属于正常状态：取消或忽略旧 generation，不弹出 `operation plan changed while preflight was running`。
- 提交瞬间远端或本地再次变化时，显示“内容刚刚发生变化，正在重新检查”，自动刷新一次；持续变化才保留可操作错误。
- 取消预检只释放本页 worker/service/cache，不产生远端写入、本地提交或绑定变更。

### 状态与文案

- `尚未预检` → `正在检查项目权限和远端内容…`
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
- [ ] 普通内容差异在下载远端优先模式下产生 `UPDATE_LOCAL`；结构性身份异常仍产生 `CONFLICT` 并阻断。
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

## Story-03：自动预检与单次确认状态机

**目标**：保留后端两阶段安全合同，但把手动“预检 → 再确认”收敛成自动预检和一次用户确认。

**验收标准**：

- [ ] 页面首次具备有效目标时自动发起一次预检；项目/策略/删除选项变化后防抖刷新。
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
2. 打开页面和字段变化时生成新 revision，取消/忽略旧 generation，自动运行只读 preflight。
3. ready 后缓存与当前 digest 匹配的 token 和 typed impact；View 不接触 token 内容。
4. 用户点击主动作时再次核对 revision，消费 token 并提交；stale 时刷新而不是执行。
5. 把 `PREFLIGHT_STALE`、identity mismatch、成员/认证/网络错误映射为用户语言和修复 intent。

**测试策略**：

- 扩展 `tests/ui/operations/test_operation_plan_coordinator_async.py`，覆盖打开即预检、快速改选、迟到结果、取消、重复点击、窗口销毁和 worker 清理。
- 扩展 `test_operation_plan_presenter.py`，覆盖一次性 token、revision/digest 变化和 stale 安全失败。
- 增加可观察旅程断言：从页面 ready 到任务开始只有一次用户点击，不出现额外确认模态框。

## Story-04：恢复保护、结果文案与端到端验收

**目标**：让“可恢复”和“原子合并”成为真实能力，并验证 8,000+ 条目的实际下载旅程。

**验收标准**：

- [ ] 会覆盖或删除本地内容的下载在提交 TaskRuntime 前创建包含本次计划输入的真实历史还原点；创建失败时不开始下载。
- [ ] 还原点名称包含任务类型、时间和短 Run/plan identity，结果页可定位该还原点；不包含凭据或远端敏感数据。
- [ ] 只新增且无破坏性影响的计划可按明确规则跳过额外还原点，但界面不得声称已创建。
- [ ] 原子 UoW 失败不留下半合并集合；成功、部分失败、取消和 stale 均显示用户语言计数及下一步。
- [ ] 8,300 条本地内容、8,096 条远端更新的 fixture 下，自动预检显示更新而非冲突，单次确认后产生一个下载 Run。
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

## 依赖顺序

1. S01 先修正 request/policy/impact 的事实来源。
2. S02 可在 S01 Qt-free state 冻结后实现项目选择和页面布局。
3. S03 依赖 S01 的 digest 和 S02 的 typed intent，完成自动预检与一次确认。
4. S04 最后接入真实恢复保护和完整旅程；未通过前不得在界面显示“已创建还原点”。

## 兼容、迁移与回退

- Agent/MCP 继续接受显式 `project_id`；GUI 不手填 ID 只是展示层变化。
- 旧 Project binding 已保存 `project_name`，无需数据迁移；名称为空的旧数据在重新验证目录后补足展示名，不按名称猜目标。
- 现有 `OperationPlanPresenter`、confirmation authority、SyncPlan hash、TaskRuntime 和 executor 保留；新页面是专用 adapter，不建立第二套业务权威。
- 可按 composition factory 回退 ParaTranz 专用 dialog 到旧通用 dialog，但不得回退到硬编码 `ABORT`、最后浏览项目或静默跳过确认。
- 若真实历史还原点暂不可可靠实现，界面必须移除相应承诺并明确“失败前原子保护，不提供成功后的撤销”，不能保留误导文案。

## 风险与控制

- **项目重名**：名称优先但 ID 保留为次要消歧信息；内部身份始终使用 ID。
- **自动预检造成重复网络请求**：只在 identity 变化时触发，使用防抖、generation、取消和有界目录/计划缓存，不引入 polling。
- **远端优先覆盖错误对象**：request digest 冻结目标、范围、策略和删除选择；结构性身份冲突继续 fail closed。
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

## 明确假设与未决问题

- 假设下载的主流意图是“远端为准更新本地”，因此 `PREFER_REMOTE` 为默认；保留本地作为明确次选项。
- 假设远端项目名称足以作为主要用户识别信息；ID 仅用于重名消歧和技术支持。
- 实现前需确认远端 tombstone 在产品语义上是否等于“删除本地”。在确认前按安全默认设计为不删除，并单独展示/选择删除影响。
- 实现前需验证现有 Version/Project snapshot 能否精确保存本次 plan 使用的未保存本地状态；若不能，先补齐恢复 port，再展示“自动创建历史还原点”。
