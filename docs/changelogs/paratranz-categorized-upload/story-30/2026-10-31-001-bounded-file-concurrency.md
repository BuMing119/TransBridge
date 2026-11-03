# S30：上传与下载最多五路并发

日期：2026-10-31（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S30。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md#s30-最多五路文件传输)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[开发说明](../../../dev/paratranz-categorized-upload.md)、[S29](../story-29/2026-10-31-001-file-transfer-performance.md)。

## 行为与取舍

用户在S29交付后明确要求最多同时并发5个。现有文件上传和下载可以实际重叠独立文件请求；同一进程、同一服务/账号的默认文件网关共享最多5个在途请求与0.5秒最小发起间隔，重试同样计入。官网不推荐批量并发，用户明确选择此受限方案；不是官方建议或线上验证值。短于0.5秒的请求可能不增加吞吐，不保证跑满五路。

上传每文件仍按创建/原文→译文顺序执行，可能共享413分片路径的文件按输入顺序调度。每任务独立session并关闭；自定义单session而无factory保持串行。工程/目标检查通过队列交回原worker，进度和来源回调也不从新文件线程直接触碰Qt。

401/403/未恢复429立即阻止后续发起；取消/停止可退出排队和429等待，账号冷却截止不撤销。已发出HTTP等待原超时，不强行中断或重放未知POST；确认成功的响应完整解析，保留已创建/已导入事实，再停止下一阶段。

下载最多5个response future，原worker按来源及文件次序完整验证/计数。失败来源不合入前缀，重复ID与100,000条上限保持。404先排空旧代再刷新重建，已消费并解析的同路径/ID继续复用；未消费的旧代前瞻可能重读。

## 相对S29交付后的增量

已保存本轮起始文件及Git diff，只记录下列新增内容；S29历史及已有S28/用户修改保留。没有提交Git。

- 修改 `src/transbridge/paratranz/file_publication.py`：账号共享五槽和既有起始节奏，独立session fork；发起前取消/停止，响应后保留确认事实；即时全局拒绝通知及可停止的429等待。其他 `ParatranzClient` 调用不改。
- 修改 `src/transbridge/paratranz/file_transport.py`：新增请求准入取消适配，区分排队/本轮停止与正在处理的响应。
- 修改 `src/transbridge/application/file_upload.py`：有并发能力的网关接入有界调度；404刷新通过共享inventory原子替换，其他自定义端口保持原路径。
- 新增 `src/transbridge/application/file_upload_parallel.py`：最多5个文件任务，原worker检查队列，同分片根输入顺序、互斥刷新及独立创建身份覆盖、原序回执/补传输入。
- 修改 `src/transbridge/paratranz/batch_download_snapshot.py`：并发前瞻接线、来源结束/失败释放；原匹配/合入与完整性规则保持。
- 新增 `src/transbridge/paratranz/parallel_file_reads.py`：有界响应future、同源与跨源读取、全局失败即时停止、404换代排空重建、构造失败与关闭清理。
- 新增 `tests/paratranz/concurrent_file_support.py`、`test_concurrent_file_publication.py`、`test_parallel_file_downloads.py`：21项受控并发回归，真实阻塞send观察五路在途、跨网关账号隔离、session不共享/全部关闭、0.5秒间隔、在途取消事实、未知POST、403/429、413顺序、inventory竞态、404缓存/换代、来源失败/上限/重复ID、owner线程及构造清理。
- 修改 `tests/paratranz/test_file_publication.py`、`test_file_transfer_timing.py`：显式单槽排队兼容测试及共享冷却假时钟适配，不依赖默认串行。
- 修改 `scripts/benchmark_paratranz_transfer.py`：新增 `--concurrency-demo` 实际上传协调器的本地延迟响应演示；原S29候选基准保持可用。
- 定点更新需求FR3.4.5、ADR-047、开发说明、S30计划和直接索引；新增本记录，不修改S29增量。

生产模块最大311行，最大新增主类10个方法，无尺寸例外；无依赖、lockfile、schema或持久缓存变更。

## 验证

