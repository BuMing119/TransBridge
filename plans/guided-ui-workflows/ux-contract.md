# S01 UX 合同

本合同冻结 FR26 P0 的用户语言、intent、焦点、取消、错误和连续性规则。它约束 S02～S10 的呈现与接线，但不改变 application/domain 权威边界。

## 1. 用户术语

| 用户术语 | 含义 | 可显示内部词的场景 | 禁止误用 |
|---|---|---|---|
| 本地翻译工程 | 本机保存的一组翻译工作、版本和来源 | 设置、诊断、导入导出中可补充 `Project` | 不简称为 ParaTranz 项目 |
| ParaTranz 云端项目 | ParaTranz 服务中的远端协作项目 | 同步计划、账户与权限页 | 不用“当前项目”含混指代 |
| 插件 | 真实 ESP/ESM/ESL 来源 | 解析、写回、插件上下文 | JSON/EET/XT/SST 或无插件集合不得强称插件 |
| 翻译内容 | source-agnostic 的当前可编辑词条集合 | 来源不是插件、来源类型不确定、多个来源聚合时 | 用户主界面不显示 `CollectionSlot` |
| 翻译版本 | 同一本地工程内可切换的译文状态 | 版本切换、复制、保存 | 不称为快照或历史还原点 |
| 历史还原点 | 某翻译版本可恢复的只读历史点 | 历史与恢复页面 | 不与可继续编辑的翻译版本混用 |
| 任务 | 有 Run ID、owner 和可观察终态的一次后台执行 | 任务中心、日志、诊断 | 普通页面加载不得伪装成可恢复任务 |

普通界面先显示用户术语；诊断可附带内部 identity，但不得只显示 `project/collection/variant/snapshot`。

## 2. 稳定 intent 词汇

后续 Action Catalog 可以扩展元数据，但不得改变下列 P0 intent 的业务含义：

| Intent ID | 用户动作 | 权威 command owner | 允许的备用入口 |
|---|---|---|---|
| `project.create_from_source` | 选择插件并创建本地工程 | S02 application provisioning use case | 开始中心、Workbench 空状态、文件菜单 |
| `project.create_empty` | 创建空工程 | 同一 S02 use case，显式无 source | 开始中心高级入口、文件菜单 |
| `project.open` | 打开已有本地工程 | current-project application facade | 开始中心、最近工程、文件菜单 |
| `project.return_to_start` | 返回开始中心但不关闭工程 | shell display-context owner | Workbench 工程菜单 |
| `translation.import_source` | 为当前翻译内容导入已有译文 | parse/migration application port | Workbench 上下文、文件菜单 |
| `translation.ai.run` | 创建 AI 翻译 RunSpec 并运行 | AI run application/TaskRuntime adapter | Workbench 主动作、翻译菜单 |
| `sync.paratranz.upload` | 上传当前范围 | ParaTranz upload use case | Workbench 上下文、同步与发布菜单 |
| `sync.paratranz.download` | 下载并合并 | ParaTranz download use case | Workbench 上下文、同步与发布菜单 |
| `publish.write` | 写回插件/受支持输出 | writer use case | Workbench 上下文、同步与发布菜单 |
| `publish.fomod` | 构建 FOMOD 产物 | typed FOMOD pipeline | 开始中心专项任务、同步与发布菜单 |
| `task.open_activity` | 查看任务活动或结果 | S03 read-only activity projection | Shell 任务入口、运行/结果卡片 |
| `task.cancel` | 请求取消一次真实支持取消的 run | TaskRuntime/workload owner | 任务卡、运行页 |
| `task.resume` | 恢复有效 checkpoint | TaskRuntime/workload owner | 恢复目录、任务卡 |
| `task.retry` | 以新 Run ID 重试 | typed retry factory | 失败结果、任务卡 |
| `shell.toggle_assistant` | 显示/隐藏智能助手 | shell ToolWindows owner | 视图菜单、一个快捷键 |

任何 QAction、按钮、空状态链接或命令搜索结果都只能提交一次 intent。备用入口不得复制 enabled 规则、构造业务 request 或直接执行副作用。

## 3. 页面状态合同

每个页面状态必须提供以下字段：

- `context_identity`：本地工程、翻译版本、翻译内容和可选 Run ID。
- `revision/generation`：用于拒绝迟到投影。
- `headline`：用户语言描述当前状态。
- `reason`：空、阻塞或失败的原因；ready 状态可为空。
- `primary_intent`：恰好零个或一个；为零时必须说明为何没有可执行动作。
- `secondary_intents`：不会与主动作竞争的恢复/高级动作。
- `enabled_reason`：动作不可用时可见；不得只显示灰色。
- `return_context`：成功、取消、失败后要恢复的页面、对象、筛选和选择。

同一 projection revision 内，重复点击、重复信号或迟到事件不得提交第二次 command。

