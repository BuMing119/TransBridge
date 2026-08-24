# S03 当前任务能力与迁移退出清单

- **Feature**：`guided-ui-workflows`
- **Story**：[S03 任务能力矩阵、活动投影、历史与恢复合同](stories/story-03-task-activity-projection.md)
- **架构约束**：[ADR-019 Unified Task Runtime](../../docs/adr/019-unified-task-runtime.md)
- **需求约束**：[FR26.9](../../docs/requirements.md)
- **证据日期**：2026-08-24
- **状态**：当前实现盘点；不是目标能力承诺

## 1. 判定规则

本清单按用户入口的端到端证据判定，不因某个底层类存在同名方法就宣布能力可用：

- **支持**：当前入口具有稳定身份、真实执行语义和可安全调用的控制/证据；可以通过受 owner 校验的 application port 投影。
- **入口不支持**：底层可能具备部分结构，但当前入口没有接线、身份或生命周期证据；S03 必须显示为 unavailable。
- **不支持**：没有代码证据，或现有实现与 ADR-019 身份、终态、恢复/幂等要求不兼容。
- **checkpoint** 只有同时校验 Run ID、JobSpec digest、input fingerprint、owner 和 schema 才算可恢复。旧进度文件或候选 checkpoint 不自动等于任务恢复能力。
- **retry** 只有 feature-owned factory 重新预检并提交新 Run ID 才算支持。LLM 内部重试、用户重新点按钮或把终态改回 running 均不算。
- **artifact/log** 只有稳定、安全、可受 owner 控制的引用才可进入任务中心。完成消息中的裸路径或进程日志不自动算统一能力。

S03 已提供默认拒绝的 `UnsupportedTaskActivityEvidence`、`TaskRetryIntentRegistry` 和 `RecoveryExpectationRegistry`，但当前 production composition 没有为下列 workload 注册 feature evidence、retry intent 或 recovery descriptor。因此除表中明确说明外，任务中心必须按“不支持”呈现。

## 2. 身份、权威与执行后端

