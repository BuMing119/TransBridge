# S16：统一自动翻译与术语的执行器及退出生命周期

- 日期：2026-11-13
- Epic/Story：project-terminology-build-versioning-reporting / S16
- 用户要求：统一、一致。
- 关联：[计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 最终行为

以现有自动翻译的资源生命周期为准：停止新工作、取消排队任务与模型请求、等待正在执行的请求释放回调和并发额度，再释放客户端并退出。术语抽取与冲突判断不再采用上一增量的可放弃独立请求池。取消后的响应不写入术语结果或缓存。

自动翻译原有依赖链、公平调度、暂停与错误隔离保持；冲突仍为每组独立请求且最多 50 并发，抽取继续使用已有配置上限。共享客户端取消时去除固定三秒等待、证据 SQL 中途取消以及保存保护保留。

## 文件变化

- 新增 `src/transbridge/application/translation/batch_execution.py`：完整迁移既有 BatchExecutor、BatchExecutionSnapshot 及内部调度状态至公共应用层；增加独立单批次 `submit` 返回标准 Future，和原 `run` 使用同一工作线程、队列调度和 shutdown。异步批次完成后回收运行状态，关闭取消未开始 Future，默认等待活动批次。
- 修改 `src/transbridge/ai_translator/batch_execution.py`：保留 Quest 链规划，将原公共类型从新模块重新导出。此兼容入口保留既有调用和测试的导入 API；核心执行器没有复制第二份。
- 修改 `src/transbridge/application/terminology/extraction_batches.py`：用公共 BatchExecutor.submit 保留现有完成顺序、缓存和进度处理；finally 中先阻止新批次，再取消请求，最后等待工作线程。即使 cancel 抛异常，也等待活动请求收尾。
- 修改 `src/transbridge/infra/terminology_conflict_judge.py`：使用同一执行器与安全收尾顺序；保留取消异常和取消失败诊断。
- 删除上一增量新增的 `src/transbridge/application/translation/request_pool.py` 和 `tests/application/translation/test_request_pool.py`，移除“后台请求不阻塞进程退出”的独立策略及对应测试。
- 新增 `tests/application/translation/test_shared_batch_execution.py`：证明旧导入和应用层是同一个类、关闭取消排队任务而保留活动任务所有权、独立 Future 失败不影响另一提交、完成后清理运行状态。
- 修改 `tests/application/terminology/test_extraction_batches.py`：增加取消成功/取消失败两种情况下，抽取调用均等到提供方返回才结束且不缓存迟到结果。
- 修改 `tests/infra/test_terminology_shutdown.py`：将“不等待提供方”的旧断言改为与自动翻译一致的资源保留断言，同时覆盖 cancel 抛异常。
- 更新计划当前策略与 changelog 索引；此前两条增量作为历史保持原样。

## 验证

命令：

```powershell
uv run --no-sync --cache-dir .tmp-unified-close/uv pytest tests/application/translation/test_shared_batch_execution.py tests/ai_translator/test_batch_execution.py tests/application/terminology/test_extraction_batches.py tests/infra/test_terminology_shutdown.py tests/infra/test_terminology_conflict_judge.py tests/ui/tools/test_ai_translation_cancel_lifetime.py tests/ai_translator/test_translation_rounds.py tests/ai_translator/test_translator_pause_control.py tests/ui/tools/test_ai_task_feedback.py tests/ui/test_runtime_task_shutdown.py tests/integration/bootstrap/test_terminology_conflict_judgment.py tests/integration/bootstrap/test_terminology_llm_build.py -q --basetemp .tmp-unified-close/regression --tb=short
```

98 passed，包含自动翻译既有取消生命周期测试；此前聚焦 62 项通过。集成项目 fixture 在获准的正常权限环境验证。

现有环境 `.venv/Scripts/ruff.exe check src tests`、`format --check src tests` 通过，2598 文件格式一致。源码和测试搜索确认已无 ReadOnlyRequestPool 或 translation.request_pool 残留。临时目录核对绝对路径后清理。

## 边界

无数据格式或配置迁移，不修改用户数据。远端若不响应取消，两边现在都会等待请求结束，因此不承诺立即退出；此取舍保持请求资源、回调、额度与保存生命周期一致。未执行真实 API、全仓测试或打包程序整体退出时长测量。未提交 Git。