## 4. 主动作与渐进披露

- 开始中心无可恢复工程时，统一以“新建本地翻译工程”进入建项页，再选择插件或空工程。最近工程、恢复项和 FOMOD 是次级入口。
- Workbench 主动作由业务状态决定：导入已有译文、开始翻译、检查问题或写回/发布，同一时刻只突出一个。
- AI 快速运行默认只显示模式、作用域、估算、覆盖策略和开始动作；provider、模型、Embedding、术语和后处理进入高级配置。
- 上传、下载、写回和 FOMOD 共享“编辑计划 → 预检 → 提交 → 任务/结果”语义；正式副作用前只有一个提交点。
- `guided`、`auto`、`compact` 的说明密度可以不同，但 stable intent、enabled 条件、结果和错误分类必须等价。

## 5. 焦点与键盘

- 页面出现时，焦点落在不会立即产生副作用的主任务控件；开始中心是“选择插件”，操作计划是第一个无效/必填字段，否则是“检查并提交”。
- 阻塞后焦点移到原因或第一个修复字段；错误摘要必须可被键盘到达。
- 任务开始后不把焦点留在已销毁窗口；任务投影或进度视图应置前一次，但不得反复抢焦点。
- `Enter` 只触发当前页面已启用的主动作；多行编辑和表格编辑保持原生语义。
- `Esc` 关闭次级浮层或返回编辑，不停止后台任务；取消任务必须使用显式 `task.cancel`。
- 快捷键只有一个 owner。S12 冻结命令搜索前，`Ctrl+K` 不视为可长期保留的助手合同。

## 6. 取消、返回与错误恢复

- 选择来源或编辑计划时取消：零网络、零正式文件写入、零 repository 可见状态变化。
- S02 建项失败：保留可编辑 draft，清理 staging，原 active project/generation 不变。
- 从 Workbench 返回开始中心：只改变 shell display context，不关闭工程、不丢 dirty、不取消任务。
- 操作计划返回编辑：保留所有有效输入和预检诊断；修改影响 identity 的字段后使旧预检失效。
- 任务失败：显示稳定 diagnostic、已成功/失败对象和真实可用的下一步。没有 checkpoint/retry factory 时不得显示恢复/重试按钮。
- retry：创建新 Run ID；远端上传、下载合并和正式文件发布必须重新预检，不能盲目重放旧副作用。
- 结果导航：先验证 owner/project/version/generation；对象已失效时说明原因并提供打开所属工程，而不是导航到错误上下文。

## 7. 保存与数据状态

Workbench 必须持续显示：

- `正在保存`：包含目标工程/翻译版本；重复保存请求被合并。
- `已保存 · 时间`：明确最近成功提交时间和目标。
- `有未保存修改`：不是仅在按钮上附加 `*`。
- `保存失败`：保留 dirty，显示诊断和 `project.save` 重试入口。

关闭、工程/版本切换必须消费 application dirty decision；View 不直接清除 dirty，也不凭按钮颜色推断保存成功。

## 8. 任务能力与结果连续性

- 活动列表是 TaskRuntime/受控 legacy adapter 的只读投影，不拥有业务终态。
- 控件按 capability 显示：`cancel`、`pause`、`resume`、`retry`、`log`、`open_artifact`、`locate_result`；不按任务名称猜测。
- 任务卡至少显示用户任务名、所属工程/翻译内容、阶段、进度、耗时和终态。
- 历史摘要不可把终态改回 running；有效 checkpoint 必须校验 JobSpec/input/owner/schema identity。
- AI、ParaTranz、写回、解析和 FOMOD 完成后都能回到发起时 `return_context`，并保留可验证的 artifact/diagnostic reference。

## 9. 性能和生命周期门禁

- 禁止新增 UI polling timer、周期性窗口树扫描、每事件动态反射、重复 projection 或重复 command。
- 旅程 projection 使用事件/订阅并具有显式 `close/dispose`；owner/revision/generation 变化后忽略迟到更新。
- guided/compact 不重复构造业务状态，不因说明文字模式增加网络或后台任务。
- S10 在固定 Windows 窗口树上验证 NFR1.4/NFR1.5：heartbeat ≤ 200 ms，普通窗口打开 P95 回归 ≤ 5% 或 10 ms，100 次生命周期无订阅/timer/worker 泄漏。
- S01 只冻结场景名称和采集字段；性能基准实现由集成者在允许修改 `tests/performance/benchmark_cases.py` 的 S10/集成切片完成。

## 10. 可验收性

每个 P0 旅程测试/人工证据至少记录：fixture identity、入口 intent、D/M/N、默认焦点、取消点、失败 diagnostic、Run ID（如有）、返回上下文和产物摘要。改变本合同必须在对应 Story 中说明被替代条款，不能仅改变按钮文案后宣称通过。