| Workload | 当前终态权威 / backend | 当前 owner | RunSpec / Run ID 证据 | 结论 |
|---|---|---|---|---|
| 解析（单插件、EET、批量、迁移） | `ParseCoordinator` 组织一个或多个 `ApiWorker(QThread)`；worker 的 `result/error/finished` 回调决定 UI 终态并直接更新 `AppContext`/slot | MainWindow + ParseCoordinator；无 `OwnerRef` | `ParseConfig` 是可变 UI DTO；无 `JobSpec`、全局 Run ID、input fingerprint 或 generation | 入口不支持统一任务身份 |
| AI translate（单插件） | `_TranslationWorker(QThread)` + `AutoTranslator`；专用进度窗口拥有控制与报告呈现 | `RunController.owner_id` 只属于 AI 窗口；不含 project/variant/session scope | `TranslationRunRequest` 冻结 config/entries，但 `run_id` 是窗口内递增整数；`AutoTranslator` 另生成字符串 run_id，二者不是 `JobRef` | 有局部身份，不能作为 TaskRuntime 身份 |
| AI polish | `_PolishWorker(QThread)`；`RunController`/模态进度窗口守护迟到回调 | AI 窗口 owner；不含 project/variant/session | 与 translate 共用窗口内 `TranslationRunRequest`；无不可变 application RunSpec/JobSpec | 入口不支持统一任务身份 |
| AI mixed | `_MixedWorker(QThread)`，可再创建两个 daemon `threading.Thread`；RunController 只守护外层信号 | AI 窗口 owner；不含 project/variant/session | 窗口内整数 run_id；config/entries 只存在于 worker 闭包，无 input fingerprint | 入口不支持统一任务身份 |
| AI batch | `_BatchTranslationWorker(QThread)` 串行处理多个 slot；专用批量进度窗口决定展示终态 | 批量窗口；没有稳定 owner scope | 无外层 Run ID/JobSpec；每个插件可能产生独立 legacy `ProgressCheckpoint.run_id` | 入口不支持统一任务身份 |
| ParaTranz 上传 | `UploadCard` + `OperationCoordinator.run_worker()`；冲突检测和正式上传可由两个独立 `ApiWorker` 完成 | MainWindow/UploadCard；无 `OwnerRef` | dialog 字段和 closure 形成临时请求；没有一体化 plan identity、Run ID、input/config fingerprint | 入口不支持统一任务身份 |
| ParaTranz 下载 | `DownloadCard` + `ApiWorker`；下载器直接合并当前 collection | MainWindow/DownloadCard；无 `OwnerRef` | 远端 file IDs 只在 closure；无 Run ID、输入 revision/fingerprint 或 immutable JobSpec | 入口不支持统一任务身份 |
| 写回 | `WriteCard` + `ApiWorker`；Writer/导出函数生成正式文件，回调显示结果 | MainWindow/WriteCard；无 `OwnerRef` | target/path/version 由 dialog/closure 持有；无 Run ID、source snapshot digest 或 JobSpec | 入口不支持统一任务身份 |
| FOMOD | GUI `_PipelineWorker(QThread)` 调用兼容 `FomodPipeline`；内部实际构造 immutable `FomodRunSpec` 并委托 typed `PipelineEngine` | typed pipeline 内使用 run_id；GUI 没有 project/session `OwnerRef` | **底层支持** immutable `FomodRunSpec`、UUID、输入归档 hash、config hash、stage/artifact；GUI 未使用 `FomodPipelineWorkload`/TaskRuntime | 底层身份充分，当前 GUI 入口仍不支持统一任务身份 |
| Smart Assistant 长任务 | `TaskManager` compatibility facade 已绑定 composition `TaskRuntime`；tool translate/polish/postprocess 由独立 `threading.Thread` 执行并通过 facade 提交终态 | 调用方没有传 owner 字段时固定为 `legacy-task-manager` / `legacy`，无法区分 project/session | **存在 TaskRuntime JobRef**，但 JobSpec 固定为 `legacy-task`、`legacy:unspecified` input/fingerprint；真实类型只在 metadata `type` | 有统一终态骨架，但身份不足以跨 owner、恢复或安全重试 |
| Smart Assistant chat/graph | Chat 使用独立 `AsyncWorker`；GraphExecutor 有本地控制与 V2 checkpoint；`GraphWorkloadAdapter` 目前只在 tests/公共模块出现，production 无提交调用 | ChatWidget/ConversationOrchestrator 或 GraphExecutor 本地 context | 显式 checkpoint identity 时 Graph 可满足 owner/spec/input；普通 production chat/graph 未形成 TaskRuntime JobRef | 当前入口不支持全局任务投影 |

证据入口：[ParseCoordinator](../../src/transbridge/ui/coordinators/parse_coordinator.py)、[ApiWorker](../../src/transbridge/ui/workers.py)、[AI RunController](../../src/transbridge/ui/tools/ai_translator/run_controller.py)、[OperationCoordinator](../../src/transbridge/ui/coordinators/operation_coordinator.py)、[FOMOD compatibility pipeline](../../src/transbridge/fomod/pipeline.py)、[TaskManager](../../src/transbridge/smart_assistant/tools/task_manager.py)。

## 3. 控制能力矩阵

这里的“支持”表示当前入口真的把控制信号传入 workload 安全点；仅有按钮或 Event 字段不算。

