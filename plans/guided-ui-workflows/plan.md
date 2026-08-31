# 任务导向 UI 引导与工作流体验实施计划

- **Feature slug**：`guided-ui-workflows`
- **状态**：已完成（2026-08-24，S01～S13 全部终验通过）
- **日期**：2026-08-22
- **对应需求**：[FR26、NFR1.6](../../docs/requirements.md)
- **架构约束**：[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-021](../../docs/adr/021-ui-presentation-modularization.md)
- **前置条件**：FR25 已完成；FR24 基础能力可并行，页面迁移按本计划交互冻结门禁推进

## 目标

把 TransBridge 从按内部模块陈列功能的控制台调整为按用户任务和当前状态组织的桌面工作流。新用户无需先理解 Project/Collection/Variant 即可加载第一个插件；熟练用户可关闭非必要引导，通过紧凑界面和稳定入口高效操作。

P0 核心交付完成后应满足：

1. 无可恢复工程时以统一的“新建本地翻译工程”为入口，并在建项页选择插件或空工程；无 ParaTranz/LLM 配置仍可完成纯本地旅程。
2. 插件驱动建项通过 application 权威事务完成，不由 UI 直接写工程文件，不留下孤儿工程。
3. 页面根据 projection/task 状态提供一个主动作、原因和恢复路径，引导强度可配置。
4. 删除“小工具”产品分类，AI、词典、智能助手和 FOMOD 按真实任务角色归位。
5. Workbench、AI、同步/写回与任务结果保持上下文连续，并满足 FR24/FR25 性能和生命周期预算。
6. 任务中心按实际能力显示操作；恢复、重试、日志和结果不得由展示层伪造。

## 非目标

- 不实现 FR24 的主题引擎、完整主题编辑器、皮肤或全面视觉重绘。
- 不在 View/Presenter 中重写解析、翻译、持久化、ParaTranz、FOMOD 或 TaskRuntime 业务规则。
- 不建立插件市场、动态插件加载或重新恢复“小工具即插件”的产品模型。
- 不用 UI 任务中心替代 ADR-019 的 TaskRuntime，也不承诺所有 legacy worker 天然支持恢复或重试。
- 不一次性改写全部历史文案；只处理关键旅程术语、动作和状态说明。
- P1 的命令搜索、示例体验和完整 accessibility 不阻断 P0 核心里程碑。

## 实施前基线事实（已由本计划替代）

- 启动时 `MainWindow` 会在缺少 ParaTranz token 时先打开配置窗口，然后自动恢复当前工程；本地旅程会被无关配置打断。
- V2 权威模式下旧 `ProjectCoordinator.new_project()` 被禁用，`GuiProjectCommandFacade` 没有 `create_project`；不存在可直接复用的权威建项用例。
- `ProjectLifecycleService` 已有两阶段切换和原子 active pointer，但尚未覆盖“从源文件创建 Project + 默认 Variant + baseline”的事务。
- `TaskRuntime` 是进程内权威状态机且无“把终态改回运行”的 retry API；恢复依赖有效 checkpoint，重试应提交新 Run ID。
- AI、Workbench、ParaTranz 与 FOMOD 仍包含 QThread/ApiWorker 路径；adapter 只能投影真实能力，不能补造 checkpoint、日志或幂等性。
- FR25 已提供 Shell、Workbench、AI、Chat 的公开 ports 和生命周期 owner；FR26 只重组 View/composition 与用户 intent。

## 设计原则

1. **用户任务优先**：先说“开始翻译、检查、写回、同步”，技术模型按需解释。
2. **单页渐进披露**：默认显示当前任务所需信息，高级配置和危险动作后置。
3. **一状态一主动作**：每个空/阻塞状态只有一个权威主动作，备用入口映射同一 intent。
4. **先解释再禁用**：禁用动作必须提供原因、影响与修复入口。
5. **异步不中断上下文**：启动任务后立即进入进度/任务投影，完成后返回原工作对象。
6. **能力型操作**：Task capability、checkpoint、artifact 和 retry factory 决定按钮，不按任务名称猜测。
7. **单一权威状态**：最近工程、保存状态、引导和任务中心都是既有权威状态的 projection。
8. **先冻结交互再迁移视觉**：FR24 可推进 foundation；页面组件布局在对应 FR26 Story 验收后迁移。
9. **P0/P1 真正解耦**：P1 缺失不得让 S10 的核心旅程终验失败。

