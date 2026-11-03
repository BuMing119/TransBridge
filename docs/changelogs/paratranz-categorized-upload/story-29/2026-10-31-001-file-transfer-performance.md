# S29：文件传输节奏、共享冷却与下载候选复用

日期：2026-10-31（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S29。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md#s29-文件传输性能与分段诊断)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[开发说明](../../../dev/paratranz-categorized-upload.md)。方案保持草稿，本地实现与验证完成。

## 原因与结果

分类文件数量放大请求数，文件网关原先按服务/账号每秒发起一次。现改为最小 0.5 秒发起间隔，HTTP 与 429 等待仍串行；官方每用户 480/min 是上限，0.5 秒只是保留余量的工程选择，未进行线上压测，也不是官方推荐值。预算仅在文件网关实例间共享，不声称所有 ParaTranz 客户端都进入同一预算。

429 冷却除持有账号槽位外还写入共享截止；等待任务取消或第三次 429 重试耗尽，也不使其他窗口提前请求。独立账号/endpoint 不互相冷却，耗时超过发起间隔的成功请求不会再附加 0.5 秒等待。

下载预览与发布曾各自从同一个不可变计划构造完整候选，包括远端索引、候选条目及状态。现在同一计划只转换一次；新计划即重算，本轮结束释放。发布仍读取当前权威状态、做三方比较、检查目标并按原许可提交。未改变文件布局、safe/force 导入、状态语义、未知 POST 不自动重放、404/413 恢复或保存规则。

## 可归属增量

启动时已有 S28 等大量未提交修改。本轮先保存目标文件当前内容和已有文档 diff；以下是相对启动内容的增量，不能把工作区全部相对 HEAD 的差异归入本轮。四个生产模块和两个已跟踪测试在启动时均无 diff；ADR、开发说明、计划和索引只做本条所述定点更新。未修改既有 changelog。

生产代码相对 `src/transbridge/`：

- 修改 `paratranz/file_publication.py`：`RequestBudget` 默认间隔 1→0.5 秒，增加共享冷却截止；网关对所有 429 保留截止，只重试明确拒绝且最多三次。记录预算/锁等待、成功与失败 HTTP、排队取消及 429 实际等待；仅输出固定类别、数字及有界状态，不输出错误正文或载荷。
- 修改 `application/categorized_upload/diagnostics.py`：增加 `timed_phase` 与 `log_timing`。计时诊断失败只输出固定告警，handler 也失败时用固定 stderr 消息；两个诊断通道均不可用时保留业务结果。不会让日志异常覆盖原错误或已提交保存的成功回执。
- 修改 `application/sync/project_download.py`：按同轮操作编号记录捕获、读取/解析、组装、匹配、预览、合入保存六个阶段；精确计划对象身份复用不可变候选，计划变化重建、discard 清理。没有缓存当前本地状态或另建跨轮缓存。
- 修改 `application/sync/download_merge.py`：发布使用会话的同计划候选；原 `merge_entry_states`、当前状态读取、目标核对和 commit guard 保持。

测试及工具：

- 新增 `tests/paratranz/test_file_transfer_timing.py`：假时钟验证默认间隔、HTTP/排队分离、失败/取消计时、三次 429、最终 429/取消后共享冷却、账号/endpoint 隔离、慢 HTTP、多窗口互斥及诊断失败隔离。
- 修改 `tests/paratranz/test_file_publication.py`：原 Retry-After 测试改用推进假时钟，覆盖真实共享冷却期限，不实际等 12 秒。
- 修改 `tests/contracts/paratranz/test_project_download_use_case.py`：新增同计划复用且同步期间编辑受保护、新计划重建/本轮释放、阶段顺序及无载荷计时、失败日志不改变业务错误、提交后日志失败仍返回成功回执。
- 新增 `scripts/benchmark_paratranz_transfer.py`：无网络/用户数据的合成计划五轮对照及实际文件网关的假时钟节奏模型。before 两次执行未改变的候选转换，after 通过真实会话方法复用一次结果；不是完整下载/保存基准。

文档：定点更新 ADR-047 当前请求预算及派生结果复用合同、开发说明的计时解释、计划 S29 及直接索引；新增本记录。生产模块均低于 500 行，下载编排 454 行/18 个方法，没有新增尺寸例外或依赖、lockfile/schema 变更。

## 性能证据

同台 Windows 11（10.0.26200）、Python 3.12.12、既有 uv 环境和锁定依赖；HEAD 为 `512e0b4ba4de458d281fe5d454d94f46df10fa96`，基线是启动时工作区内容。没有真实 API、LLM、用户工程或用户译文输入。

输入是 80 个来源、100,000 条合成词条，其中一个来源 65,000 条，其余 79 个分配剩余 35,000 条。所有词条都计划更新本地译文。计划创建单次 28.483 秒，仅作上下文，未进行五轮匹配前后对比或声称匹配提速。

候选转换：每轮按交替顺序执行 before/after，轮前 GC，构建计划及摘要核对均在计时外。五轮中位及范围：

- before（预览/发布各转换一次）：33.079 秒，32.581～33.234 秒；五次为 `[33.167858, 33.233834, 32.766998, 32.580987, 33.078963]` 秒。
- after（同计划转换一次并复用）：16.382 秒，15.910～16.613 秒；五次为 `[16.317931, 16.612914, 15.910156, 16.416478, 16.381706]` 秒。
- 该局部工作中位减少约 50.5%。十组均生成 100,000 个相同候选，完整 repr 顺序摘要 SHA-256 为 `cbbe6bfd0ec8be929f849e506078f6717b5bb70b836d7f8578d83591be052db1`。

