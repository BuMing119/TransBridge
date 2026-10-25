# AI 任务界面反馈与术语查找响应修复

- 日期：2026-10-24
- Epic：ai-project-concurrency
- Story：S06 界面响应与高频反馈隔离
- 关联：[计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.7
- 范围：本轮开始时未提交工作区的独立拷贝与最终文件对比；不把此前并发、名称首批、窗口布局或其他用户修改归入本次。

## 问题与证据

用户反馈翻译、校对、后处理期间经常未响应。实际模型和后处理路径已由 `AiTaskWorker` 的 QThread、来源执行池及批次池运行；当前仍为同一进程中的多个线程。后台执行不代表 GUI 接收反馈时没有昂贵工作。

现场 watchdog 记录了 9.625、13.812、18.844 秒 GUI 心跳中断。其中一份主线程堆栈在旧 `_source_log` 回调，其余只停在 `app.exec()`；后台同时出现日志写入等待和术语正则编译。这些堆栈只能限定调查方向，不能单独证明 GIL、磁盘锁或某一个函数解释了全部卡顿。

可复现的主线程热点是 `_refresh_actions`：任务尚在运行时，每次进度仍计算 `ready_keys`，遍历全部词条。20,000 词条、1,000 次纯刷新剖析中，该扫描占约 92.7%。日志原先在 Qt 排队后才过滤，逐条刷新文档；限制文档显示行数并不能限制先前已排队的事件。普通翻译和校对也存在逐条重建术语映射、匹配器及冻结快照摘要的重复计算。

## 逐文件变更

路径相对仓库根目录。

- 修改 `src/transbridge/ui/tools/ai_translator/task_progress.py`：运行中先短路结果预览按钮判定，不再逐进度遍历全部结果；结束后仍读取真实可应用状态。
- 新增 `src/transbridge/ui/tools/ai_translator/task_worker_binding.py`：QObject/slot 在 GUI 线程接收 worker 事件，统一隔离旧尝试，缓存来源显示名称并一次交付一批日志；保留旧 worker 的双参数日志信号兼容。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run.py`：将 worker 订阅与释放接到独立绑定组件，保留任务恢复、应用、完成及取消的原有权威边界。
- 新增 `src/transbridge/ui/tools/ai_translator/task_feedback.py`：不依赖 Qt 的有界缓冲。按来源/阶段保存最新绝对进度，日志在进入 GUI 队列前过滤；最多缓冲 3,000 行、1,024 个状态，常规一次最多交付 100 行，省略明确可见。日志积压时仍交付最新状态。锁覆盖取出和信号发布，来源结束及最终完成前冲刷有界尾部，拒绝完成后的迟到反馈。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：沿已有 50 毫秒监控节拍排空反馈；新窗口订阅批量日志，旧消费者仍可接收逐行信号。暂停、取消控制不等待缓冲排空，工厂参数及最终术语检查顺序不变。
- 新增 `src/transbridge/ai_translator/term_lookup_cache.py`：独立术语视图及懒编译匹配器，每类缓存最多 16 个作用域。键覆盖完整上下文、已发布版本、动态修订、加载代次和发布事务对象身份；未知无修订 provider 保守使用即时查找。构造视图时保持数据库锁先于缓存锁，重载的远程/embedding I/O 不持共享池锁。发布回调若已持池锁且需要等待另一线程重载，明确报错让事务回滚，避免循环等待。
- 修改 `src/transbridge/ai_translator/term_database.py`：动态库的受支持加载/写入接口增加修订；manager 仅接线独立缓存、加载失效及既有查找替换，保留主词、别名、大小写、最长匹配和优先级。
- 修改 `src/transbridge/ai_translator/shared_term_lookup.py`：共享快照复用匹配器，保留调用方捕获的旧快照；已有局部优先级和已发布解析仍每次执行，不缓存覆盖或 suppression 决策。
- 修改 `src/transbridge/application/translation/terminology_run_snapshot.py`：对嵌套成员不可变的冻结快照只验证一次并建立索引；每次读取仍校验项目、Variant 和请求版本，TaskConsistency 的外部修改保护保持原状。
- 新增 `tests/ui/tools/test_ai_worker_feedback_binding.py`：3 项运行中无全量扫描、实际线程到 GUI 投递及旧尝试/完成后日志隔离回归。
- 新增 `tests/ui/tools/test_ai_task_feedback.py`：11 项合并、有界缓冲、溢出明示、多生产者、日志积压不阻挡进度、生命周期屏障及暂停取消控制回归。
- 新增 `tests/ai_translator/test_term_lookup_cache.py`：19 项作用域、修订、重载、发布版本、别名、未知 provider、LRU、并发首查、回滚复用修订号及锁顺序回归。
- 新增 `tests/application/translation/test_frozen_terminology_lookup.py`：2 项只验证一次、身份/版本与构造前损坏保护回归。
- 新增 `scripts/benchmark_ai_task_feedback.py`：可指定源码树的离线真实 worker/Qt 反馈基准，构造和交付分别计时，断言 GUI 线程、最终进度和日志保留。
- 修改 `docs/requirements.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：将后台反馈合并、界面刷新和缓存边界写回规范来源；更新本计划与直接相关的两份索引。

`AiTaskRun` 现为 468 行、31 个方法。事件订阅职责已提取，保留恢复/提交生命周期以避免扩大候选和持久化改动；再次扩展这些职责前应提取完整组件。`term_database.py` 为 991 行、主类 35 个方法，已超过大小门禁；本次新增缓存职责完整放到独立模块，原文件只接线和替换热路径，旧检索与用户既有改动未扩大重构。

## 验证

仓库 Python 3.12.12 / uv 环境，模型与远程来源均用本地替身。

1. GUI 绑定首轮聚焦 52 项通过；缓存、反馈及生命周期首轮 117 项通过。交叉审查随后发现发布回滚复用修订号、重载长持共享锁和发布回调重入边界，补充确定性回归并修复后，最终专项 **122 passed**（2.61 秒）：

```powershell
uv --cache-dir .tmp-ai-ui-hang-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator/test_term_lookup_cache.py tests/ai_translator/test_project_terminology_adapter.py tests/ai_translator/test_required_term_matching.py tests/ai_translator/test_shared_term_pool.py tests/ai_translator/test_term_database.py tests/application/translation/test_frozen_terminology_lookup.py tests/application/translation/test_terminology_run_snapshot.py tests/ui/tools/test_ai_task_feedback.py tests/ui/tools/test_ai_worker_feedback_binding.py tests/ui/tools/test_ai_project_name_progress.py tests/ui/tools/test_ai_source_concurrency.py tests/ui/tools/test_ai_runtime_control_order.py -q --disable-warnings --basetemp=.q28/final-focus
```

2. 修正上述边界后重新运行完整相关集合，**1672 passed、2 deselected**（165.87 秒，8387 条 warnings）；新增回归合计 35 项。此前中间版本的 1667 项通过不作为最终版本证据。

```powershell
uv --cache-dir .tmp-ai-ui-hang-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools tests/ui/test_hang_watchdog.py --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q28/full-final
```

两个排除项是此前已在基线复现的配置窗口字号布局失败。未运行智能助手/独立术语工作台的专属子目录及全仓全部测试。恢复夹具需要 Windows 硬链接，完整集合在获准的非受限执行中运行；未访问用户的实际翻译任务。

3. `uv run ruff check src tests scripts/benchmark_ai_task_feedback.py`、`uv run ruff format --check src tests scripts/benchmark_ai_task_feedback.py` 均通过（实际同样带上述 cache/offline/no-sync 参数，2048 个文件已格式化）；`git diff --check` 通过。最终复核确认只改动本轮声明文件，历史增量记录未改写。

## 离线性能证据

真实 worker 基准使用两个来源、总计 20,000 个合成词条和 1,000 组进度/日志，走实际 `_start`、worker、来源调度、绑定和进度窗口。Qt offscreen，10 毫秒心跳；替换模型、提交、快照、任务记录与外部 TaskRuntime adapter，不测完整任务启动或模型时延。基线是本轮开始时的工作区拷贝，包含此前未提交修复，不是 Git HEAD。

使用下列命令，按基线/当前交替运行五对，每次独立进程、`--repetitions 1`，输出文件后缀分别为 1～5：

```powershell
.venv/Scripts/python.exe scripts/benchmark_ai_task_feedback.py --source .tmp-ai-ui-hang-20261024/src --entries 20000 --events 1000 --repetitions 1 --output .tmp-ai-ui-hang-20261024/worker-baseline-1.json
.venv/Scripts/python.exe scripts/benchmark_ai_task_feedback.py --source src --entries 20000 --events 1000 --repetitions 1 --output .tmp-ai-ui-hang-20261024/worker-fixed-1.json
```

- 反馈交付中位：0.705465 秒 → 0.029215 秒；五轮范围分别为 0.697552～0.863120、0.027959～0.029783 秒。
- 单次事件处理最长耗时的中位：0.610535 秒 → 0.010639 秒；范围分别为 0.474345～0.784324、0.010002～0.011915 秒。
- 心跳最大间隔的中位：0.494551 秒 → 0.014585 秒；范围分别为 0.413963～0.786748、0.014249～0.019639 秒。
- 1,000 次进度回调合并成 2 次最终绝对计数，两个来源均到 500；1,002 行日志保留，文档插入从 1,002 次变为 13～14 次。
- 窗口构造中位 0.485086 → 0.463267 秒，未把该差异作为本次优化结论。该短突发主要覆盖完成前冲刷；持续积压及常规排空语义另由回归验证。

另有临时术语热点诊断脚本，最终命令为 `uv --cache-dir .tmp-ai-ui-hang-20261024/admission/uv-cache run --offline --no-sync python .tmp-ai-ui-hang-20261024/admission/measure_term_retrieval.py --product`。每轮 768 条术语、32 条合成文本，五轮中位：翻译准备 184.79 → 14.09 毫秒；发送前合入 695.78 → 43.27 毫秒；校对查词 850.01 → 41.06 毫秒。最终范围分别为 13.71～16.07、40.07～51.33、40.39～41.67 毫秒；实际正则编译由 24,576 次降到 768 次、匹配器由 32 次降到 1 次。这里只说明重复本地计算减少，不等于模型吞吐提升或所有现场卡顿消失。

## 兼容与遗留

无持久化格式或数据迁移，模型并发配置、翻译/校对质量依赖和最终提交权威不变。新窗口用批量日志；旧双参数信号保持可用，但接收的是过滤后的可见过程事件。完整 LLM 请求/响应日志路径未修改。生产写入走动态库 `load/add/add_many` 和共享池发布，均有失效保护；调用方绕过这些接口原地修改已捕获的可变 `TermEntry` 不受缓存失效契约支持，仓库当前未发现这种生产写入路径。

仍有待另行取证的同步工作：启动时的草稿/版本快照复制、结果应用前身份比较和任务记录整理，以及独立 LLM 日志查看器的目录与文本读取。当前没有把整个执行器改为独立进程，也未将这些涉及实时草稿的工作贸然移到后台；不能据本次离线结果宣称全部“未响应”已解决。真实模型吞吐、语义质量与用户正在运行的进程未验证或中断，旧进程需要之后启动更新源码才会生效。

独立只读复核未发现新增反馈主链缺陷，但指出既有暂停显示竞态：监控线程已排队的 `paused` 若晚于 GUI 的恢复动作交付，可能让文案退回“已暂停”。这是缺少暂停代次校验的静态推演，本轮未动态复现，也未扩展修改控制协议；有界反馈不会自动解决这种旧状态事件问题。

本轮专属基线、诊断脚本、JSON、uv 缓存及 pytest 临时数据在收尾清理；保留仓库中的可复用反馈基准脚本。现场日志与用户数据不删除。