## 里程碑与 Story 总览

### Milestone A：P0 核心体验

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---:|---|
| S01 | 旅程、术语、线框图与可用性基线 | P0 | FR25 完成 |
| S02 | 权威的插件/空工程原子建项用例 | P0 | S01、ADR-018 |
| S03 | 任务能力矩阵、活动投影、历史与恢复合同 | P0 | S01、ADR-019 |
| S04 | 开始中心、启动策略、最近/恢复与插件建项 UI | P0 | S01～S03 |
| S05 | 状态引导、配置强度与 compact 等价性 | P0 | S01、S04 |
| S06 | Action Catalog、导航重组与“小工具”撤销 | P0 | S01 |
| S07 | Workbench 层级、筛选、上下文动作与保存状态 | P0 | S05、S06 |
| S08 | AI 快速运行、高级配置与任务结果连续性 | P0 | S03、S05、S07 |
| S09 | 上传/下载/写回/FOMOD 操作计划与任务接入 | P0 | S03、S05～S07 |
| S10 | P0 旅程、兼容、性能终验及 FR24 交接 | P0 | S04～S09 |

### Milestone B：P1 发现与可访问性增强

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---:|---|
| S11 | 安全拖放与可选示例体验 | P1 | S04、S07、S09 |
| S12 | 命令搜索与上下文术语帮助 | P1 | S03、S06、S07 |
| S13 | 键盘、焦点、危险操作、无障碍与 P1 终验 | P1 | S05、S08～S12 |

S10 通过即可标记“P0 核心完成”，不等待 S11～S13。全部 P1 通过后才把整个 FR26 标记为完整完成。

## Story-01：旅程、术语、线框图与可用性基线

**目标**：先用证据冻结目标体验，不以主观“更现代”替代可观察结果。

**验收标准**：

- [x] 固定首插件、继续工程、导入译文、AI 翻译并检查、ParaTranz 同步、写回、FOMOD、失败恢复和熟练快捷操作旅程。
- [x] 每条旅程记录入口、用户决策、模态窗口、默认焦点、错误/禁用反馈、任务和返回上下文。
- [x] 输出开始中心、Workbench、AI 快速运行、操作计划和任务中心低保真线框图及组件状态表。
- [x] 术语表区分本地翻译工程、ParaTranz 云端项目、翻译内容/插件、翻译版本和历史还原点；非插件来源不强称为插件。
- [x] 固定 NFR1.6 的 30% 改善目标；不适用旅程逐条记录证据和替代目标。
- [x] 盘点重复/孤立入口、快捷键冲突、危险操作、“小工具”残留和无法解释的禁用状态。

**文件落点**：新增 `journey-inventory.md`、`ux-contract.md`、`wireframes.md`、当前旅程 characterization tests，并扩展性能场景。

**验证**：覆盖全部 P0 旅程；线框图主动作映射稳定 intent；characterization 可复现当前成功、取消、失败和恢复路径。

## Story-02：权威的插件/空工程原子建项用例

**详细文档**：[story-02-authoritative-project-provisioning.md](stories/story-02-authoritative-project-provisioning.md)

**目标**：补齐 V2 从源文件或显式空工程创建 Project 的 application 权威事务，供所有入口复用。

**验收标准**：

- [x] application 接收不可变建项 draft/request，校验工程名、源、迁移输入、format 与 fingerprint。
- [x] 源解析/迁移候选、Project、默认 Variant、baseline 和 active pointer 在 staging/UnitOfWork 边界内协调。
- [x] 全部成功后一次发布 project/variant/projection；失败保持原 active generation 和 repository 可见状态。
- [x] 重名、非法路径、损坏源、解析失败、迁移不兼容、写失败和激活失败均产生稳定诊断且可安全重试。
- [x] 空工程使用同一命令，只显式声明无 source；UI、MCP 或 legacy facade 不直接构造 V2 DTO 并保存。

