# Story 06：TaskRuntime workload、进度、取消与 stale 屏障

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

把 build、publish、report 和 changelog 四类长任务接入现有 TaskRuntime，以 Project/Variant owner、互斥终态、取消 token、run permit 和业务 revision guard 阻止迟到或过期结果提交。

## 原始验收标准

- [ ] 注册 `terminology.build`、`terminology.publish`、`terminology.report.render`、`terminology.changelog.render`，owner 固定 Project/Variant，build JobSpec fingerprint 使用 `build_key`。
- [ ] 进度使用稳定业务阶段、完成/总来源或批次、当前对象、复用/重算数；LLM 提交/完成/等待/重试/耗时单独统计，连续 2 秒无计数变化仍有 heartbeat。
- [ ] 用户停止后 500ms 内可投影“正在停止”，立即停止补充来源/LLM 批次，并在 3 秒内进入用户可见 cancelled；不可中断调用只在隔离区清理。
- [ ] worker、fragment、LLM 和 renderer 的迟到结果在写入 BuildResult/draft/version/artifact ledger 前验证 cancellation token、run lease 和 expected revision。
- [ ] 构建结束与发布前重验 Project/Variant/source fingerprint/effective/base/draft/build freshness；变化结果为 stale 且不可发布。
- [ ] TaskRuntime execution terminal 与 BuildResult `completeness/freshness/llm` 质量维度分离。

## 当前实现事实

- `TaskRuntime.submit()/schedule()/update_progress()/cancellation_token()/commit_permit()/try_commit()`、`JobSpec`、`OwnerRef` 和 `JobState` 已提供统一运行时。
- `BoundedThreadPoolBackend(max_workers<=3)` 已由 composition 注入；新域不得自建业务 executor。
- `TaskProjectionReducer/TaskProjectionBinding` 可复用 owner filtering、revision/终态防倒退和幂等 close。
- `update_progress()` 会增加 runtime revision，使既有 permit 过期；正式 permit 必须在最后一次进度/heartbeat 后签发。

## 关键接口与运行顺序

- `workloads.py`：计划新增 typed workload request/result、`TerminologyWorkloadType`、稳定 phase 与 `TerminologyProgress`。
- `runtime.py`：计划新增 `TerminologyTaskEntrypoint`、`TerminologyRunLease`、`TerminologyCommitGuard`、`ProgressHeartbeat`。
- `ui/tools/terminology/task_adapter.py`：只把 TaskEvent 转为 UI projection。

```text
capture build key/ref -> JobSpec + OwnerRef(project, variant)
  -> scheduled workload
  -> token checks + progress/heartbeat
  -> run-scoped/CAS staging
  -> revalidate all business baselines
  -> final progress, then commit permit
  -> short repository expected-revision transaction
  -> one TaskRuntime terminal
```

## 实施步骤

1. 定义四类 workload、请求/结果、phase enum 和 owner/context 校验；Project/Variant 对术语任务均必填。
2. build JobSpec fingerprint 使用 build key；publish/report/changelog 使用对应 immutable ref/digest。
3. 实现进度 accumulator 和 2 秒 heartbeat，进度 payload 保持 flat JSON scalars；LLM 统计单列。
4. 在 parse/component/LLM/render/publish 安全点检查 token，取消后停止 refill；不可中断结果只进入隔离 staging。
5. 在最后进度后签发 permit，同时由 repository 验证 Project/Variant/source/effective/base/draft/build freshness。
6. stale/partial/LLM 状态属于业务结果；TaskRuntime 仍只使用 completed/failed/cancelled。取消不冻结正式 `BuildResultRef`。
7. composition 注册 workloads/use cases 和 close 生命周期；UI adapter 不继承 QThread，不拥有业务状态。

## 文件与测试

计划新增 `application/terminology/workloads.py`、`runtime.py`、UI `task_adapter.py` 和对应 application/contract/UI tests；修改 `bootstrap/composition.py`。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_workloads.py tests/contracts/terminology/test_runtime.py tests/ui/tools/terminology/test_task_adapter.py -q
uv run pytest tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/integration/bootstrap/test_task_runtime_wiring.py -q
```

## 边界、风险与回退

- 未实现真实 pause safety point 前不得声明 pause capability。
- heartbeat 不得创建无限或私有线程池；使用 composition 提供的 scheduler/clock。
- permit 内只执行短最终事务，避免 runtime 全局锁被大 SQL 写入占用。
- 外部调用可在 cancelled 后隔离清理，但迟到数据不得投影或提交；回退时禁用术语 workload 注册，不改变现有 runtime。
