# S14～S16：大任务术语读取、候选检查点与进度响应性

- 日期：2026-10-24。
- Epic：ai-project-concurrency；Story：S14～S16。
- 关联：[实施计划](../../../../plans/ai-project-concurrency/progress-responsiveness-plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)。
- 用户确认：解决术语快照、检查点重复处理和进度消息积压，以翻译任务进度／过程日志窗口为重点，验证大任务及暂停、恢复、保存。

## 基线与问题

本轮开始时工作区已有大量未提交修改，HEAD 为 `ee3a592013429aafcc67dc96c0dc7650e0af9825`。
因此使用开始时复制的工作区作为对照，不把相对 HEAD 的全部差异归入本轮。
基线清单 SHA-256 为 `1d1ccbc51079084e9eabf5d2460f8bbd87608d64a5792ae9eb23b40296d5d94d`。
此前已实现的术语参考化、AI 综合校对、Excel 标注和后台生命周期均保留。

现场挂起日志曾记录约 5～15 秒的 GUI 心跳空档，但栈停留在 `app.exec`，不足以证明全部现场停顿来自某一处。
本轮确认并复现三个可修复的重复工作：共享池每次快照深复制、候选批次全量读写检查点，以及 GUI 停止消费期间持续排入过时的 Qt 进度事件。

## 逐文件变更

以下“新增／修改”相对本轮工作区基线，部分“修改”文件在 Git 中仍为此前任务创建的未跟踪文件。

- 新增 `src/transbridge/ai_translator/shared_term_snapshot.py`：`FrozenTermEntry` 递归冻结 metadata，`SharedTermSnapshots` 每次发布只归一化一次，最多保留 16 个插件作用域视图；快照持有稳定的事务／作用域身份。
- 修改 `shared_term_pool.py`：复用发布代次的快照，在发布失败回滚时恢复原快照索引；同一个修订号被后续事务重用时也不会误命中失败事务的缓存。
- 修改 `term_lookup_cache.py`：`TermLookupView` 复用精确索引与匹配器，按稳定快照身份缓存共享视图；历史术语优先级按修订建索引，未知可变 provider 继续即时读取。
- 修改 `exact_term_lookup.py`：精确索引保存主词和译文，`term_for`／`match` 保留原有大小写、规范化、别名及主词返回语义。
- 修改 `shared_term_lookup.py`：使用冻结记录和已有索引，不逐条构建整份快照字典或扫描所有历史术语。
- 修改 `term_database.py`：既有查找入口接入上述索引，内部读冻结记录，公开解析接口仍返回可修改的独立 `TermEntry`。移除原地重复构建的精确索引。
- 修改 `src/transbridge/application/translation/candidate_checkpoint.py`：公开不可变检查点及 `load` 合同保留；文件端口委派独立持久层，增加可选增量接收和修订核验。
- 新增 `candidate_checkpoint_journal.py`：存储租约、校验链日志、刷盘／读回验证及原子提交头；恢复仅接纳提交头覆盖的完整前缀，忽略尚未提交的尾部。
- 新增 `candidate_checkpoint_store.py`：按 run 缓存已验证状态、应用批次增量、检测外部文件变化，并在达到条数和尺寸阈值时压缩；旧日志在新提交头成功后清理，失败保留原始错误及可恢复证据。
- 修改 `legacy_bridge.py`：按 collection revision 复用响应别名索引，持久化只传入新增批次；保留歧义处理、重复响应跳过、提交后继续接收和存储失败拒收。
- 修改 `commit.py`：一次提交只计算一次候选集合指纹，恢复后重复提交不再对每条已应用记录重算整个集合。
- 修改 `proofread_advisory.py`：检测器依赖移至实际准备校对候选时加载，修复从术语模块首先导入时的包循环；综合校对行为不变。
- 修改 `src/transbridge/ui/tools/ai_translator/task_feedback.py`：生产模式只保存每个来源、项目阶段和请求计数的最新绝对状态，日志最多 3000 条；在短锁内取出数据，释放后才更新 GUI。来源开始／结束及完成作为有序边界保留；日志省略计数可见。
- 修改 `task_worker.py`：高频请求状态纳入同一缓冲，后台 monitor 在轮询模式下不再发出高频 Qt 信号；暂停／取消仍独立处理，旧信号订阅入口保留。
- 修改 `task_worker_binding.py`：生产绑定每 50ms 在 GUI 线程取最新反馈；旧 worker 不再刷新新执行，finished 停止计时器并强制交付尾部，确保完成和最后结果不遗漏。
- 修改 `tests/ai_translator/test_shared_term_pool.py`、`test_term_lookup_cache.py`：覆盖不可变快照、副本隔离、作用域、发布失败回滚、缓存淘汰、别名和未知 provider。
- 新增 `tests/application/translation/test_candidate_checkpoint_journal.py`：覆盖重开恢复、重复和替换、旧格式迁移、并发修订、刷盘／验证／发布失败、损坏、压缩中断、未知可变集合及重复提交只哈希一次。
- 新增 `tests/ui/tools/test_ai_feedback_polling.py`：后台产生 4 万次来源进度和 2 万条日志，GUI 恢复后只取两份最新状态；验证线程归属、完成尾部、暂停继续、取消不提交、成功仅保存一次及回调不持缓冲锁。
- 修改 `scripts/benchmark_ai_task_feedback.py`：增加分波反馈和强制暂停 GUI 消费的对照场景，明确区分生产耗时、故意暂停和恢复刷新耗时。
- 新增 `scripts/benchmark_term_snapshot.py`、`scripts/benchmark_candidate_checkpoint.py`：可指定源码目录，使用临时合成数据重复测量术语读取／请求最终合入及真实刷盘检查点。
- 修改 ADR-048 和主 plan；新增响应性实施计划；索引只更新本轮直接关联项。