**文件落点**：新增 application project provisioning contracts/use case；扩展 project ports、persistence V2 staging/UoW、GUI facade/composition；新增 application/persistence/fault tests。

**验证**：正常、空工程、重名、解析/迁移/写入/激活故障注入、重复提交、非 ASCII/长路径；断言无孤儿记录、旧 active 不变且事件只发布一次。

## Story-03：任务能力矩阵、活动投影、历史与恢复合同

**详细文档**：[story-03-task-activity-projection.md](stories/story-03-task-activity-projection.md)

**目标**：在实现任务中心前先定义可证明的能力、历史、恢复、重试和结果导航边界。

**验收标准**：

- [x] 为解析、AI、上传、下载、写回、FOMOD、Smart Assistant 建立 authority/owner/RunSpec/capability/checkpoint/artifact/log/retry/migration 矩阵。
- [x] `TaskActivityViewState` 只读投影 TaskRuntime event/snapshot；legacy adapter 必须有 owner、Run ID、generation、close 和退出条件。
- [x] 终态历史只保存不可变事件摘要/diagnostic/artifact reference，不成为业务终态权威。
- [x] 恢复目录只列出通过 checkpoint identity 校验的任务；不可恢复历史有原因但无继续按钮。
- [x] 重试通过 task-type intent/factory 创建新 Run ID；非幂等任务必须重新预检/确认，不能盲目重放。
- [x] 控制和全局查看遵守 OwnerRef/管理权限；当前用户不可借任务中心越权访问其他 owner 内容。

**文件落点**：新增 application task activity/history 窄合同和 UI task projection；扩展 composition/event projection；新增 capability/history/recovery/retry/authorization tests。

**验证**：能力矩阵、终态不可逆、新 Run ID 重试、checkpoint mismatch、owner 隔离、乱序事件、重启目录和订阅释放。

## Story-04：开始中心、启动策略、最近/恢复与插件建项 UI

**详细文档**：[story-04-start-center-guided-project.md](stories/story-04-start-center-guided-project.md)

**目标**：让用户从“选择插件开始”进入产品，并保持 FR8.4 自动恢复和已有工程安全。

**验收标准**：

- [x] 无可恢复工程、自动恢复失败或用户主动返回时显示开始中心；正常自动恢复继续直接进入 Workbench。
- [x] 缺少 ParaTranz/LLM/Embedding 配置不弹出阻塞本地旅程的启动模态框。
- [x] 主动作选择插件；最近工程和真实可恢复任务来自只读 projection，并解释缺失/不可恢复原因。
- [x] 建项 draft 可建议工程名、选择迁移来源、折叠高级解析项并返回编辑；提交只调用 S02 use case。
- [x] 用户主动返回开始中心不隐式关闭工程、丢弃 dirty 状态或取消后台任务。
- [x] 校验/提交失败保留 draft 和修复入口，不生成孤儿工程。

**文件落点**：新增 shell start center、guided coordinator；修改 MainWindow/app/project coordinator/tool windows；新增 launch/recent/recovery/guided tests。

**验证**：首次启动、正常恢复、恢复失败、无 token、本地写回、返回开始页、dirty、运行中任务、故障和重复提交。

## Story-05：状态引导、配置强度与 compact 等价性

**目标**：用当前业务状态解释下一步，并允许熟练用户降低引导强度。

**验收标准**：

- [x] 无工程、空工程、未翻译、待检查、待发布、缺配置、失败/部分失败都有稳定 GuidanceState、一个主动作和恢复入口。
- [x] `[ui] guidance_mode=auto|guided|compact` 经 ConfigRepository 持久化；无效值回退，写失败不伪称已保存。
- [x] 引导可折叠、关闭和恢复，同一 projection revision 不重复提示或提交 intent。
- [x] compact 只减少说明与留白，不移除完成任务所需能力；guided/compact 业务 command 与结果等价。
- [x] 不使用 polling、窗口树扫描或 View 私有字段；迟到更新不污染新上下文。

**文件落点**：新增 `src/transbridge/ui/guidance/`；修改 UI 偏好 schema/composition；新增 GuidanceState、配置和模式 parity tests。

**验证**：状态矩阵、配置 round-trip/写失败、重复事件、切换/关闭迟到更新和模式 command parity。

