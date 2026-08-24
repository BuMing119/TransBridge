# Story-03：任务能力矩阵、活动投影、历史与恢复合同

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **需求**：FR26.2、FR26.9、FR20、FR21、NFR1.6
- **架构**：ADR-019、ADR-021

## 目标与验收边界

原样承接 plan S03：先证明每类任务真实拥有的控制、checkpoint、artifact、日志和重试能力，再提供统一只读投影。该 Story 不把 legacy worker 自动宣布为 TaskRuntime workload，也不改变任何业务终态。

## 当前事实

- `TaskRuntime` 拥有进程内 `_jobs`、状态迁移、owner 校验、capability control、事件订阅和 commit permit。
- `JobCapabilities` 仅声明 pause/resume/cancel/checkpoint；artifact、log、retry/navigation 尚无统一展示合同。
- `CheckpointPort` 已按 Run ID、JobSpec digest、owner、input fingerprint 和 schema 校验恢复。
- AI/FOMOD/Workbench/ParaTranz 仍存在 QThread/ApiWorker；Smart Assistant 的 compatibility `TaskManager` 已可绑定 composition runtime。
- TaskRuntime 进程内记录不是跨重启任务历史；Session recovery 遇不到 runtime JobRef 时会降级为不可恢复。

## 交付一：任务能力矩阵

新增 `task-capability-inventory.md`，每类任务至少记录：

- 权威 owner 与当前执行 backend；
- JobSpec/RunSpec 是否不可变、是否有 Run ID；
- pause/resume/cancel/stop/checkpoint；
- progress/diagnostic/log/artifact/result navigation；
- retry 是否安全、所需预检与 idempotency key；
- 重启后恢复条件；
- legacy adapter owner、迁移 Story、退出条件。

没有证据的单元格记为“不支持”，不能写“待 UI 实现”。

## 计划新增合同

- `TaskActivityViewState`：run_id、owner scope、job type、display context、state/revision、progress、available actions、diagnostic/artifact refs、recoverability reason。
- `TaskActionAvailability`：由 snapshot state + JobCapabilities + checkpoint/artifact/retry ports 派生；View 不自行判断。
- `TaskHistoryRecord/Port`：持久化不可变 terminal event 摘要和安全引用；不提供 `set_state()`。
- `RecoveryCatalog`：枚举 checkpoint metadata 并调用 `CheckpointRecord.validate()`；输出 recoverable/unrecoverable reason。
- `TaskRetryIntentRegistry`：按 job_type 接受旧记录和当前 context，重新预检后提交新 JobSpec；返回新 JobRef。
- `TaskResultNavigator`：artifact/navigation intent 到 Shell/Workbench/ParaTranz/output facade 的映射，不直接持有窗口。
- `LegacyTaskProjectionAdapter`：必须注入 OwnerRef、Run ID/generation 和 close；只声明 worker 实际发出的能力。

全局任务中心若需要跨 owner 列表，使用显式 `tasks:manage` actor；普通 feature 仍按 same_scope 查询。

## 事件顺序

```text
TaskRuntime event / bounded legacy event
  -> TaskProjectionBinding (owner + run_id + revision guard)
  -> immutable TaskActivityViewState
  -> task center / progress facade

terminal event -> append immutable history summary
checkpoint catalog -> validate identity -> recovery availability
retry intent -> re-preflight -> new JobSpec -> new Run ID
```

历史写失败不得改变任务终态；只产生诊断并允许当前会话继续显示 runtime snapshot。

## 实施步骤

1. 完成能力矩阵和 legacy 退出清单，先确认“不支持”的行为。
2. 扩展 application task contracts，仅加入展示所需的不可变 activity/history/recovery/retry ports。
3. 实现 history adapter 与 checkpoint recovery catalog；敏感路径/Prompt/secret 不进入历史摘要。
4. 实现 Qt-free projection reducer，按 sequence/revision 拒绝重复和乱序事件。
5. 实现 UI Binding 和 manager actor 注入；关闭时释放 subscription，不轮询 TaskRuntime.list。
6. 为各 job_type 注册 result navigator/retry intent；未注册则不显示对应动作。

## 文件与测试

- 新增：`src/transbridge/application/tasks/activity.py`、`history.py`（或等价内聚模块）
- 新增：`src/transbridge/ui/presentation/task_projection.py`
- 修改：`application/tasks/__init__.py`、bootstrap composition、UI shell composition
- 新增：`plans/guided-ui-workflows/task-capability-inventory.md`
- 新增：task activity/history/recovery/retry/authorization contracts 和 UI lifecycle tests

重点测试：终态不可逆、retry 新 Run ID、checkpoint digest/owner/schema mismatch、manager/feature actor 隔离、乱序/重复事件、历史损坏/写失败、100 次订阅创建销毁、无 polling。

## 回退与风险

任务中心/历史 adapter 可关闭而不停止底层任务；原进度 facade 保留到对应 S08/S09 迁移验收。禁止让 legacy adapter 和 TaskRuntime 同时提交同一终态。历史保留周期、数量和清理策略需在实现时依据现有数据目录预算固定为版本化配置，不能无界增长。
