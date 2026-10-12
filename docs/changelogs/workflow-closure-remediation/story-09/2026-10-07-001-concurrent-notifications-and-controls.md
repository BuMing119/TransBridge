# 修复回执恢复、会话收尾与 AI 控制的并发竞态

- 日期：2026-10-07。
- Epic / Story：workflow-closure-remediation，S09。
- 计划：[跨模块流程闭环整改](../../../../plans/workflow-closure-remediation/plan.md)。
- 结果：三项审查问题已修复，新增 26 项正式回归；助手组合 1312 passed、1 deselected，AI/runtime 组合 163 passed，全库 Ruff 通过。
- 归属：以本轮开始时的目标文件副本与最终文件对照，保留 S01～S08 和 purpose-aware-data-projection 等既有未提交改动；本记录仅涵盖下列并发修复。未 commit/push。

## 逐文件变更

- 修改 `src/transbridge/application/assistant_requests/step_execution.py` 的 `StepReceiptWriter.retry_pending`：通知写入按会话去重的有序队列，由一个执行者依次处理。处理期间的跨会话或同会话通知保留为后续一轮；队列为空与释放执行权在同一锁内完成。内部记账锁不跨存储 I/O；失败不会自行入队，不会重放业务操作。
- 新增 `tests/smart_assistant/test_step_receipt_concurrency.py`，6 项：双会话通知、同会话通知、同步重入、持续故障有界重试、快照后新增回执、序列化入口失败后的恢复。真实 Session/Request/StepReceipt 持久化验证回执唯一及业务不重放。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_binding.py` 的 `release`：捕获原 context、view 和 turn generation，迟到成功/失败仅在原轮次仍当前且未被替代时影响 UI；旧错误不再停止新会话/新轮次。关闭时主动清理确认等待项。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_background.py`：增加可选 `settled` 清理钩子，在交付的 `finally` 中执行，不受旧上下文、关闭或业务异常分支的过滤影响；不改变其他调用方的缺省语义。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_confirmation_binding.py`：增加按原 turn 丢弃等待项和关闭清理的方法，将正常确认交付与无条件资源清理分开。
- 新增 `tests/ui/tools/smart_assistant/test_release_concurrency.py`，11 项：切会话、换轮次、重复中断、关闭及当前轮次的成功/失败对照，另含真实 `interrupt_steps → StepReceiptWriter.interrupt → save` 两次存储故障的会话隔离测试。
- 新增 `src/transbridge/ui/tools/ai_translator/worker_controls.py` 的 `SerialAiWorkerControls`：在一个串行边界内接纳 snapshot.sequence、更新绑定并调用 worker；旧快照不能覆盖新状态。同线程信号重入先记 pending，原 worker 方法返回后再处理，避免递归获取 worker 的普通锁。unbind/close 与实际动作共用控制锁。
- 修改 `src/transbridge/ui/tools/ai_translator/task_adapter.py`：将 worker/run 控制委托给上述组件；调用 runtime 或控制组件前释放 adapter 自有锁，避免 adapter/runtime/control 之间形成反向锁依赖。
- 新增 `tests/ui/tools/test_ai_runtime_control_order.py`，9 项：晚到事件、实际动作交错、绑定旧快照、unbind/close、旧 worker 解绑、同步信号重入和并发取消锁序。
- 更新对应 plan 和三处索引，仅登记 S09 状态与本记录，不修改历史增量。

## 验证证据

修复前，回执首批 4 项中 3 failed、1 passed；release 首批 10 项中 9 failed、1 passed；AI 首批 5 项全部失败。修复后相关分组分别 52 passed、58 passed、70 passed，release 补真实回执故障后专用文件 11 passed。

最终助手组合：

```text
uv run pytest tests/application/assistant_requests tests/smart_assistant tests/ui/tools/smart_assistant -q -m "not llm and not integration and not slow" -p no:cacheprovider --basetemp .tmp-concurrency-fix-integration/assistant --disable-warnings
```

结果：1312 passed、1 deselected、72 warnings，113.27 秒。首轮因指定的 basetemp 父目录不存在导致 453 项夹具初始化错误（859 passed），补齐父目录后以同一命令重跑全部通过；不是业务失败。

最终 AI/runtime 组合：

```text
uv run pytest tests/ui/tools/test_ai_runtime_control_order.py tests/ui/tools/test_ai_runtime_commit_boundary.py tests/ui/tools/test_unified_task_progress.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_translation_workflow_closure_audit.py tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/contracts/tasks/test_task_center_actions.py tests/ui/test_task_center.py -q -p no:cacheprovider --basetemp .tmp-concurrency-fix-integration/ai --disable-warnings
```

结果：163 passed、8358 warnings，12.11 秒。为核验警告，再将 `--disable-warnings` 替换为 `-W default`、basetemp 改为 `.tmp-concurrency-fix-integration/ai-warnings`：163 passed、55 warnings，11.04 秒；均为 SWIG 类型或原有测试直接修改词条的弃用警告，未出现线程异常警告。

- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：1567 个文件通过。
- `git -c core.safecrlf=false diff --check`：通过。
- 独立只读交叉复核 release 确认交付与 AI 锁顺序，未发现新增可达问题。

## 兼容与验证范围

无存储格式或公开任务 API 迁移；回执恢复仅补写结果，不重新执行业务。暂停、恢复、取消仍以 TaskRuntime 为权威。第三项是存量公共 API 多线程竞态，未将其描述为普通 GUI 单线程操作必现。

本轮未重复全库测试、真实远端 API 或长时间压力测试；使用 Windows 上的离线 Qt/线程/持久化测试。无已知未解决的本轮缺陷。本批临时测试、缓存及实施前副本在收尾时清理，正式回归测试保留。