## Story-06：Action Catalog、导航重组与“小工具”撤销

**目标**：按用户任务重组入口，并让所有入口映射同一 public intent。

**验收标准**：

- [x] 建立稳定 intent ID、可见性、enabled reason、危险等级和主/备用入口元数据；catalog 不执行业务。
- [x] 顶层导航按文件、项目、翻译、同步与发布、视图、设置、帮助组织，不再显示“小工具”。
- [x] AI、词典、智能助手、FOMOD 按 FR26.5 归位；智能助手只有一个权威菜单状态和一套快捷键。
- [x] 菜单、开始中心和 Workbench 上下文入口对同一 intent 只提交一次 command。（待 S04/S07 集成验证）
- [x] P1 命令搜索不作为本 Story 或 S10 的完成前提。

**文件落点**：修改 shell menu/tool window slices；新增 action catalog；扩展 shell/action contract tests。

**验证**：intent 唯一性、重复点击、快捷键冲突、enabled reason、panel checked 状态和旧入口兼容。

## Story-07：Workbench 层级、筛选、上下文动作与保存状态

**目标**：突出当前工程/翻译内容和表格任务，降低统计与高级筛选的视觉竞争。

**验收标准**：

- [x] 本地工程、翻译版本和当前翻译内容层级明确；有真实插件时显示插件语义，纯 EET/JSON 等来源不伪称插件。
- [x] 统计收敛为可点击摘要；基本/高级筛选保持既有 FilterState、row identity、scroll、selection 和增量渲染语义。
- [x] 标签管理与标签筛选分离；表格附近按上下文提供翻译、检查、写回/发布动作。
- [x] 移除/删除/覆盖进入次级安全入口。
- [x] 保存状态显示保存中、已保存时间、未保存、失败可重试及目标工程/版本；切换/关闭遵守 dirty decision。

**文件落点**：修改 Workbench widget/project bar/step2/filter slices/status presenter；新增 workflow actions view 和 UX/performance tests。

**验证**：10k+ 条目筛选/滚动/编辑、保存成功/失败、切换、危险操作、单 intent 和模式状态。

## Story-08：AI 快速运行、高级配置与任务结果连续性

**详细文档**：[story-08-ai-quick-run-task-continuity.md](stories/story-08-ai-quick-run-task-continuity.md)

**目标**：让日常 AI 运行只关注本次任务，把长期配置后置，并接入真实任务能力和结果导航。

**验收标准**：

- [x] 默认面只显示模式、scope、估算、覆盖策略和主动作；高级配置保留现有 provider/model/embedding/terms/postprocess 能力。
- [x] 长期配置与不可变 RunSpec 分离；运行中配置变化不修改既有 run。
- [x] 缺 API/模型/依赖/目标词条时就地显示原因和修复入口，不启动后才报错。
- [x] 启动后立即进入 S03 任务投影；停止、恢复、失败项重试、日志和报告严格按能力显示。
- [x] translate/polish/mixed 语义、正式提交点、Run ID/generation、checkpoint 和取消行为不变。
- [x] 完成后可定位问题、应用结果、以新 Run ID 重试失败部分或再次运行。

**文件落点**：修改 AI config/view state/presenter/run/result slices；新增 quick run/advanced settings view；按 ADR-019 迁移或封装 worker；扩展 characterization/task/performance tests。

**验证**：三模式、无配置、空 scope、估算、重复启动、取消/恢复、关闭、部分失败、新 Run ID、结果定位和配置隔离。

## Story-09：上传/下载/写回/FOMOD 操作计划与任务接入

**详细文档**：[story-09-operation-plans-task-adapters.md](stories/story-09-operation-plans-task-adapters.md)

**目标**：用可返回编辑的计划替代多层模态链，并把长任务接入 S03 能力合同。

**验收标准**：