| Workload | progress | cancel / stop | pause / resume | shutdown / close | 当前可投影动作 |
|---|---|---|---|---|---|
| 解析 | 支持：Step2 当前/总数/消息；单解析多为不定进度 | 不支持：`ApiWorker` 无 cancellation token；关闭窗口不能保证阻止 slot 更新 | 不支持 | 不支持任务级 bounded shutdown；窗口持有 worker 引用 | 仅查看进度；无控制按钮 |
| AI translate | 支持：条目、成功/失败、round/log signal | 支持局部 stop：worker 设置 stop event，AutoTranslator 在请求和 batch 安全点检查；不等于 TaskRuntime cancel barrier | 支持局部协作 pause/resume；AutoTranslator 可取消在途 LLM 并等待继续 | 进度窗口关闭可选择后台继续或停止；无 AppRuntime shutdown 接线 | 迁移 adapter 可暂投影 stop/pause/resume，但必须绑定唯一 owner/run/generation |
| AI polish | 支持：逐条进度 | 支持局部 stop：逐条之间检查 | 支持局部 pause/resume：逐条之间检查 | 关闭模态会调用 RunController.cancel；无统一 shutdown | 迁移 adapter 可暂投影 stop/pause/resume |
| AI mixed | 支持：阶段级 aggregate | 支持局部 cancel flag；parallel 子线程不会主动取消正在执行的单次 polish 调用 | 不支持 | RunController 可取消外层；daemon 子线程没有 bounded join/TaskRuntime shutdown | 仅 stop；不得显示 pause/resume |
| AI batch | 支持：插件和条目级 | 支持局部 stop：当前/后续插件检查 stop event | 支持局部协作 pause/resume | 窗口可后台继续或停止；无统一 shutdown | 迁移 adapter 可暂投影 stop/pause/resume |
| 上传 | 支持：批量/远端回调进度 | 不支持：ApiWorker 和上传器入口没有统一 cancellation token；冲突检测与正式上传之间只能取消 dialog | 不支持 | 不支持 | 仅查看进度；正式上传开始后无控制 |
| 下载 | 支持：批量/远端回调进度 | 不支持：无 cancellation token/commit guard；下载器可直接修改 collection | 不支持 | 不支持 | 仅查看进度 |
| 写回 | 仅不定进度或批量摘要 | 不支持：正式文件 worker 无 cancellation token；不能安全宣称停止 | 不支持 | 不支持 | 仅查看进度 |
| FOMOD | typed pipeline 支持 stage events；当前 GUI 只显示无限进度条 | **底层支持 cancellation**，但 GUI `_PipelineWorker` 没有 stop event/cancel API，因此当前入口不支持 | 不支持 | 不支持；关闭 dialog 不等于停止 worker | 当前 GUI 仅查看 running/result；不得显示取消 |
| Smart Assistant tool tasks | TaskManager 可投影 progress/terminal | **不支持统一 cancel**：translate/polish/postprocess 有局部 stop event，但 compatibility facade 在 worker 确认安全停止前就提交 cancelled；polish 还可能在单次 LLM 返回后继续写 collection | **不支持**：TaskManager 一律声明 pause/resume，但 translate、polish、postprocess 调用方没有消费 handle pause event；当前按钮是伪能力 | `TaskManager.cleanup_all()` 有局部清理，但独立线程和外部请求不形成完整 AppRuntime shutdown 证明 | 全局控制全部隐藏；原 Assistant 可保留“停止请求”，但不得把它投影成已证明的 TaskRuntime cancel |
| Smart Assistant chat/graph | 各自有局部进度/回调 | Chat/Graph 有本地 cancel | Graph 本地支持 pause/resume；Chat 不支持 | owner 本地 close/cancel；未接全局 runtime | 全局任务中心全部不支持；原局部 UI 可保留 |

风险证据：`TaskManager.register()` 当前无条件设置 `supports_pause=True` 和 `supports_resume=True`，而 production 注册调用仅传 `type/mode/phases` 等 metadata。S03 activity projection 不得直接信任这些 legacy capability；退出前必须把 capability 变成各 workload 显式输入，并以 workload 消费 pause token 的测试证明。

## 4. Checkpoint、恢复、证据与重试矩阵

