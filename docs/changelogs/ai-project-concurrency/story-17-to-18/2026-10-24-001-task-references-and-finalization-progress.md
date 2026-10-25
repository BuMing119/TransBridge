# 任务参考复用与项目收尾进度

- 日期：2026-10-24
- Epic/Story：AI 多插件并发与项目共享术语，S17～S18
- 依据：[实施计划](../../../../plans/ai-project-concurrency/reference-snapshot-finalization-plan.md)、用户明确授权 bm-pilot 实施并补齐项目收尾进度。
- 归属：以本轮开始保存的源码、测试、文档 ZIP 基线核对实际差异。工作区此前已有大量未提交修改，不归入本记录；未提交 Git、未修改用户项目、未操作运行中的程序。

## 问题与结果

失败项重试在重新加载术语后，因 `TaskConsistency.observe_terms` 比较完整术语内容而抛“术语内容已变化，请新建任务”。参考内容因此变成了任务和结果应用的硬门槛。项目收尾还会重复读取参考，长时间扫描期间 UI 只有“来源流程 2/2”，AI 补充校对没有接入批次进度。

现在任务首次加载的公共参考、来源基线和已发布项目术语固定并复用；恢复使用独立、带校验和的参考文件。参考变化不拒绝有效译文、结果应用或失败项重试。本任务生成术语照常共享；外部生成文件冲突只拒绝新增术语发布并记录诊断，不覆盖文件，不把成功译文变成失败。原文、身份、项目、配置及修订保护保留。

项目收尾按来源显示加载、扫描、AI 校对、整理与完成，扫描限频并支持条目边界暂停/取消。顶部展示当前阶段，扫描和模型批次进度通过已有后台与有界反馈路径交付。

## 逐文件变化

所有路径相对于仓库根目录；未列出的既有工作区改动不属于本轮。

### 参考与写入边界

- 新增 `src/transbridge/ai_translator/task_reference_snapshot.py`：不可变输入/来源快照，原子持久化、身份与校验和验证；首次来源读取失败时采用的缓存或空表及诊断也一起冻结。非共享动态库在任务内复用实例，避免外部新增术语混入后续阶段。
- 新增 `src/transbridge/ai_translator/project_reference_snapshot.py`：序列化/恢复已发布决定、作用域、版本和 echo links，不保存配置凭据。
- 新增 `src/transbridge/ui/tools/ai_translator/task_references.py`：按逻辑任务和项目目录组合存储；恢复只认当前清单 task_id，origin_task_id 仅追溯，不能让不同任务共写参考。
- 修改 `src/transbridge/ai_translator/term_database.py`：可选参考依赖接线，将原有来源读取/缓存回退交给独立捕获模块；参考快照 IO 错误不进入普通缓存回退。
- 修改 `src/transbridge/ui/tools/ai_translator/task_consistency.py`：移除实时参考版本、内容与文件签名的应用/重试门槛；读访问只序列化视图，写访问独立检查拥有权。保留任务与配置身份，保存确认仍检查实际持久化内容。
- 新增 `src/transbridge/ai_translator/term_publication_conflict.py`：外部写入冲突的专用异常，不混同存储故障。
- 新增 `src/transbridge/ai_translator/dynamic_term_transaction.py`：非共享动态写入持锁，失败时恢复原对象字段、列表及查找代次；不回写磁盘。
- 修改 `src/transbridge/ai_translator/generated_term_writer.py`：仅捕获明确的发布冲突并跳过写入，保留警告/共享冲突证据；普通异常仍传播。非共享 reload/save/ack 使用内存事务。
- 修改 `src/transbridge/ai_translator/shared_term_pool.py`：外部文件变化和已知写锁占用转专用冲突；保存/确认前后校验，竞态时恢复本任务内存而不覆盖外部字节。未发布术语保留临时诊断。

### 执行与后台启动

