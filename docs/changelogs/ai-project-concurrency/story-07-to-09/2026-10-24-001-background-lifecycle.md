# AI 启动、结果整理与辅助窗口后台化

- 日期：2026-10-24
- Epic：ai-project-concurrency
- Story：S07～S09
- 关联：[计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.7
- 归属：以本轮开始时的工作区副本为基线，仅记录本轮启动/结果生命周期及辅助窗口改动。此前未提交修复和同时进行的 S10 即时草稿复用不属于本记录。

## 问题与交付行为

模型和后处理已有后台执行器，但启动时的范围选择、输入复制、草稿与账本构造，以及结束后的结果吸收、报告整理和记录准备仍占用 GUI。仅将模型调用放在线程中不能消除这些停顿。

生产任务窗口现在先显示可取消的准备窗口；GUI 分片捕获实时输入，后台筛选来源、建立私有草稿和初始索引，执行文件预检、术语检查及快照准备。启动期间不会因排队的配置通知再做一次同步全量估算。结果阶段后台吸收候选、生成摘要、准备合并与记录，再由 GUI 校验归属并发布。恢复初始化先于模型执行；历史和恢复写入串行收尾，完成通知等待最终记录。

GUI 最终完整字段校验与权威发布仍保留，避免绕过集合修订的旧式直接编辑被覆盖。提交后基线重建转为分片捕获及后台整理；若整理失败，保留已应用事实并阻止继续保存、重试或结束，不能把已提交结果记成未应用。V2 后台保存同时校验捕获的项目、版本和修订，防止冻结 A 的请求后切到 B 而误保存 B；legacy 标签、标签库及位置也受到输入守卫保护。

独立日志窗口在后台扫描和读取文件，连续请求只保留一个最新待办；报告后台建立搜索、诊断和详情投影。校对预览改为虚拟表，保留逐条选择、全选、候选值及确认结果。超长单行会使 Qt 排版本身长时间阻塞，因此日志/诊断/详情正文限制为 64 Ki 字符、1000 个内容行及每行 2048 字符，截断有明确提示。表格显示和 tooltip 也有上限；完整日志、记录、候选及导出数据不截断。

## 逐文件变更

以下 Python 文件在未另行注明时位于 `src/transbridge/ui/tools/ai_translator/`。

- 新增 `task_launch.py`：拥有启动准备、取消、预检、准入和最终窗口交接；后台任务存活至完成，关闭后丢弃迟到结果。空恢复记录已经完成后，窗口刷新异常仍会释放启动器并明确报告已完成事实。
- 修改 `task_runtime.py`：统一入口转交 `TaskLaunch`，防止重复启动并停止排队的旧估算。
- 修改 `ai_translator_window.py`、`_window_actions.py`：准备期间短路同步范围/预检刷新；关闭配置窗口取消尚未发布的启动请求。
- 新增 `run_request_preparation.py`：捕获术语所有者与配置，后台构造运行请求、摘要和冻结术语视图。
- 修改 `run_controller.py`：接收已准备请求并校验其输入身份与选项，保留旧调用入口；准入阶段不重新读取术语或计算大输入摘要。
- 新增 `task_session_preparation.py`：输入捕获按最多 128 步/约 4 毫秒调度，后台建立独立草稿和账本，最终在 GUI 执行完整字段守卫；准备任务自主保活并支持取消。
- 新增 `task_source_selection.py`：捕获来源、表格范围、标签和恢复意图，后台使用同一份冻结输入执行既有筛选与恢复规则；恢复 JSON 复制同样分片。
- 修改 `task_session.py`：增加 `create_async`、后台重试及输入验证接口，复用初始账本；应用后刷新基线异步化，失败保留提交证据并封锁后续事务。
- 修改 `task_draft.py`：从已捕获输入建立草稿，后台准备重试和合并，避免重复读取实时 GUI 集合。
- 修改 `task_apply_preparation.py`：在后台准备来源集合及提交后基线，交付使用既有 GUI QObject 边界。
- 新增 `src/transbridge/ui/version_snapshot_inputs.py`：捕获 legacy 快照元数据和来源约束，后台构造实际存储输入，发布前校验标签等直接修改。
- 修改 `src/transbridge/ui/version_persistence.py`：冻结快照/保存端口与目标身份；V2 `save` 传入完整 `expected_active_signature`，由既有 lifecycle 锁内校验；legacy 保存完成后的 dirty 确认回到 GUI。
- 新增 `task_empty_recovery.py`：空恢复任务采用后台读取准备、GUI 当前性校验、后台写入 completed 的两阶段收尾；写入后只刷新界面，不以迟到校验否认持久化事实。
- 新增 `task_result_preparation.py`：私有结果吸收、应用准备及 applied 索引计算的单任务后台协调器。
- 新增 `task_result_flow.py`：结果整理、应用、重试时序及取消/版本保护；文件一致性检查在后台执行，GUI 发布前继续检查实时状态。
- 新增 `task_result_index.py`：不可变的逐来源及全局摘要、失败/候选/已应用/待确认集合。
- 修改 `task_entry_results.py`：按账本代次缓存索引，冻结记录输入共享有效索引；正常状态变更使索引失效。
- 修改 `task_run.py`、`task_completion.py`：接入结果协调器，显示整理阶段，完成和关闭等待后台结果及记录；保留正式提交后的 applied 证据和原有保存语义。
- 新增 `task_record_inputs.py`：GUI 只捕获来源描述及冻结账本，worker 生成完整报告快照，不在后台读取可变 run/session。
- 修改 `task_record_writer.py`：历史记录串行异步写入，一个执行中任务及一个最新待办，失败可见；保留无冻结账本的旧调用兼容。
- 新增 `task_recovery_persistence.py`：独立恢复记录初始化、序列化和更新操作。
- 修改 `task_recovery_binding.py`：生产恢复记录后台初始化和串行更新，保留已保存条目证据；“重新开始”显示配置后再异步筛选，“继续任务”交给启动器收尾，不能按旧同步假设立刻关窗并触发取消。
- 新增 `read_projection.py`：QObject queued 投递、单个最新待办、窗口代次隔离；关闭不阻塞等待读取线程。
- 新增 `llm_log_reader.py`，修改 `_llm_log_viewer.py`：后台目录/stat/尾部读取，保留选中文件与阅读位置，轮询不取消尚未完成的慢读取。
- 新增 `polish_preview_model.py`，修改 `_polish_preview_dialog.py`：后台准备预览行与虚拟表；保持既有返回映射，限制显示文字但保留完整候选。
- 新增 `task_result_projection.py`，修改 `task_result_dialog.py`：后台报告索引、过滤、诊断和详情，搜索去抖及过期结果隔离；导出仍使用完整记录。
- 新增 `bounded_read_text.py`：后台产生明确截断的有界显示文本，普通短文本逐字符保持不变。
- 新增 `scripts/benchmark_ai_task_lifecycle.py`：可选择源码树的真实 Qt 事件循环基准，分开记录总耗时、初次回调、最大心跳间隔及线程归属，可渲染合成窗口。
- 新增 `scripts/benchmark_ai_read_presentation.py`：2 MiB/超长单行日志与诊断的实际 Qt 交付、排版和 repaint 测量，不操作用户窗口或数据。
- 修改 `docs/requirements.md` 的 FR5.13.7、`docs/adr/048-ai-project-concurrency-and-terms.md` 的生命周期职责，以及本计划、计划索引和 changelog 索引；不覆盖 S10 的并行文档修改。

新增测试：`test_ai_session_preparation.py`、`test_ai_source_selection_preparation.py`、`test_ai_frozen_save_boundary.py`、`test_ai_task_launch_background.py`、`test_ai_launch_refresh_guard.py`、`test_run_request_preparation.py`、`test_ai_result_background.py`、`test_task_background_records.py`、`test_ai_background_read_projections.py`、`test_ai_bounded_read_text.py`。这些文件覆盖真实线程身份、阻塞式后台工作下的 GUI 心跳、直接字段修改、切换项目/版本、取消和窗口销毁、恢复入口、保存目标、已提交事实及完整导出。新增 `ai_read_projection_support.py` 提供基于实际结果的事件循环等待。

调整既有测试以等待异步完成并保留原业务断言：`test_ai_task_session.py`、`test_ai_version_snapshots.py`、`test_ai_partial_task.py`、`test_ai_task_action_flow.py`、`test_unified_task_progress.py`、`test_unified_task_runtime.py`、`test_ai_runtime_commit_boundary.py`、`test_task_completion_flow.py`、`test_translation_workflow_closure_audit.py`、`test_ai_task_presentation.py`、`test_task_recovery_binding.py`、`test_task_recovery_discovery.py`、`test_ai_history_result_interactions.py`、`test_ai_task_history_ui.py`、`test_layout_stability.py`、`test_live_llm_logs.py`、`test_llm_log_popup.py`、`test_polish_preview_dialog.py`、`test_workflow_progress.py`。上述测试均位于 `tests/ui/tools/`。

## 责任审查与兼容

`AiTaskRun` 为 384 行、31 个方法，结果流程/索引/记录计算已提取为完整组件，原类保留运行状态及兼容回调。`TaskSession` 为 501 行、35 个方法，其中 11 个属性；输入捕获、筛选、草稿合并和持久化输入独立，原类只编排版本事务。保留兼容接口以避免扩大旧持久化适配风险；再次增加状态或版本事务职责前应继续按完整事务边界提取。

同步 session/持久化入口保留给旧调用方，生产统一任务窗口走异步入口。没有修改项目格式、依赖、并发设置或模型配置，也没有新增独立进程。GUI 完整字段守卫仍为 O(N)，单条 metadata 递归复制和权威发布也不是实时期限保证；本轮不能据此宣称任意规模下每一帧都低于 16 毫秒。只有所有编辑入口均具备可靠修订协议后，才能安全替换直接字段守卫。

## 回归验证

使用仓库现有 Python 3.12.12 / uv 环境，所有模型与远程来源使用本地替身。启动/事务切片 202 项收敛，结果生命周期 62 项通过，读取展示专项覆盖 90 项；最后加入预览长文本和恢复 closeEvent 修复后，相关五文件 **73 passed**。这些集合有重叠，不能相加为独立总数。

最终冻结代码的完整相关集合：**1846 passed、2 deselected、8397 warnings，182.10 秒**。

```powershell
uv --cache-dir .tmp-ai-background-lifecycle-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools tests/ui/test_hang_watchdog.py tests/persistence/v2/test_lifecycle_save_transactions.py tests/application/projects/test_lifecycle.py --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q29/i
```

两个排除项是此前已在基线复现的配置窗口字号布局失败。未运行全仓全部测试、智能助手和独立术语窗口专属目录，也未调用真实 LLM/API。完整集合包含工作区其他已存在工作线的相关用例，通过不代表这些改动归属于本记录。

中间验证修正了新测试类名拼写，以及仍假设日志同步加载的旧断言；后者改为等待真实可见结果。Windows 硬链接夹具在受限沙箱中报 WinError 5，改用获准的非受限离线执行；一项 281 字符的临时路径失败以短 basetemp 复验通过，未为这些环境问题修改生产代码。最终完整集合使用短路径通过。

```powershell
uv --cache-dir .tmp-ai-background-lifecycle-20261024/uv-cache run --offline --no-sync ruff check src tests scripts/benchmark_ai_task_lifecycle.py scripts/benchmark_ai_read_presentation.py
uv --cache-dir .tmp-ai-background-lifecycle-20261024/uv-cache run --offline --no-sync ruff format --check src tests scripts/benchmark_ai_task_lifecycle.py scripts/benchmark_ai_read_presentation.py
git diff --check
```

以上均通过，格式检查覆盖 2082 个文件。真实 offscreen Qt 渲染了任务进度、校对预览、结果报告及日志窗口，加载系统中文字体后逐张检查文字和控件布局；没有用生成图片代替实际窗口验证。

## 离线性能证据

环境：Windows 11 10.0.26200、Python 3.12.12、仓库既有依赖、Qt offscreen、微软雅黑系统字体、10 毫秒 GUI 心跳。基线为本轮开始时完整工作区源码副本，包含此前未提交修复，不是 Git HEAD。基线和当前按规模各交替运行五对，每次独立进程、`--repetitions 1`；没有同时运行 pytest 或其他性能负载。

场景包含两个来源、session 和任务窗口构造、校对结果吸收直到待确认。计时终点覆盖最终 GUI 守卫与窗口建立，最大心跳间隔覆盖整个阶段，不能用很短的初次异步派发时间代替它。该场景不包含范围筛选、快照/历史/恢复落盘、权威提交、模型/API 时间；这些边界由相应线程与生命周期回归验证，不能把本测量当完整项目耗时。

20,000 词条，单位秒，格式为中位数 `[最小, 最大]`：

- 启动总耗时：基线 **8.1377 [7.3681, 9.9905]**，当前 **6.3280 [6.2131, 7.9228]**。
- 启动最大 GUI 心跳间隔：基线等于同步总耗时；当前 **0.2332 [0.2135, 0.3067]**。
- 结果吸收总耗时：基线 **0.3135 [0.2743, 0.3960]**，当前 **0.3495 [0.3045, 0.3829]**。
- 结果吸收最大 GUI 心跳间隔：基线等于同步总耗时；当前 **0.04382 [0.03303, 0.05142]**。
- 当前最终完整字段守卫：**0.01830 [0.01351, 0.02243]**，仍在 GUI。账本初始化和 ingest 探针从基线 GUI 改为 background。

结果吸收的总耗时略有增加，但 UI 连续阻塞显著缩短；不把后台化描述成每种计算都加速。

独立文本场景为 2 MiB ASCII，包含 1 MiB 无换行文本。旧展示路径的日志和诊断分别在超过 10 秒、15 秒仍卡于 `setPlainText`，只中断本任务创建的离线进程；这些数字是下界，既非精确耗时，也不是五轮基线中位。修复后相同输入五轮完成，正文显示上限为 65,536 字符：

- 日志 `setPlainText + repaint`：**0.004508 [0.004039, 0.009305] 秒**；阶段最大心跳间隔 **0.012359 [0.009977, 0.022846] 秒**。
- 诊断 `setPlainText + repaint`：**0.006659 [0.005594, 0.015832] 秒**；阶段最大心跳间隔 **0.016426 [0.012020, 0.039939] 秒**。

完整日志文件、候选和报告导出保留原值，截断仅用于控件排版。该测量解释了为什么后台读完文件后仍可能未响应：Qt 排版本身也需要有界输入。

100,000 词条五对压力结果，单位秒，格式仍为中位数 `[最小, 最大]`：

- 启动总耗时：基线 **44.28335 [26.83958, 50.90619]**，当前 **36.93893 [27.70991, 43.15683]**。
- 启动最大 GUI 心跳间隔：基线等于同步总耗时；当前 **1.13619 [0.97635, 1.18903]**。
- 结果吸收总耗时：基线 **2.01887 [1.24735, 2.28046]**，当前 **1.94144 [1.08941, 2.35873]**。
- 结果吸收最大 GUI 心跳间隔：基线等于同步总耗时；当前 **0.06117 [0.04441, 0.07243]**。
- 当前最终完整字段守卫：**0.09117 [0.07728, 0.12134]**，仍在 GUI；全部当前账本初始化和 ingest 探针均为 background，基线均为 GUI。

大样本启动仍存在约一秒的心跳间隔，最大间隔明显大于最终字段守卫耗时，本基准未对其原因归因，不能声称全部源于守卫或已经完全无延迟。总耗时存在较大轮次波动，当前输入下可确认的是长期连续 GUI 占用显著下降；真实项目的 metadata、磁盘和主窗口投影成本仍可能不同。

生命周期基准实际命令模板如下；对 `20000`、`100000` 分别以基线/当前顺序交替运行五对，输出后缀为 1～5，共 20 个独立进程：

```powershell
uv --cache-dir .tmp-ai-lifecycle-20261024/uv-cache run --no-sync python scripts/benchmark_ai_task_lifecycle.py --source .tmp-ai-lifecycle-20261024/src --entries 20000 --repetitions 1 --output .tmp-ai-lifecycle-20261024/lifecycle-20000-baseline-1.json
uv --cache-dir .tmp-ai-lifecycle-20261024/uv-cache run --no-sync python scripts/benchmark_ai_task_lifecycle.py --source src --entries 20000 --repetitions 1 --output .tmp-ai-lifecycle-20261024/lifecycle-20000-current-1.json
```

长文本复测使用 `scripts/benchmark_ai_read_presentation.py --source src --bytes 2097152 --repetitions 5 --temp-root .tmp-ai-lifecycle-20261024 --output .tmp-ai-lifecycle-20261024/read-presentation-after.json`。本记录保留汇总、中位数和范围；任务临时源码副本、原始输出及截图在完成核对后清理，不作为仓库产物保留。未重启或取消用户现有任务，没有提交 Git。
