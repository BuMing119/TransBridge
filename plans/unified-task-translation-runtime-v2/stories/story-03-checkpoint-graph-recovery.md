# Story 03：CheckpointPort、Graph Workload 与幂等恢复

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR20.4/20.5、FR21.4、NFR1.3；ADR-011/019；R-031/R-032
- 依赖：S01/S02、I/O EntryKey

## 目标与验收

checkpoint 原子保存稳定身份、规格 fingerprint、frontier 和结果；重复恢复不重复提交；损坏/规格漂移明确拒绝；Graph pause/resume 保留 frontier/result，Graph 不拥有 Job 终态。

> 实现记录：[2026-08-18-001-原子 Checkpoint 与 Graph 恢复](../../../docs/changelogs/unified-task-translation-runtime-v2/story-03-checkpoint-graph-recovery/2026-08-18-001-原子Checkpoint与Graph恢复.md)。主线正式 uv 定向回归 137 passed，EvidenceManifest 复验有效；生产入口 wiring 仍由 S07 承接。

## 数据流与接口

workload safe point → `CheckpointRecord(schema, run_id, owner, spec_hash, input_hash, completed EntryKeys/actions, frontier, candidate refs, revision)` → CheckpointPort temp/write/validate/replace。resume → load/validate owner+hash → workload state rehydrate → TaskRuntime 新 run 或受控 continuation。GraphExecutor 被 `GraphWorkloadAdapter` 包装。

## 实施步骤

1. 定义 CheckpointPort 与 filesystem adapter，替换直接 `open(...,'w')` 和进程随机 hash。
2. 将 AI/PostProcess/Graph 数据迁至版本化 record，无法迁移旧 checkpoint 时报告而非当作无记录。
3. 修正 pause Event 方向；序列化 ready/running/completed frontier、branch decisions、loop counters、HITL state/result。
4. 恢复时已完成 commit id 集合去重，规格/输入变化拒绝。
5. checkpoint 生命周期由 TaskRuntime/owner 管理，Graph 只返回 workload outcome。

## 测试与边界

在写临时、fsync、replace、加载、节点完成、分支/HITL 各点注入崩溃；重复恢复两次验证提交一次；覆盖损坏 JSON、future schema、owner/spec/input mismatch。100k 更新 P95≤100ms，原始数据交 release S03。回退保留旧 checkpoint 只读导入器，不恢复非原子写。
