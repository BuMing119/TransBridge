# AI 日志句柄释放与 GUI 卡顿线程栈采集

- 日期：2026-10-23
- Epic / Story：maintenance / ai-runtime-diagnostics（直接维护，无独立 plan）
- 基线：`ee3a592`；只记录本次可归属的变更，不包含工作区其他功能修改。
- 背景：用户反馈 AI 翻译任务运行一段时间后越来越卡。前轮离线排查确认调用日志句柄随调用次数增长，但未取得无响应现场线程栈；本次修复资源累积并补充自动诊断，不宣称已确定原卡顿的全部原因。

## 行为与文件变化

- 修改 `src/transbridge/ui/tools/ai_translator/workflow_log_store.py`：新增 `close_channel()`，支持调用完成后释放文件；缓存最多保留 64 个打开文件，按最近写入淘汰。首次写入仍覆盖同名旧日志，淘汰或关闭后重新写入使用追加，保留流式和分组日志完整性。写入不可用时释放其余句柄，保留错误上下文；关闭后忽略迟到写入。
- 修改 `src/transbridge/ui/tools/ai_translator/workflow_logging_client.py`：四种普通/预备/流式调用共用调用通道生命周期，在成功、失败、准备异常、取消异常退出时释放文件；日志关闭失败不替换模型结果或原始异常。请求、响应、调用编号、错误脱敏及预算指标的原有格式保持不变。
- 修改 `src/transbridge/ui/tools/ai_translator/_translation_worker.py`：旧翻译线程的批次流日志复用有界 `WorkflowLogStore`，移除独立的无限增长文件句柄字典，保留原目录、批次文件名和原生线程收尾顺序。
- 新增 `src/transbridge/ui/hang_watchdog.py`：Qt 主线程每秒刷新心跳；连续 5 秒缺失心跳时，`faulthandler` 的原生看门狗自动写出 Python 线程栈。每次连续卡顿只采集一次，恢复后记录前后 UTC 时间和间隔，再重新布防。正常心跳不追加日志正文。
- 修改 `src/transbridge/ui/app.py`：主窗口显示后、进入事件循环前启动看门狗；`aboutToQuit` 以及入口 `finally` 都执行幂等关闭，先取消原生采样，再释放文件描述符。诊断目录或调度失败仅记录异常，不阻止 GUI 运行。
- 新增 `tests/ui/tools/test_workflow_log_lifecycle.py`：覆盖 1000 个通道的句柄上限、淘汰后追加、并发流写入、四种调用路径的成功/失败/取消、准备失败、分组日志及磁盘不可用时的释放。
- 新增 `tests/ui/test_hang_watchdog.py`：覆盖心跳续期、关闭顺序、轮转、初始化失败；隔离子进程运行真实 Qt 事件循环，分别模拟 Python 等待及 Windows `PyDLL` 持有 GIL 的原生阻塞，验证恢复前已采集、同次卡顿不重复、再次卡顿重采集、不写局部变量以及退出后不再写入。
- 修改 `tests/ui/tools/test_legacy_ai_worker_lifecycle.py`：日志失败测试转到共享文件存储边界，继续验证关闭失败不丢失成功结果及其余资源清理。
- 修改 `tests/integration/gui/test_ui_foundation_startup.py`：将自动诊断输出隔离到测试目录，验证 GUI 启动创建采样文件，保留现有 UI Foundation 故障降级与关闭顺序断言。
- 修改 `docs/changelogs/INDEX.md`：只增加本记录链接，保留已有条目。

## 下次无响应时如何取证

重新启动程序后自动生效，无需打开“LLM 日志”窗口。输出位于应用数据目录的 `log/ui-hangs/ui-hang-<UTC 时间>-<PID>.log`；从当前仓库启动时通常为 `data/log/ui-hangs/`。

文件头标明程序版本、PID、GUI 线程十六进制标识和阈值。出现 `Timeout (` 表示看门狗实际触发，随后列出线程的文件、函数、行号；恢复后追加上一次与恢复时的 UTC 心跳时间。只有文件头、没有 `Timeout (` 的日志表示未捕获到超时。

每次运行的活动文件达到 2 MiB 后，在下一次心跳安全轮转，保留 `.1.log`、`.2.log` 两个备份；单次完整线程栈允许超过轮转阈值。不会删除其他运行的诊断文件。诊断失败可在现有程序主日志中查找 `GUI hang watchdog`。

采样不写局部变量或源码正文，也不退出程序；它记录 Python 栈而非原生 C 栈，最多 100 个线程、每线程 100 帧。实现及描述符生命周期约束参照 [Python 3.12 faulthandler 文档](https://docs.python.org/3.12/library/faulthandler.html#dumping-the-tracebacks-after-a-timeout)。该定时采样设施为进程级资源，由 GUI 入口独占。调试断点、系统休眠或合法的长时间同步操作也可能造成心跳超时，应结合程序主日志判断。

## 验证

环境：Windows、Python 3.12.12、仓库现有 uv 环境；所有模型调用均使用离线替身，未安装依赖。uv 默认沙箱缓存目录不可写，改用本任务专用的仓库内临时缓存。

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tmp-ai-diagnostics-uv'
uv run --offline --no-sync pytest tests/ui/test_hang_watchdog.py tests/ui/test_logging_setup.py tests/ui/tools/test_workflow_log_lifecycle.py tests/ui/tools/test_workflow_logging_client.py tests/ui/tools/test_workflow_progress.py tests/ui/tools/test_live_llm_logs.py tests/ui/tools/test_legacy_ai_worker_lifecycle.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_unified_task_progress.py tests/smart_assistant/tools/test_polish_llm_runtime.py tests/integration/gui/test_ui_foundation_startup.py -q --basetemp=.tmp-ai-diagnostics-tests
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
```

- 首轮针对性回归：48 passed；最终扩展回归：**144 passed**（包含首轮用例，不累计），退出码 0。9 条警告来自现有兼容 API / SWIG 弃用提示。
- 全库 Ruff 与格式检查通过（2003 个 Python 文件）；首轮 Ruff 发现本次修改后残留的未使用测试导入，已移除并复验。
- 本次修改文件的 `git diff --check` 通过。
- 五轮资源对照：临时基准通过 `git show HEAD:<目标文件>` 加载修改前的日志存储与包装器，同一解释器中交替运行旧/新实现；每轮分别完成 3000 次合成普通调用，在关闭整项任务前统计仍打开的日志。旧实现每轮均为 **3000**，新实现每轮均为 **0**。新实现仍在写入的流式通道上限另由 1000 通道及并发写入回归验证。
- 基准命令：`uv run --offline --no-sync python .tmp-ai-diagnostics-benchmark.py`。输入为固定合成请求/响应，独立临时目录，无用户翻译数据。旧实现五次耗时为 1047.56、4010.08、3959.47、3770.16、4059.06 ms；新实现为 2856.02、4045.69、4250.41、4105.21、4360.06 ms，中位数分别为 3959.47 / 4105.21 ms。磁盘状态及同期测试负载存在波动，此结果只证明句柄释放，不作为吞吐提升或原始 UI 卡顿消失的证据。临时基准脚本、样本、缓存与测试目录在交付前清理。

## 兼容与未完成项

无需配置或数据迁移，翻译内容、日志格式和公共调用接口不变。没有修改 `uv.lock`、构建配置或安装包，没有提交或推送。

明确请求范围内无未完成项。未运行全仓 pytest、真实 LLM / 网络集成或发行包构建；尚未复现用户原任务的实际无响应，下一次发生时需要结合自动线程栈确定其他原因。