责任审查：新冻结记录、日志编码／刷盘、检查点状态分别为独立模块；现有 962 行 `term_database.py` 仅减少重复实现并接入既有查找入口，不增加职责。继续遵守主 plan 的拆分触发条件。未引入 Qt 到 application 或领域层。

## 性能对照

Windows、Python 3.12.12、同一 uv 环境；所有测量为离线合成数据，无真实 LLM、网络、用户项目或翻译数据写入。
基线和当前版本各 5 轮，性能基准串行运行，不与 pytest 并行争抢 CPU。数字为中位数，括号为五轮范围。

### 术语热路径

10,000 个术语，每批 20 条输入，每轮预热后连续测量 10 批，单位 ms／批：

- 快照读取：353.479（327.769～383.086）→ 0.001690（0.001390～0.001850）。这是已有冻结快照的复用，不包含首次冻结／发布成本。
- 精确查找：437.580（397.917～483.402）→ 0.055520（0.052570～0.079610）。
- 请求最终术语合入：1616.847（1440.021～1657.593）→ 173.486（158.093～219.504）；仍需要按输入执行术语匹配，不等于整个模型请求提速 9 倍。

### 已有 20,000 条候选时继续保存

每轮先写相同的 20,000 条合成候选，再接受 10 个 50 条批次，完整保留 fsync、读回验证和新端口恢复校验：

- 10 批合计：38,326.236 ms（34,549.016～44,574.199）→ 336.256 ms（334.386～383.104）。
- 各轮批次中位数的中位：3938.034 ms（3462.835～4447.139）→ 21.565 ms（21.180～24.311）。
- 各轮最慢批次的中位：4380.268 ms → 143.985 ms；新版首次建立来源别名索引仍有成本。
- 新端口完整恢复：1251.190 ms（1067.605～1414.199）→ 1897.158 ms（1632.243～2006.591）。恢复增加了日志校验和回放成本，运行在既有后台入口；不宣称所有持久化动作均变快。
- 每轮恢复断言：20,500 条候选、修订 11；最终存储约 10.88 MB，新旧空间量级一致。

### GUI 暂停消费后的反馈恢复

真实 `AiTaskWorker`、来源调度、绑定、进度展示和日志控件；两个来源共 20,000／100,000 条合成条目及相同数量的反馈对，40 波，波间 50ms。
测试故意暂停 GUI 消费 2500ms 并等待生产者结束，再测恢复；故意暂停及生产时间不算作自然心跳延迟。

- 20,000 条：恢复刷新 0.329291 s（0.287491～0.452494）→ 0.113739 s（0.104682～0.139870）；最大 GUI 心跳间隔中位 0.329143 s → 0.112379 s；进度回调每轮 80 次 → 2 次，日志插入 72 次 → 5 次。
- 100,000 条：恢复刷新 0.393106 s（0.349866～0.410498）→ 0.099216 s（0.068107～0.139599）；最大 GUI 心跳间隔中位 0.392994 s → 0.098680 s；进度回调原为 95～108 次，新版均为 2 次，日志插入原为 80～87 次，新版均为 5 次。
- 每轮断言最终来源计数正确、所有控件回调在 GUI 线程、completed／finished 各一次；可见日志保留最近 3000 块并显示省略信息。

此 GUI 场景不包含模型调用、候选提交、快照持久化、任务记录及外层 TaskRuntime。
基准夹具直接构造结果账本，2 万／10 万条窗口构造仍约 2／9 秒，未使用生产入口已有的异步预计算；这段单列报告，不算作本轮反馈恢复改善。
三组性能证据分别覆盖术语、持久化和 GUI，不能拼成一次完整真实模型任务，也不能据此断言现场所有 5～15 秒卡顿已消失。