- 修改 `src/transbridge/ai_translator/translator.py`：只接可选参考存储和独立写访问，不增加翻译主控职责。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：同任务各尝试复用参考和共享池，后台捕获发布参考；翻译/校对传递同一依赖。
- 修改 `src/transbridge/ui/tools/ai_translator/proofread_composition.py`：校对 manager 读取同一来源快照。
- 修改 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：名称 manager 复用基线，删除纯读取时的共享文件比较；发布冲突不丢弃名称结果，记录实际新增数量。
- 修改 `src/transbridge/ui/tools/ai_translator/run_request_preparation.py`：可选恢复的发布参考，保持项目/Variant 身份检查。
- 修改 `src/transbridge/ui/tools/ai_translator/task_launch.py`：完整参考恢复在后台 inspect 阶段执行；新建及重新开始不读取旧任务快照。

### 收尾进度

- 修改 `src/transbridge/ui/tools/ai_translator/source_terms.py`：收尾复用捕获参考，明确加载阶段，禁用收尾不需要的向量初始化，删除逐条文件检查，传递 pause_event。
- 修改 `src/transbridge/ui/tools/ai_translator/task_term_closure.py`：扫描计数/待校对数/已评估数，限频反馈；暂停取消边界及无差异完成状态；未扫描条目取消时保留译文供接续。外部发布冲突使用明确的“未写入新增参考；译文保留”诊断。
- 修改 `src/transbridge/ui/tools/ai_translator/source_reference_review.py`：接入真实 ProofreadStage 批次进度，报告补充校对提交前后的整理状态。
- 修改 `src/transbridge/ui/tools/ai_translator/task_progress_presentation.py`：来源结束后切换项目收尾汇总，展示当前来源与实际阶段，不提前显示全任务完成。
- 修改 `src/transbridge/application/translation/postprocess_progress.py`：新增对应阶段标签。

### 回归与文档

新增测试：

- `tests/ai_translator/test_task_reference_snapshot.py`：多来源/阶段共享输入、缓存回退一致性、跨进程恢复、不可变性、损坏与落盘错误。
- `tests/ai_translator/test_term_publication_conflict.py`：外部写入、锁占用、保存/确认竞态、非共享内存回滚及真实 IO 异常传播。
- `tests/ui/tools/test_ai_task_frozen_references.py`：后台恢复、新任务隔离、旧记录兼容、历史 origin 不影响当前逻辑任务归属。
- `tests/ui/tools/test_ai_task_closure_progress.py`：扫描阶段、无差异、暂停/取消与 2 万条 Qt 反馈合并。
- `tests/ui/tools/test_project_reference_large_task.py`：两个来源共 2 万条、1000 个术语的真实本地匹配及 Qt 心跳；公共参考只读取一次，收尾不重新读取或无故调用模型。

修改测试：`tests/ai_translator/test_shared_term_pool.py`；`tests/ui/tools/` 下的 `test_ai_partial_task.py`、`test_ai_project_name_preparation.py`、`test_ai_reference_review_stage.py`、`test_ai_task_consistency.py`、`test_ai_task_progress_presentation.py`、`test_ai_task_term_closure.py`、`test_ai_task_term_transactions.py`、`test_ai_task_term_writes.py`、`test_generated_term_roundtrip.py`、`test_shared_task_term_consistency.py`。旧的参考变化必失败断言替换为保留结果/只拒绝冲突写入；配置、原文和当前译文变化仍拒绝应用。

更新 canonical 文档 `docs/adr/048-ai-project-concurrency-and-terms.md`、`docs/dev/ai_translator.md`、`plans/ai-project-concurrency/plan.md`；新增本轮实施计划，并最小更新 changelog 索引。

## 验证

环境：Windows，仓库 uv 环境，Python 3.12.12；无网络、无真实 LLM、无用户数据写入。

相关集合 **2101 passed、2 deselected**：

