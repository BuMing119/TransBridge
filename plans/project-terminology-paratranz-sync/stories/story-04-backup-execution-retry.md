# Story 04：单向备份执行、逐项结果与安全重试

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.1、FR5.17.6；FR22.2～FR22.4；ADR-019
- **前置依赖**：[Story 03](story-03-three-way-sync-planner.md)
- **下游调用方**：S05 双向执行、S07 任务入口、S08 故障演练

## 目标

执行已授权且仍 fresh 的 `BACKUP` 计划，把已发布本地版本按逐项、可取消、可恢复的方式备份到显式绑定目标。远端副作用、durable outcome 和 baseline 推进必须可判定；网络未知结果先 reconcile，任何执行都不修改本地 draft/effective version。

## 原始验收标准

- executor 只接受已授权且 fresh 的 backup plan；执行前重新验证 ADR-023 目标、binding revision、账号/成员权限、本地 version digest、远端 snapshot 和 baseline revision。
- create/update/delete 逐项记录 succeeded/failed/skipped/unknown、remote ID/revision、request ID 和安全诊断；部分成功不伪装为完整成功。
- baseline 只在能证明远端副作用的 item 上前进；timeout/断线导致结果未知时先 reconcile，再决定重试，绝不直接重复 create/delete。
- retry token 绑定 line、plan hash、owner、目标和已确认 outcome；重试跳过已成功项，并在远端已变化时返回 stale/replan。
- 取消后不再启动新请求，迟到结果只能进入 reconcile 状态；当前本地已发布版本和 draft 始终不变。

## 当前可复用能力与限制

- `application.sync.ParaTranzSyncExecutor`、`RetryToken`、`SyncItemOutcome` 和 `ParaTranzSyncTaskEntrypoint` 已证明 translation-entry sync 的 partial/retry/cancel 模式，但其 UoW 会替换本地集合，不可直接复用。
- `ConfirmationAuthority`、`OperationResult`、`TaskRuntime`、`TaskRuntimeCommitGuard`、typed `ExternalServiceError` 可直接复用公共合同。
- `ParaTranzTargetResolver` 解析目标但执行前仍需 `get_project`/成员权限验证；浏览状态和 `AppContext.paratranz_project_id` 不能参与。
- S02 SQLite 是远端事务之外的本地 durable journal；必须按“记录 intent/outcome → reconcile → 推进 baseline”缩小崩溃不确定窗口。

## 执行状态机

```text
AUTHORIZED
  → FRESHNESS_CHECKED
  → ITEM_STARTED
      → CONFIRMED_SUCCESS ─→ durable outcome/link/baseline CAS
      → CONFIRMED_FAILURE ─→ durable failure
      → UNKNOWN ───────────→ NEEDS_RECONCILE
      → CANCELLED ─────────→ stop scheduling
  → COMPLETED | PARTIAL | FAILED | CANCELLED | RECONCILE_REQUIRED
```

一条 item 的 baseline 只能在 confirmed success 或 reconcile-confirmed 时推进。`UNKNOWN`、本地 outcome 写失败或 baseline CAS conflict 都不得被包装成普通 failure 后自动重发。

## 计划新增的关键接口

- `AuthorizedTerminologySyncPlan`：plan、owner、confirmation code；只由 S03 authorize 创建。
- `TerminologySyncItemStatus`：`SUCCEEDED`、`FAILED`、`SKIPPED`、`UNKNOWN`、`CANCELLED`、`RECONCILED`。
- `TerminologySyncItemOutcome`：item/action/status/code、remote identity/revision/request ID、安全 message、attempt。
- `TerminologySyncRetryToken`：line/target/plan/owner、confirmed item IDs、unknown item IDs、outcomes、token digest。
- `ExecuteTerminologyBackupRequest`：authorized plan、exact target/binding/local/baseline expectations、run ID、cancellation、retry token。
- `TerminologyBackupExecutor.execute()` 与 `reconcile()`：同步执行和未知结果核对。
- `TerminologySyncTaskDraft`/`TerminologySyncTaskEntrypoint`：TaskRuntime 生命周期 adapter，不实现业务规则。

## 依赖有序的实施步骤