可复现命令（分别将 `--source` 指向开始时工作区副本和当前 `src`；scratch／output 使用任务自有临时目录）：

```powershell
uv run python scripts/benchmark_term_snapshot.py --source src --scratch <scratch> --samples 5 --terms 10000 --batches 10 --output <terms.json>
uv run python scripts/benchmark_candidate_checkpoint.py --source src --scratch <scratch> --samples 5 --accepted 20000 --batch-size 50 --batches 10
uv run python scripts/benchmark_ai_task_feedback.py --source src --entries 20000 --events 20000 --waves 40 --gui-stall-ms 2500 --repetitions 5 --output <feedback-20000.json>
uv run python scripts/benchmark_ai_task_feedback.py --source src --entries 100000 --events 100000 --waves 40 --gui-stall-ms 2500 --repetitions 5 --output <feedback-100000.json>
```

## 功能与静态验证

专项集合 62 passed（13.53s），覆盖候选日志、草稿复用、项目名称接续、反馈轮询、工作线程绑定及请求计数展示：

```powershell
uv run pytest tests/application/translation/test_candidate_checkpoint_journal.py tests/application/translation/test_draft_reuse.py tests/ui/tools/test_ai_project_name_reuse.py tests/ui/tools/test_ai_feedback_polling.py tests/ui/tools/test_ai_task_feedback.py tests/ui/tools/test_ai_worker_feedback_binding.py tests/ui/tools/test_ai_request_state_presentation.py -q --basetemp=.tmp-ai-ui-responsive-20261024/pytest-final-focused
```

最终扩大集合 **1901 passed、2 deselected、8394 warnings（192.69s）**。包含 slow 的 8300 条异步应用／保存诊断；仅排除此前已在基线确认失败的两个窗口尺寸布局节点，其测试文件和上一轮说明与本轮开始快照一致。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-ai-ui-responsive-20261024/uv-cache'
$env:FAISS_OPT_LEVEL = 'generic'
$env:PYTHONIOENCODING = 'utf-8'
$responsivenessTests = @(
    'tests/ai_translator', 'tests/application/translation', 'tests/contracts/translation',
    'tests/infra/test_llm_structured_outputs.py', 'tests/infra/test_openai_structured_outputs.py',
    'tests/infra/test_anthropic_structured_outputs.py'
) + @(rg --files tests/ui/tools | Where-Object {
    $_ -match 'test_(ai_|project_proofread|proofread_|unified_|task_|polish_|workflow_|optional_|run_request|shared_|translation_workflow)'
})
uv run pytest @responsivenessTests -q --disable-warnings -m 'not llm and not integration' `
  '--deselect=tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' `
  '--deselect=tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' `
  --basetemp=.qa-r16
```

首次在沙箱中运行相同集合为 1840 passed、61 failed、2 deselected（206.89s）。恢复测试在 `task_recovery.py:131` 的 `os.link` 处触发 WinError 5，并影响依赖记录初始化的 UI 测试；不修改生产原子发布规则规避权限。相同恢复文件在允许硬链接的环境下 18 passed（1.63s），随后上述整个集合在相同正常权限环境复验通过。
早期沙箱运行还输出过 Faiss 原生导入异常，不能将这些运行当作通过证据。

最终静态命令通过，2106 个文件格式无变化；初次检查发现新测试的一行超长和基准格式，已由 Ruff 规范化后复验：

```powershell
uv run ruff check src tests scripts/benchmark_ai_task_feedback.py scripts/benchmark_candidate_checkpoint.py scripts/benchmark_term_snapshot.py
uv run ruff format --check src tests scripts/benchmark_ai_task_feedback.py scripts/benchmark_candidate_checkpoint.py scripts/benchmark_term_snapshot.py
```

本轮目标文件的 `git diff --check` 通过，无暂存差异。未运行无关全仓测试和真实 API 测试。

## 兼容和限制

旧 schema 1 候选检查点仍可读取，读取不修改旧文件，首次成功保存迁移至 schema 2 的提交头加日志布局。
恢复、备份或移动必须保留头文件引用的日志；旧程序不能直接读取新布局。需要降级同一任务时，先在新版本通过 `load(run_id).to_dict()` 导出完整 schema 1 快照至独立目标，验证后再交给旧版本。
存储错误不会冒充接受成功；已完成正式提交但证据保存失败仍保留原有 partial 结果和错误诊断。

没有用户决策待办。真实 API 吞吐、语义质量及独立 LLM 日志查看器长期增长未纳入本轮验证。运行中的旧进程需要正常结束后重新启动，才会加载此次代码。

本轮完成后已校验并清理自有 `.tmp-ai-ui-responsive-20261024` 与 `.qa-r16`，基准汇总保留于本记录；未删除此前任务或用户目录。新增文档链接均可解析，索引中的既有历史链接不作为本轮修改目标。