| Workload | checkpoint / 重启恢复 | artifact / 结果导航 | per-run log | retry | S03 安全结论 |
|---|---|---|---|---|---|
| 解析 | 不支持；无 checkpoint | 不支持统一 artifact；成功只产生内存 collection/slot，失败不保留可恢复 draft | 不支持 | 不支持；只能重新打开配置并重新解析 | `recover/retry/log/open_result=false` |
| AI translate | 存在 legacy `ProgressCheckpoint`，保存 esp stem、target IDs、overwrite、completed fingerprints、统计和 AutoTranslator run_id；**缺 owner、JobSpec digest、input fingerprint、schema、原子端口校验，因此不支持 S03 恢复** | 支持局部 Excel report path 和双击定位 entry；没有 owner 校验的 `TaskArtifactRef/TaskResultNavigator` | 支持局部 stream log 目录和 UI log signal；无安全 log ref | 不支持用户级 retry factory；AutoTranslator 内部分批重试不算 task retry | 迁移前只可由原 AI 窗口显示 report/log；全局动作均 false，stop/pause/resume 除外 |
| AI polish | 不支持 checkpoint/重启恢复 | 支持局部 polish Excel report 和 entry 定位；无统一 artifact ref | 不支持 per-run log viewer | 不支持 | 全局 `recover/log/retry/open_result=false`；adapter 可在本窗口展示完成结果 |
| AI mixed | 停止时 AutoTranslator 可能留下 legacy translate progress，但 mixed RunSpec、polish 阶段和并行 frontier 未保存；不支持恢复 | 只显示汇总消息；无稳定报告/artifact/navigation | 不支持 | 不支持 | 除 progress/stop 外全部 false |
| AI batch | 每个插件各自使用 legacy ProgressCheckpoint；没有批量 run identity/frontier，不能证明整批恢复 | 每插件可生成 Excel report，并可定位 entry；没有批量 artifact port/owner | 支持批量 stream log 目录；无安全 log ref | 不支持失败插件 typed retry；重新运行不是 retry factory | 全局 recovery/retry/log/result 均 false，直到批量 spec 与 per-plugin identity 统一 |
| 上传 | 不支持 checkpoint；冲突检测结果只在 closure | 批量结果只有 dialog 文本；备份目录不是远端操作 artifact；无原上下文 navigator | 只有通用 Python/error log，不是 per-run ref | 不支持；非幂等远端副作用必须重新拉取远端状态、检测冲突并使用 idempotency policy | 全部 evidence action false |
| 下载 | 不支持 checkpoint；远端下载与本地 merge 没有候选/提交 frontier | 只有合并统计；无失败对象 artifact/navigator | 不支持 | 不支持；重试前必须重新下载并在隔离副本合并，不能重复修改当前 collection | 全部 evidence action false |
| 写回 | 不支持 checkpoint；无 TaskRuntime commit permit | 当前回调知道输出路径/Strings 列表，但只是消息文本；没有 verified artifact ref | 不支持 | 不支持；重试必须重新校验 source revision、目标覆盖/备份和输出 identity | 全部 evidence action false |
| FOMOD | 不支持 checkpoint/restart；typed stages 有 workspace/run_id，但没有 ADR-019 checkpoint envelope/frontier catalog | **底层支持 verified `ArtifactRef` 和 `PipelineResult`**，包括 published archive；当前 GUI 只把 archive path 渲染为文本，未注册 TaskArtifact evidence/navigator | 不支持 per-run log port；只有 diagnostics/stage result | 不支持 registry retry；typed RunSpec/hash/staging 为重做预检提供基础，但必须新 run_id | 当前 GUI 所有 evidence action false；迁移后可优先启用 artifact/result，不得启用 recovery/retry/log |
| Smart Assistant translate/polish | AutoTranslator translate 可能写 legacy progress；polish 无 checkpoint。TaskManager JobSpec identity 为 unspecified，因此不支持恢复 | completion data 是内存 dict；polish `last_report` 是模块级最近值，不是 owner/run artifact | 只有 process logger；无 per-run log ref | 不支持 TaskRetry registry | 局部 stop request 存在，但统一 cancel=false；其余 evidence false |
| Smart Assistant postprocess | 底层使用 `FilesystemPostProcessCheckpointPort` 和 translation commit checkpoint，但 TaskManager JobSpec owner/spec/input 与 RequestContext 不一致，且没有 RecoveryExpectation 注册；当前不支持恢复 | 报告渲染会产生文件路径并放入 completion data；无 TaskArtifact evidence/owner navigator | 只有 process logger | 不支持 | 局部 stop request/commit guard 存在，但 facade 终态过早，统一 cancel=false；report 仍只在原 Assistant 上下文显示 |
| Smart Assistant graph | 显式 checkpoint identity 时 V2 checkpoint 可校验 owner/spec/input/schema；但 production 未通过 GraphWorkloadAdapter 提交 TaskRuntime，也未注册 recovery descriptor | 节点结果留在 GraphExecutor/会话；无全局 artifact navigator | 只有 process logger/observability，不是统一 log ref | reflexion `RetryHandler` 是工具参数修正，不是终态任务新 Run ID retry | 全局全部 false；原 graph recovery 不能冒充 S03 task recovery |

