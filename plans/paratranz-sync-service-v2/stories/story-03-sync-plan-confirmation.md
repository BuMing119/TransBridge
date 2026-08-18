# Story 03：Sync Plan、Dry-run 与显式确认

- 所属 Plan：[ParaTranz Sync Service V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR22.3、FR18.3/18.4；ADR-017；R-042
- 依赖：S02、I/O S02/S03、platform S04

## 目标与验收

上传/下载/合并先产生无副作用计划；新增、更新、冲突、跳过、删除可检查；覆盖/删除必须确认，远端或本地 snapshot 变化使确认失效。

## 数据流与接口

local snapshot + remote snapshot → mapper（EntryKey/ExternalEntryRef）→ `SyncPlanner` → immutable `SyncPlan(plan_id, input hashes, items, counts, conflicts, destructive)` → adapter 展示 → `ConfirmationToken(owner, plan_hash, expiry)` → execute。计划 item 含 action、before/after摘要、remote ref、reason/conflict policy。

## 实施步骤

1. 实现纯 SyncPlanner，禁止网络写/repository mutation；拉取 remote snapshot 本身只读。
2. 明确 upload/download/bidirectional operations 和 conflict policies，不以数组顺序合并。
3. plan canonical serialize/hash，ConfirmationToken 绑定 owner/operation/hash/过期时间。
4. GUI/Agent/MCP 复用 DTO，各入口只能改变展示；Agent/MCP 破坏性操作同样要求 token。
5. execute 前重取必要 revision/hash；变化返回 stale_plan。

## 测试、边界与迁移

golden fixtures 覆盖新增/更新/冲突/跳过/删除、无 id/重复 id、远端变化、token 重放/跨 owner/过期；spy 断言 dry-run 无写入。大计划只分页展示但完整 plan hash/项目数量保留。