```powershell
$env:UV_CACHE_DIR = "$PWD/.tmp-ref-final-20261024/uvcache"
uv run --no-sync pytest tests/application/translation tests/ai_translator tests/contracts/translation tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology -q -m 'not llm and not integration' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -o faulthandler_timeout=60 --basetemp=.qrf2
```

两项排除项是此前确认的窗口布局失败。首次沙箱运行 2012 passed、87 failed：其中 81 项受 Windows 硬链接权限拒绝影响，6 项是需更新的术语硬失败旧断言；修正断言并以正常文件权限复验上述集合通过，没有改变持久化安全实现来绕过测试。

最后参考缓存回退、逻辑任务隔离及非共享回滚补强后，**360 项专项通过**（与上述集合重叠，不相加）：本轮变更的 15 个测试文件（除大型匹配文件）加上 `test_full_task_recovery_ui.py`、`test_task_recovery_binding.py`、`test_task_background_records.py`、`test_run_request_preparation.py`、`test_ai_task_launch_background.py`、`test_ai_task_session.py`、`test_translation_task_resume.py`、`tests/ai_translator/test_term_database.py`、`test_term_lookup_cache.py`，使用 `uv run --no-sync pytest <上述文件> -q --basetemp=.qrf4`。一次文件名误写导致 0 项收集，纠正后完成本次验证。

全库：`uv run --no-sync ruff check src tests`、`uv run --no-sync ruff format --check src tests` 均通过（2127 文件）。

## 大任务证据

先用真实 AiTaskWorker 和 Qt 控件跑 2 万条合成反馈五次：中位 0.234 秒，范围 0.203～0.250 秒；最大 Qt 心跳间隔 78ms，206 条生产进度被合并成 6～8 次渲染，缓冲最多 2 条。该场景使用固定参考 resolver，只验证反馈路径。

随后使用真实 TermDatabaseManager/TaskReferenceStore/共享池/来源收尾匹配，两个插件各 1 万条、1000 个术语，在后台扫描并测量 GUI 心跳：

```powershell
uv run --no-sync python .agents/skills/bm-perf/scripts/benchmark_command.py --runs 5 -- .venv/Scripts/python.exe -m pytest tests/ui/tools/test_project_reference_large_task.py -q --basetemp=.qrflg2
```

五轮扫描时长 10.369、10.256、10.260、10.294、10.215 秒，中位 **10.260 秒**；Qt 最大心跳间隔依次 45.3、45.1、42.6、42.8、49.1ms。每轮 210 条限频进度；公共来源只读取一次，收尾零次重新读取，无需校对的条目不调用模型。完整 pytest 命令中位 12.484 秒，范围 12.428～12.787 秒。

这是当前版本的离线初始测量，不是前后性能对照，也不能替代真实 API 时延或真实语义质量验证。此前与整套回归并行的一次扫描 26.929 秒因负载不同不用于比较。

## 兼容、风险与责任审查

- 老恢复记录没有完整参考时，首次继续重新捕获一次；不猜测历史内容、不丢弃有效翻译候选。新参考文件损坏明确报错，原文件保留。
- 项目共享池沿用原生成术语持久化；无项目的旧动态库本轮支持同进程生成更新复用，未新增跨进程自生成增量日志。
- 真实 IO/磁盘损坏仍可能阻止执行，不能伪装成普通参考差异；仅专用外部发布冲突被降级。
- `translator.py` 1637 行、`term_database.py` 962 行，仅做可选依赖接线与既有加载替换；新职责位于独立可测模块。`task_term_closure.py` 534 行，仍是已有收尾职责的局部修复；后续扩展前应提取完整来源检查切片并回归合并、报告和取消。
- 没有修改正在运行的程序进程。代码在下次启动时生效；未运行付费模型、未对用户已有任务自动重试或应用结果。
- 本轮专用临时基线、日志、缓存和 pytest 目录交付前统一核对绝对路径后清理；既有用户文件保留。