- [x] 上传、下载、写回和 FOMOD 共享呈现语义；业务 request 仍由各 use case 拥有。
- [x] 预检在正式副作用前验证凭据/权限、输入、输出路径、锁定/隐藏条目和覆盖风险。
- [x] 最终只有一个提交确认点；返回编辑不丢输入，取消不产生网络或正式文件副作用。
- [x] 部分失败显示成功/失败对象；只重试失败项必须新建 Run ID、重做必要预检并遵守幂等策略。
- [x] legacy adapter 不伪造暂停/恢复；迁移后的 workload 由 TaskRuntime 拥有终态和提交屏障。
- [x] FOMOD 保留 typed pipeline 阶段、归档策略和安全发布语义。

**文件落点**：新增 UI operation plan/preflight slices；修改 Workbench cards、operation coordinator、FOMOD entry；扩展 TaskRuntime adapter 与 tests。

**验证**：正常、权限失败、不可写路径、覆盖、取消、部分失败、新 Run ID 重试、迟到提交和无重复副作用。

## Story-10：P0 旅程、兼容、性能终验及 FR24 交接

**目标**：证明核心体验降低路径成本，同时不破坏业务、性能或 FR25 架构。

**验收标准**：

- [x] 首插件、继续工程、AI 翻译检查、同步/写回、失败恢复和熟练操作全部通过固定旅程。（2026-08-24：[P0 journey evidence](p0-journey-evidence.md)，73 passed）
- [x] NFR1.6 决策/模态目标逐旅程通过或有用户确认的替代目标；无 token 本地旅程通过。（J02～J07：D 下降 60%，M 下降 80%；J01 纠正为 2/1/0）
- [x] 公开 imports、Project/Variant/Collection、TaskRuntime、I/O、ParaTranz 和 FOMOD parity 回归通过。（2026-08-24：Windows 定向兼容组 390 passed）
- [x] 无 polling、窗口树扫描、私有跨访、singleton、重复 command 或 import cycle。（2026-08-24：模块审计无非豁免项，intent/ownership 19 passed）
- [x] Windows P95/heartbeat/RSS/100 生命周期满足 NFR1.4～NFR1.6；10k 表格和高频 task 更新不回退。（2026-08-24：10k/20 样本/100 生命周期固定 comparator 连续两次 `failures=[]`）
- [x] FR24 migration inventory 面向 S07 Workbench、S08 AI 和 S09 operation 的最终组件状态；P1 不阻断 P0 交接。（2026-08-24：三切片交互边界冻结，FR24 视觉迁移仍未开始）

**文件落点**：新增 P0 guided journeys/performance tests；修改 modularization parity；更新 FR24 migration notes；按证据标记“P0 核心完成”。

**验证**：UI/contract/integration/full regression、静态审计、Windows 性能、生命周期、人工冒烟和回退演练。

## Story-11：安全拖放与可选示例体验

**目标**：提供不影响核心交付的低门槛入口。

**验收标准**：

- [x] 拖入插件、`.transbridge`、JSON、EET/XT/SST、Strings 目录和支持的 FOMOD 归档只产生识别结果与候选计划。
- [x] 未知、混合、冲突、超预算、符号链接和不可读输入稳定诊断；确认前零网络/覆盖/发布副作用。
- [x] 本期不交付示例工程；未来若交付，其 fixture 必须可再分发、不联网、不耗 Token、可完全删除并与真实最近工程区分。
- [x] 示例未交付时明确记录为延期，不影响 S10。

**文件落点**：新增 drop router/tests；可选新增示例 fixture、来源说明和 sample tests。

**验证**：支持/未知/混合/危险输入、取消、路由唯一性、归档预算、示例删除和零网络调用。

## Story-12：命令搜索与上下文术语帮助

**目标**：为熟练用户和陌生功能提供可搜索、可解释的备用入口。

**验收标准**：

- [x] 命令搜索消费 S06 Action Catalog，可按用户语言查找功能、最近工程和翻译内容。
- [x] 搜索结果显示不可用原因，危险动作不在搜索中直接无确认执行。
- [x] 帮助解释用途与何时使用，不要求离开当前任务。
- [x] 快捷键与智能助手无冲突；搜索入口只转发稳定 intent，不复制 enabled 规则。

**文件落点**：新增 command palette/help center 及 tests。

**验证**：搜索排序/别名、不可用原因、危险动作、快捷键、最近项失效、单 command 和生命周期。

## Story-13：键盘、焦点、危险操作、无障碍与 P1 终验