关键证据：[legacy AI ProgressCheckpoint](../../src/transbridge/ai_translator/translator.py)、[single translation worker](../../src/transbridge/ui/tools/ai_translator/_translation_worker.py)、[batch worker](../../src/transbridge/ui/tools/ai_translator/_batch_translation_worker.py)、[mixed worker](../../src/transbridge/ui/tools/ai_translator/_mixed_worker.py)、[polish worker](../../src/transbridge/ui/tools/ai_translator/_polish_worker.py)、[typed FOMOD models](../../src/transbridge/application/fomod/models.py)、[Smart Assistant postprocess tool](../../src/transbridge/smart_assistant/tools/tool_proofreader.py)。

## 5. Legacy adapter 最小合同

在 S08/S09 完成迁移前，允许的临时 adapter 必须逐实例注入：

- 不可猜测的 adapter Run ID；不得复用窗口内 `1, 2, ...` 作为全局身份。
- `OwnerRef`：entrypoint、project、variant、session；owner 变化后拒绝控制和迟到事件。
- immutable display/run summary 与 input fingerprint；adapter 不需要伪装成完整 `JobSpec`，但必须能证明事件属于同一输入。
- 单调 sequence/revision；terminal 后丢弃迟到 progress/result。
- 显式 capability，而不是通过 `hasattr(worker, "pause")` 或任务名称推断。
- `close()/dispose()`：断开 Qt signals/listeners，并在 generation 失效后停止投影；不得直接终止不属于 adapter 的 worker。
- 唯一终态 owner：legacy worker 或 TaskRuntime 二选一；adapter 只读，不能同时 `finish_*`。

若无法提供上述任一字段，该 workload 只能保留原局部进度 UI，不能进入全局任务中心。

## 6. 迁移退出条件

### 解析

- 所有 GUI 解析/迁移入口提交 immutable parse JobSpec，包含 source identity/hash、format/options、project/variant owner。
- 单次/批量 parse workload 统一由 TaskRuntime backend 调度；候选 collection 在 commit permit 前不可写 `AppContext`/Project。
- cancellation、迟到结果、批量 partial outcome 和窗口销毁通过测试；删除所有 ParseCoordinator 直接 `ApiWorker` 任务路径后退出 adapter。

### AI translate / polish / mixed / batch

- S08 建立 immutable AI RunSpec，并让 UI Run ID、AutoTranslator run_id、checkpoint run_id 与 TaskRuntime JobRef 使用同一 identity。
- translate/batch checkpoint 迁移到 ADR-019 envelope；polish/mixed 若不实现 checkpoint，明确保持 recovery=false。
- 四种模式按真实 workload 声明 pause/resume/cancel；mixed parallel 必须能终止/等待子线程，或只声明 cancel-requested 且不承诺即时停止。
- report、entry navigation 和 stream log 经 owner-aware ports 注册；失败项 retry factory 新建 Run ID。
- 专用进度窗口只消费 TaskActivity projection，最后一个直接 QThread terminal owner 删除后退出 adapter。

### ParaTranz 上传

