# Project Session Persistence V2 / Story 04：Session Aggregate 与 Owner 隔离

- **状态**：已实现并增量验证通过
- **追溯**：FR19.5～19.7；ADR-018；R-025/R-026

新增完整 Session Aggregate、不可变快照、owner/run/revision/request-hash 门禁、pending approval 与 degraded recovery；SessionController 改为显式 transition table，历史 messages-only facade 明确 degraded。GUI 权威投影与真实 V2 store 接线由 S05 承接。

锁定 uv：83 passed；[evidence](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/persistence-s04/qa-20260818T103636.857132Z-1182a44af11f/manifest.json) 已 verify。
