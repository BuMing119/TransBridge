# S16：提取与冲突浏览期间的退出等待

- 日期：2026-11-13
- Epic/Story：project-terminology-build-versioning-reporting / S16
- 关联：[计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 问题

冲突判断的 ThreadPoolExecutor 上下文在取消时仍等待请求结束；抽取虽然使用 shutdown(wait=False)，Python 解释器退出仍会 join executor 请求线程。共享 OpenAI/Anthropic cancel 另外主动等待活动请求最多三秒。冲突面板只在查询前后检查取消，不能中断正在执行的证据统计。

## 本轮文件变化

- 新增 `src/transbridge/application/translation/request_pool.py`：ReadOnlyRequestPool 复用既有 ThreadBackend(daemon=True)、标准 Future 和 Queue，按需启动、限制工作线程数，关闭后拒绝提交并取消未开始请求。仅用于无持久化写入的模型请求，结果仍由任务线程校验、缓存和提交。没有改通用任务后端或写入关闭策略。
- 修改 `src/transbridge/application/terminology/extraction_batches.py`：抽取请求使用该池，保留原有并发上限、缓存成功批次与取消检查。
- 修改 `src/transbridge/infra/terminology_conflict_judge.py`：使用同一池；取消时不等待未响应请求，取消排队 Future 并中断客户端；客户端取消失败记录诊断且不覆盖原取消异常。仍为每冲突一请求、最多 50 并发。
- 修改 `src/transbridge/bootstrap/terminology_conflict_judgment.py`：移除重复取消 watcher 及 join，由判断协调器统一处理，保留 raw.close 收尾。
- 修改 `src/transbridge/infra/llm_client.py`：两个提供方 cancel 去除三秒轮询；锁内切换新传输后立即关闭旧传输，关闭异常可见。该大模块只做原取消方法的局部修复，没有增加职责；新请求池放独立模块。
- 新增 `src/transbridge/persistence/terminology/read_cancellation.py`：受仓储锁保护的只读 SQLite progress handler 和逐行检查；取消转为既有 QueryCancelled，退出作用域恢复 handler，真实 SQL 错误继续传播。不得用于写入事务。
- 修改 `src/transbridge/persistence/terminology/conflict_reads.py`：证据与次数查询支持 cancellation，并在 SQL 及逐行解码中检查。
- 修改 `src/transbridge/ui/tools/terminology/conflict_evidence_panel.py`：把既有后台读取 token 传入上述查询；关闭窗口继续通过既有 runner 取消，不另建 UI 线程池。
- 新增 `tests/application/translation/test_request_pool.py`：排队取消、正在运行结果、关闭后拒绝提交，以及阻塞 60 秒请求不妨碍子进程退出（subprocess 超时 5 秒）。
- 新增 `tests/infra/test_terminology_shutdown.py`：活动请求传输取消不等待三秒、无响应模型下判断协调器仍结束；新用例断言取消在 0.75 秒等待界限内发生，不代表整个软件退出时间。
- 新增 `tests/persistence/terminology/test_read_cancellation.py`：SQL 中途取消、连接随后可用、SQL 错误不吞掉。
- 修改 `tests/infra/test_terminology_conflict_judge.py` 和 `tests/ui/tools/terminology/test_conflict_evidence_panel.py`：补齐测试客户端取消接口、查询 token，并验证关闭窗口取消正在进行的次数读取。
- 更新本 Story 计划和 changelog 索引；不改上一条历史记录。

## 验证

使用现有 uv 环境，统一参数 `--no-sync --cache-dir .tmp-term-close/uv`：

1. `pytest tests/application/translation/test_request_pool.py tests/application/terminology/test_extraction_batches.py tests/infra/test_terminology_shutdown.py tests/infra/test_terminology_conflict_judge.py tests/persistence/terminology/test_read_cancellation.py tests/persistence/terminology/test_conflict_reads.py tests/ui/tools/terminology/test_conflict_evidence_panel.py -q --basetemp .tmp-term-close/focused-final --tb=short`：52 passed。
2. `pytest tests/infra/test_llm_client_prompt_cache.py tests/infra/test_limited_llm_client.py tests/infra/test_llm_reasoning.py tests/infra/test_llm_reasoning_protocols.py tests/infra/test_anthropic_reasoning.py tests/infra/test_provider_compatibility_integration.py tests/integration/bootstrap/test_terminology_conflict_judgment.py tests/integration/bootstrap/test_terminology_llm_build.py tests/ui/tools/terminology/test_save_close.py tests/ui/tools/terminology/test_window_lifecycle.py -q --basetemp .tmp-term-close/regression --tb=short`：116 passed。
3. `.venv/Scripts/ruff.exe check src tests`、`format --check src tests`、`git diff --check`：通过；2598 个文件格式一致。

初次运行有一个测试替身遗漏新 cancellation 参数，修正后复验通过。另一轮沙箱导入 FAISS 原生模块异常，未计为通过；上述最终两组在正常权限环境运行。临时目录核对绝对路径后删除。

## 兼容性与边界

无数据迁移，不删除术语结果，不改变保存确认与数据库事务收尾。取消后的远程服务可能仍继续计算；本地不提交迟到结果。后台请求只允许做模型调用和内存结果处理，不能移入数据库、缓存或文件写入。未测真实服务或打包程序整体退出耗时；长时间来源解析、必要事务和其他模块的退出等待不在本轮证明范围内。