文件网关节奏使用实际受控 requests.Session 和确定性时钟，每次成功 HTTP 固定 150ms：82 次请求从模拟 81.15→40.65 秒；4,971 次请求从模拟 4,970.15→2,485.15 秒。数量相同、无并发、无真实等待。该模型只是解释预算效应，不能预测真实服务耗时、错误率或端到端提升。初期启动排查中发生重叠的探索进程已终止，其数据未纳入上述最终五轮。

复测命令（输出目录须先创建）：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-paratranz-performance/uv-cache'
uv run --offline --no-sync --python .venv/Scripts/python.exe python scripts/benchmark_paratranz_transfer.py --output .tmp-paratranz-performance/comparison.json
```

测量只覆盖候选转换重复工作和预算模型，不含完整来源捕获、真实服务、匹配/预览其余工作、合入/保存或 UI 响应；未测峰值内存。候选 tuple 额外保留到本轮结束，受既有 100,000 条上限约束，以时间换同轮内存；本轮未调整该上限。

## 验证与独立审查

以下命令共用既有环境，设置任务独占 uv cache 和 `QT_QPA_PLATFORM=offscreen`。全仓检查及脚本检查通过：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-paratranz-performance/uv-cache'
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff check src tests
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff format --check src tests
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff check scripts/benchmark_paratranz_transfer.py
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff format --check scripts/benchmark_paratranz_transfer.py
```

Ruff 全部通过，src/tests 共 2,333 文件格式通过；新增脚本另行通过。`git diff --check` 对本轮目标通过。首轮只发现本轮样式问题，已定点修正，没有格式化无关文件。

聚焦：75 passed（4.09s），包括所有新增测试；首次系统临时目录权限导致七项 fixture setup error，使用独占 basetemp 复跑通过，不算业务失败。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/paratranz/test_file_transfer_timing.py tests/paratranz/test_file_publication.py tests/contracts/paratranz/test_project_download_use_case.py -q --basetemp .tmp-paratranz-performance/focused-pytest
```

扩大集合：453 passed、1 skipped、6 既有失败（42.11s）。真实账号/密钥测试按既有配置跳过。首次 sandbox 运行因本机回环连接 WinError10013 和任务文件原子 rename WinError5 出现 23 个额外失败/15 个 fixture error；同一命令获准执行本机回环和任务临时文件操作后消失，没有真实外部服务访问。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/paratranz tests/contracts/paratranz tests/application/projects/test_batch_download_sources.py tests/ui/operations/test_batch_download_session.py tests/ui/operations/test_batch_download_production.py tests/ui/operations/test_batch_download_autosave.py tests/ui/operations/test_download_autosave_boundaries.py tests/ui/operations/test_categorized_upload_retry.py tests/smart_assistant/tools/test_project_download.py -q --basetemp .tmp-paratranz-performance/regression-permitted --junitxml .tmp-paratranz-performance/regression-permitted.xml
```

补充集合：128 passed（15.05s），同样仅获准使用任务临时文件，不访问外部服务。与扩大集合不重叠，两组合计 **581 passed、1 skipped、6 既有失败**；聚焦集合是子集，不相加。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/application/categorized_upload tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_categorized_upload_feedback.py tests/ui/operations/test_file_upload_production.py tests/ui/operations/test_file_upload_input_scope.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_batch_download_results.py tests/ui/operations/test_download_automatic_recovery.py tests/ui/operations/test_download_workbench_projection.py tests/ui/operations/test_unified_download_service.py -q --basetemp .tmp-paratranz-performance/ui-regression --junitxml .tmp-paratranz-performance/ui-regression.xml
```

六项既有失败未混入本轮修复：

- `test_entry_upload_order.py` 两项：旧助手上传 fixture 没有打开工程版本，现行入口要求工程。
- `test_entry_upsert.py::test_assistant_upload_reuses_only_the_target_projects_known_identity[7/8]` 两项：fixture 的 SimpleNamespace 缺少 config。
- `test_file_transport_keys.py::test_identical_local_identity_is_still_rejected_without_post`：现行分类入口更早拒绝重复身份，测试尚未进入它预期的执行阶段。
- `test_batch_download_production.py::test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads`：上传 fixture 使用裸 object，缺 collection。

隔离复验：复制源码到独占临时目录，只在副本中将上述四个生产模块恢复为启动快照，以 `-o pythonpath=.tmp-paratranz-performance/baseline/src` 优先导入；traceback 显示该路径，网关间隔确认 1.0 秒。24 项为 18 passed、上述同六项失败（3.16s），未临时覆盖当前生产源码。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/paratranz/test_entry_upload_order.py tests/paratranz/test_entry_upsert.py tests/paratranz/test_file_transport_keys.py tests/ui/operations/test_batch_download_production.py::test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads -q -o pythonpath=.tmp-paratranz-performance/baseline/src --basetemp .tmp-paratranz-performance/baseline-pytest --junitxml .tmp-paratranz-performance/baseline.xml
```

独立 Sol high 只读审查发现计时日志可能覆盖已提交成功，以及最后一次 429 没有保留共享冷却；已修复并补聚焦回归。最终静态复核未发现新增问题；运行测试由实施会话统一完成。

## 边界与清理

未运行全量 pytest、真实 ParaTranz 上传/下载、真实账号压测、LLM、打包或发布；未提交 Git。默认 uv cache 首次因 sandbox 权限失败，改用任务可写 cache 后正常使用仓库现有 uv 环境，无依赖修改。任务独占 `.tmp-paratranz-performance`（源码快照、副本、uv cache、pytest 临时目录、XML 和原始 JSON）在核验归属后清理；数值及原始五轮结果已归纳于本文。用户原有 `.transbridge`、S28 文件和其他未提交修改保留。

无 schema/格式或用户数据迁移。真实服务速度、首次匹配/保存成本和峰值内存仍未验证；本地复用与节奏模型不替代线上验收。