1. 在 `execution_models.py` 定义冻结 outcome/retry token 和 canonical item ID；token 校验 plan/owner/target/合法 item 集，保持序列化无 secret。
2. 在 executor 开始时验证 authorized plan hash、mode=BACKUP、owner 和 confirmation；拒绝 BIDIRECTIONAL 误入。
3. 重新通过 Project lifecycle/ADR-023 resolver 取得当前 target，调用 typed project service 验证 endpoint、账号、项目存在和权限；比较 binding revision。
4. 重读 exact local terminology version、remote stable snapshot、sync profile/baseline；调用 S03 freshness helper 比较所有 digest/revision。失败返回 stale，不发写请求。
5. 对 retry token 中 confirmed item 生成本次 skipped outcome；unknown item 先进入 reconcile，读取 remote ID或按安全 identity 查找，不直接 create/update/delete。
6. 按 plan 稳定顺序执行可写 item，每次请求前检查 cancellation。`LOSSY/SKIP/CONFLICT/BLOCKED` 不进入 remote port。
7. create：传入 stable client attempt ID 供本地 journal；S01 若无服务端 idempotency 能力，timeout 记 UNKNOWN。update/delete 必须 remote ID 和 expected revision/digest仍匹配。
8. 每次获得 confirmed response 后，在 S02 单事务中追加 item outcome、更新 link并 CAS baseline；如果本地 commit 失败，将远端已成功事实记为需要 reconcile，不重复远端操作。
9. 遇到 typed confirmed failure继续还是停止由错误类别决定：认证/授权/目标变化停止后续；单项 validation/conflict 可 partial；429/暂不可用遵守 S01 retry 后仍失败则停止有界批次。
10. 取消后不再启动新 item；正在进行请求若返回成功，先 durable 记录 confirmed result，再把剩余 item标记 cancelled，整体结果为 PARTIAL/CANCELLED而非丢失成功。
11. 生成 `OperationResult` counts、diagnostics、retry token/reconcile intent；secret、raw response 和用户 term 内容不进入普通日志。
12. 用 `TerminologySyncTaskEntrypoint` 提交 TaskRuntime，job fingerprint=plan hash，metadata 只含 mode/plan/line；commit permit保护本地 journal/baseline mutation。

## 文件变更清单

- **新增** `src/transbridge/application/terminology_sync/execution_models.py`、`executor.py`、`task_adapter.py`。
- **更新** `src/transbridge/application/terminology_sync/ports.py`：执行所需 target/local snapshot/state transaction ports。
- **更新** `src/transbridge/persistence/terminology/sync_state.py`：durable item/run outcome、CAS baseline/link、reconcile query。
- **更新** `src/transbridge/bootstrap/terminology.py`：只组合 executor/task adapter，不增加业务规则。
- **新增** `tests/application/terminology_sync/test_backup_executor.py`、`test_task_adapter.py`。
- **新增** `tests/contracts/paratranz/test_terminology_sync_execution.py`：受控 remote success/partial/unknown/reconcile。

## 边界条件与错误处理

- 计划 mode 不是 backup、plan hash损坏、confirmation缺失/重放或 owner变化：执行前失败。
- target/binding/account/project权限/local version/remote snapshot/baseline/profile任一变化：stale/replan；不得部分按旧 plan执行。
- create timeout 后列表出现唯一相同 canonical item：reconcile confirmed并建立 link；零个或多个匹配仍 unknown/conflict。
- update/delete timeout 后 remote ID内容/存在性符合预期：reconcile success；状态不确定时不能重复。
- baseline local commit 在远端成功后失败：整体至少 PARTIAL/RECONCILE_REQUIRED，不能报告远端失败。
- cancellation 与 response 同时发生：以实际可证远端结果记 outcome，取消只阻止后续调度。
- 独立 remote、plugin lossy、未确认 delete 无 remote write call。

## 测试策略与建议命令

- fresh/authorization：所有 expectation逐一变化、token owner/hash/重放、错误 mode。
- remote execution：create/update/managed delete、第二次相同备份零写、独立 remote零写。
- faults：401/403/409/429/5xx、timeout-before/after-commit、transport断线、取消 before/during/after response。
- durability：outcome append失败、baseline CAS conflict、进程重启加载 retry token、unknown reconcile、confirmed item不重复。
- TaskRuntime：owner隔离、cancel、late completion、terminal guard、result parity。
- 建议命令：`uv run pytest tests/application/terminology_sync/test_backup_executor.py tests/application/terminology_sync/test_task_adapter.py tests/contracts/paratranz/test_terminology_sync_execution.py -q`。

## 风险、回退与未决问题

- 远端 API 与本地 SQLite 无分布式事务；正确性依赖 durable outcome + reconcile，而不是假装原子。
- 批量并发会扩大未知结果窗口；首版按稳定顺序或小型有界批次执行，性能优化不能越过 per-item outcome。
- 回退可停止新任务并保留 journal/baseline；不得删除 unknown/reconcile 记录或把 raw bulk import 作为替代。
