# 助手结果补记恢复与依赖失败收尾

- 日期：2026-09-23。
- Epic/Story：maintenance / assistant-task-failure-recovery；用户通过 bm-pilot 授权修复本次审查确认的两个 Major。
- 关联：[ADR-040](../../../adr/040-assistant-user-request-lifecycle.md)。
- 范围：只记录本轮符号级修改；工作区既有的会话路由、上下文、撤销及其他未提交改动不归属于本增量。

## 行为变化

后台结果保存失败不再凭历史 diagnostics 永久阻止所有请求。按 Session/request/run 跟踪未补记结果，保留原终态快照；事件处理最多保存三次，请求变化或后台调度唤醒时每个待补记 run 再试一次。成功后清除对应阻断，重复或并发补记不重放业务操作。错误历史保留；结果保存后的通知异常独立记录。

前置事项失败或取消后，在关联操作已收尾的条件下，将其尚未执行的依赖后继递归标为 FAILED，记录 dependency_failed:<item_id>。独立事项继续；已完成、运行中及未知结果不被提前覆盖。必要事项均收尾后请求进入失败终态。旧版停滞请求在恢复或点击继续时也能收敛。界面显示未执行数量及前置失败原因。

## 文件修改

- 修改 `src/transbridge/application/assistant_requests/task_events.py`：分离保存与通知；新增按请求查询待补记状态、保留终态快照、有限补记、并发互斥及同会话通知重入保护。
- 修改 `src/transbridge/smart_assistant/request_execution.py`：仅将 `_validate` 的历史 diagnostics 全局阻断改为当前 Session/request 的未解决故障判断；其他既有修改不计入本轮。
- 修改工作区既有未跟踪文件 `src/transbridge/ui/tools/smart_assistant/request_turn_selection.py`：在后台选择模型轮次前尝试补记当前会话的待保存结果。
- 修改 `src/transbridge/application/assistant_requests/reducer.py`：`converge` 增加依赖失败传播，`resume` 补做收敛；原有本轮归属逻辑保留。
- 修改 `src/transbridge/ui/tools/smart_assistant/request_list_view.py`：失败事项不再误投影成活动等待；增加“未执行：前置事项失败”说明。
- 修改 `tests/application/assistant_requests/test_request_reducer.py`：增加链式依赖失败、独立事项继续、失败收尾与恢复、运行中/未知结果保护测试。
- 修改 `tests/smart_assistant/test_request_execution.py`：增加真实 Session/TaskRuntime 故障注入，覆盖暂时和持续保存失败、跨请求隔离、终态快照补记、通知异常、父计划并发补记与业务操作不重复。
- 新增 `tests/ui/tools/smart_assistant/test_request_dependency_display.py`：验证依赖失败说明及独立事项不误显示等待。
- 修改 `docs/adr/040-assistant-user-request-lifecycle.md`：在原状态收敛及保存失败合同处更新规则与边界。
- 修改 `docs/changelogs/INDEX.md`：新增本条索引。

## 验证

最终联合命令（退出码 0）：

```powershell
uv run pytest tests/application/assistant_requests tests/application/assistant_context tests/smart_assistant tests/ui/tools/smart_assistant tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/integration/bootstrap/test_task_runtime_wiring.py tests/ui/tools/test_unified_task_runtime.py -q --disable-warnings
```

结果：1353 passed，65 warnings，149.88 秒。相较修复前同范围增加 9 个测试案例。此前审查中偶发失败的确认许可释放测试本轮联合运行通过，未修改该测试或对应行为。

聚焦命令：`uv run pytest tests/smart_assistant/test_request_execution.py tests/application/assistant_requests/test_request_reducer.py tests/ui/tools/smart_assistant/test_request_dependency_display.py -q --disable-warnings`，38 passed。

- `uv run ruff check src tests`：通过。
- `uv run ruff format --check src tests`：通过，1410 文件。
- `git -c core.safecrlf=false diff --check`：通过。

中间验证：最初直接运行虚拟环境 pytest 因共享临时目录权限出现 setup 错误，使用获准的 uv 命令完成验证。新增“任务历史不可读取”故障注入最初延续到 fixture 清理，导致两项 teardown 错误；已改为局部 monkeypatch 上下文。最后一次联合测试基于最终实现与修正后的测试，全部通过。

## 兼容与限制

- 沿用原状态及字符串原因字段，无存储 schema 迁移；终态记录不重写，明确继续已失败任务仍使用既有后继请求流程。
- 补记队列位于当前进程内存；持久存储持续不可用时保持阻断，不谎报成功。进程退出后沿用结果未知的保守核对机制，不保证跨进程自动补记。
- 其他请求仍受实际存储可用性及资源占用约束，不承诺存储故障期间可任意执行写操作。
- 未运行全仓测试、付费真实模型或远端写操作；本次不涉及模型协议或供应商变更。
- 不新增后台无限重试线程，不自动重新执行失败的翻译、导出或上传。
- 未提交或推送 Git；本任务未创建需手动清理的临时目录。