环境：Windows 11、Python3.12.12、既有uv环境/锁定依赖、Qt offscreen。只用合成session、本机回环fixture和任务临时工程，不访问真实服务或用户项目。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-paratranz-concurrency/uv-cache'
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/paratranz/test_concurrent_file_publication.py tests/paratranz/test_parallel_file_downloads.py -q --basetemp .tmp-paratranz-concurrency/concurrent-final
```

新增21项全部通过（4.50秒）；首轮相关旧回归129项通过。扩大集合与补充集合不重叠，共 **602通过、1真实账号测试跳过、6既有失败**。前两组为扩大集合子集，不重复相加。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/paratranz tests/contracts/paratranz tests/application/projects/test_batch_download_sources.py tests/ui/operations/test_batch_download_session.py tests/ui/operations/test_batch_download_production.py tests/ui/operations/test_batch_download_autosave.py tests/ui/operations/test_download_autosave_boundaries.py tests/ui/operations/test_categorized_upload_retry.py tests/smart_assistant/tools/test_project_download.py -q --basetemp .tmp-paratranz-concurrency/regression --junitxml .tmp-paratranz-concurrency/regression.xml
uv run --offline --no-sync --python .venv/Scripts/python.exe pytest tests/application/categorized_upload tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_dialog.py tests/ui/operations/test_categorized_upload_feedback.py tests/ui/operations/test_file_upload_production.py tests/ui/operations/test_file_upload_input_scope.py tests/ui/operations/test_upload_mode_routing.py tests/ui/operations/test_batch_download_results.py tests/ui/operations/test_download_automatic_recovery.py tests/ui/operations/test_download_workbench_projection.py tests/ui/operations/test_unified_download_service.py -q --basetemp .tmp-paratranz-concurrency/ui-regression --junitxml .tmp-paratranz-concurrency/ui-regression.xml
```

两命令获准使用本机回环HTTP和任务文件原子rename，结果分别为474通过/1跳过/6旧失败（47.20秒）、128通过（13.22秒）。六项与S29相同：旧助手上传fixture无工程2项、缺config2项、重复身份提前拒绝1项、裸object缺collection1项。引用S29的隔离起始源码复现证据；本轮未再次建立baseline或修复无关失败。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff check src tests
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff format --check src tests
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff check src tests scripts/benchmark_paratranz_transfer.py
uv run --offline --no-sync --python .venv/Scripts/python.exe ruff format --check src tests scripts/benchmark_paratranz_transfer.py
```

全部通过，src/tests2338文件格式通过，含脚本组合2339文件通过。独立Sol high只读审查发现并修正迟到取消丢确认事实、413全局停止时机、分片相关文件抢锁次序、构造取消清理及长429等待不响应stop；最终静态复核未发现未解决问题。测试由实施会话统一运行。

## 受控演示

实际执行器、真实线程协调和独立requests.Session，send返回本地合成响应，无socket。6个单条文件，列表10ms、每次文件POST固定0.8秒；双方最小发起间隔0.5秒，串行与最多5并发交替各五次。准备在计时外，调用上传执行器至完整回执在计时内。

```powershell
uv run --offline --no-sync --python .venv/Scripts/python.exe python scripts/benchmark_paratranz_transfer.py --concurrency-demo --output .tmp-paratranz-concurrency/concurrency-demo.json
```

- 串行五次秒数：`[5.311549, 5.329617, 5.332663, 5.327789, 5.313536]`；中位 **5.327789秒**，范围5.311549～5.332663。
- 最多5并发：`[3.832156, 3.828781, 3.826880, 3.827644, 3.832885]`；中位 **3.828781秒**，范围3.826880～3.832885，中位减少28.1%。
- 每轮均7次HTTP、6文件成功；实测峰值1→2，因0.8秒响应与0.5秒节奏只需两路重叠。五路实际重叠/上限由阻塞单测另行证明，没有声称此演示跑满5或预测线上速度。

未重复S29十万条CPU基准，未测峰值内存。前瞻future有界，仍会同时持有若干完整响应；真实服务、429概率、完整保存/UI性能未验证。

## 收尾边界

未运行全量pytest、真实上传/下载、真实账号压测、LLM、构建或发布。无跨进程配额/路径原子保护承诺。任务独占 `.tmp-paratranz-concurrency`（起始快照、uv cache、pytest/JSON/XML）在核验归属后清理；本地受控证据已归纳于本文。用户原有临时文件和未提交修改保留。