- S09 把冲突检测、计划确认、远端 mutation 组织为一个 immutable operation plan/JobSpec，并记录 owner、远端 project/file identity、input fingerprint。
- 正式上传使用 cancellation barrier、idempotency/commit policy；取消后不启动新远端副作用。
- partial 结果形成失败对象引用；retry factory 重新获取远端状态、重新预检并创建新 Run ID。
- 删除 UploadCard/OperationCoordinator 的两段 ApiWorker 终态编排后退出 adapter。

### ParaTranz 下载

- 下载结果先进入隔离候选集合，验证 source/collection revision 后凭 commit permit 合并。
- JobSpec 固定远端 project/file IDs、本地 project/variant/content identity 和 merge policy。
- partial/diagnostic/失败对象可投影；retry 新建 Run ID 并重新下载，不重放旧 merge closure。
- 删除 DownloadCard 直接 mutation 的 ApiWorker 路径后退出 adapter。

### 写回

- S09 生成包含 source snapshot、collection revision、输出 identity、覆盖/备份 policy 的 immutable JobSpec。
- Writer 使用 staging → validate → TaskRuntime commit permit → atomic publish；取消和迟到回调不能发布正式文件。
- verified output artifact 注册 navigator；retry 重做路径/覆盖/源 revision 预检并创建新 Run ID。
- 删除 WriteCard 直接正式写入的 ApiWorker 路径后退出 adapter。

### FOMOD

- GUI 改为提交 TaskRuntime JobSpec，且 JobRef.run_id 与 immutable `FomodRunSpec.run_id` 一致。
- 使用现有 `FomodPipelineWorkload`、`TaskRuntimeRunGuard`、`TaskRuntimeCommitGuard` 和 cancellation token；stage events 转为 progress/diagnostic/artifact events。
- published archive 的 verified ArtifactRef 经安全 navigator 暴露；pause/resume、checkpoint、retry 在另有证据前保持 false。
- 删除 GUI `_PipelineWorker` 和 direct `FomodPipeline.run()` 调度后退出 adapter。

### Smart Assistant

- `TaskManager.register()` 不再创建 generic `legacy-task/legacy:unspecified` JobSpec；每个 long tool 显式提交真实 owner、job_type、input/config fingerprint 和 capability。
- translate/polish/postprocess 只有在真正消费 pause token 后才可声明 pause/resume；否则从 JobCapabilities 和 UI 移除。
- cancel 必须保持 `cancelling`，直到 workload 确认安全点和 commit barrier 后再提交 `cancelled`；不得由 compatibility facade 收到请求后立即 `finish_cancelled()`。
- postprocess checkpoint identity 与 TaskRuntime JobSpec/OwnerRef 对齐并注册 RecoveryExpectation；report 注册 artifact/navigation evidence。
- production graph 需要全局展示时必须通过 `GraphWorkloadAdapter` 提交；无需全局展示则保持会话本地，不创建伪 activity。
- 所有 production 注册调用移除、Task Monitor 改为 S03 projection、compat facade 不再拥有 live task 后，才能删除 TaskManager legacy adapter。

## 7. 当前可安全发布的全局动作

在上述迁移完成前，S03 统一任务中心的安全默认值是：

- 解析、上传、下载、写回、FOMOD、AI mixed：只读进度/终态（有稳定 adapter identity 时）；所有控制、恢复、重试、日志和结果动作默认隐藏。
- AI translate/polish/batch：临时 adapter 可显示 worker 已证明的 stop/pause/resume；report/log 仍留在原窗口，直到 owner-aware evidence port 注册。
- Smart Assistant translate/polish/postprocess：原 Assistant 可保留本地“停止请求”，但统一任务中心必须隐藏 cancel/pause/resume，直到 compatibility facade 的取消确认与各 workload capability 修正完成。
- 所有 workload 的 `recover`、`retry` 默认隐藏，因为当前没有 production RecoveryExpectation 或 TaskRetry intent 注册。
- FOMOD 是最接近完整迁移的 workload：已有 immutable RunSpec、typed outcome、guard 和 verified artifacts，但 GUI 接线完成前仍不得提前显示 TaskRuntime 控制。