**目标**：完成 P1 的可发现性和可访问性增强，并保持 P0 能力稳定。

**验收标准**：

- [x] Enter/Esc、默认焦点、搜索、表格编辑、返回和面板切换一致；Esc 不停止后台任务。
- [x] 滚轮防误触、重复点击幂等、窗口置前/owner 修复保持有效。
- [x] 危险操作明确对象、范围和恢复方式；可撤销/软删除优先。
- [x] 关键控件有可访问名称、可见焦点和非纯颜色状态，与 FR24 accessibility contract 兼容。
- [x] S11/S12 启用或禁用均不改变 P0 业务结果和性能门禁；全部 P1 通过后才标记 FR26 完整完成。

**文件落点**：修改相关 views；按需扩展 focus/input/windowing 合同；新增键盘、焦点、危险操作与 accessibility tests。

**验证**：键盘旅程、焦点顺序、Esc/Enter、误触、撤销/确认、屏幕阅读属性和完整 P1 性能/生命周期冒烟。

## 依赖顺序与交付门禁

```text
S01 ─┬─> S02 ─┐
     ├─> S03 ─┼─> S04 -> S05 ─┐
     └────────┴────> S06 ─────┼─> S07 -> S08 ─┐
                              └──────> S09 ───┼─> S10 (P0 complete)

S04/S07/S09 -> S11 ─┐
S03/S06/S07 -> S12 ─┼─> S13 (FR26 complete)
S05/S08/S09 ─────────┘
```

- S01 未冻结前不调整菜单或页面层级。
- S02 完成前，S04 不得用 legacy `ProjectHandle.create()` 拼接建项。
- S03 完成前，不得承诺未证明的恢复、重试或日志能力。
- S06 后菜单、上下文和命令搜索才共用 intent。
- S07 验收后 FR24 S06 才可锁定 MainWindow/Workbench 布局；S08/S09 后 FR24 S07 才可锁定工具布局。
- S10 不依赖 S11～S13；P1 不能改变已冻结的 P0 intent 或业务结果。

## 风险与缓解

- **权威建项扩展 application 范围**：单独 S02，遵守 ADR-018 staging/UoW；UI 只提交 request。
- **任务中心成为第二状态机**：S03 先定义能力矩阵和只读历史；控制仍走 TaskRuntime/use case。
- **legacy worker 永久化**：adapter 必须记录 owner、能力缺口、迁移 Story 和退出条件；禁止新增旧 API。
- **重试重复副作用**：新 Run ID、不可变 RunSpec、预检和幂等 checkpoint/commit guard 缺一不可。
- **过度引导**：默认 `auto`，页面内可折叠；compact 保留全部能力。
- **术语失真**：“翻译内容”作为 source-agnostic 后备词，只有真实插件才称插件。
- **FR24/FR26 返工**：基础服务并行，页面迁移按 S07/S08/S09 精确门禁。
- **范围过大**：S10 独立封闭 P0；拖放、示例、命令搜索和完整 accessibility 在 P1。

## 回退策略

- 每个切片保留原 public intent/coordinator；新 View/composition 可逐 feature flag 回退。
- S02 失败只清理 staging，原 active project/workspace/projection 保持不变。
- S03/S08/S09 迁移期间原进度/报告 facade 保留为只读兼容入口；不得双重提交终态或结果。
- 菜单迁移期间只能有一个 action owner；旧入口只转发同一 intent。
- FR24 若已实现 foundation，回退只调整 adapter/View，不把旧信息架构固化为主题合同。

## 明确假设与未决项

- 默认保留 FR8.4 自动恢复；开始中心在无恢复目标、恢复失败或用户主动返回时出现。
- 工程名由首插件文件名建议；创建空工程为高级入口并走同一 S02 use case。
- `guidance_mode` 暂定 `auto|guided|compact`，具体 schema version 在 S05 对齐 ConfigRepository。
- FOMOD 暂作为开始中心/“同步与发布”专项能力；S01 可依据使用频率调整入口层级。
- 全局命令搜索可能使用 `Ctrl+K`；最终组合由 S12 冲突盘点确定。
- 首期不采集云端遥测；旅程证据来自固定自动化、人工冒烟和用户确认。
