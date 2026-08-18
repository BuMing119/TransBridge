# Story 03：原子 Checkpoint 与 Graph 恢复

- 日期：2026-08-18
- Epic/Story：`unified-task-translation-runtime-v2/S03`
- 追溯：FR20.4/20.5、FR21.4、NFR1.3、ADR-011/019、R-031/R-032
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增版本化、owner/run/spec/input 四重身份校验且深冻结的 CheckpointRecord；损坏 JSON、future schema 与旧非原子格式均明确拒绝。
- FilesystemCheckpointPort 采用同目录临时文件、文件 fsync、回读校验和原子 replace；同进程 per-run 锁拒绝 revision 回退及相同 revision 不同内容。
- commit-id 集合固定重复恢复去重语义，直接构造和反序列化都严格拒绝 bool/非整数 revision、空标识及非规范嵌套结果。
- Graph 保存 ready/running/completed frontier、result、branch、loop 与 HITL；节点、分支、循环和确认决策在故障注入前建立持久安全点。
- pause 改为 Condition 真实阻塞，resume/cancel 可唤醒；显式同 run 幂等恢复，相同 Graph 不同 run 隔离，legacy 无身份调用每次生成独立 identity。
- GraphWorkloadAdapter 消费 cancellation 与 checkpoint identity，只返回 workload outcome，不调用 TaskRuntime 的 completed/failed/cancelled 终态 API。

## 验证证据

- 主线正式 uv 定向回归：137 passed，覆盖 checkpoint 合同、Graph 恢复、ExecutionEngine 与真实 Agent tool chain；12 个 warning 为既有 SWIG/直接 mutation 迁移提示。
- 子任务全仓回归：844 passed、1 skipped；37 个 warning 为既有 deprecation。
- Task/Checkpoint 新增范围 Ruff 与 format 门禁通过。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/task-runtime-s03/qa-20260818T073803.376366Z-84ed3aa63e5f/manifest.json)：`passed`，schema/verdict/hash 复验有效。
- 未执行 Git commit/push。

## 剩余门禁

Windows 无可移植目录句柄 fsync，当前保证文件 fsync 与同卷原子 replace；revision 锁只覆盖同进程，未引入跨进程锁。旧 checkpoint 仅只读识别不自动恢复；生产入口 wiring 由 S07 承接，无法中断的外部调用仍遵循 S02 协作式取消边界。
