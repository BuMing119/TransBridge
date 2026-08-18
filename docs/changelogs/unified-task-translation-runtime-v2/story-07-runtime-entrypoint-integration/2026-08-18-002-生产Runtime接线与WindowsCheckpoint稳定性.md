# Story 07：生产 Runtime 接线与 Windows Checkpoint 稳定性

- 日期：2026-08-18
- 状态：综合 QA 通过

## 增量

- GUI 的兼容 `TaskManager` 绑定 Composition Root 的同一 `TaskRuntime`；状态、进度、终态均从 `JobSnapshot` 投影。
- long-running 工具把实际 `JobRef/run_id` 送入 SessionController；旧 session 的迟到事件不再推进当前状态。
- `wait_terminal` 超时改为结构化超时，不伪造 cancelled；Session gate 只接受当前 active session。
- checkpoint 使用稳定路径锁键、revision high-water mark 和 Windows WinError 5/32 有界原子替换重试。
- 取消延迟探针覆盖两条取消竞态路径，相关压力用例连续 10 轮通过。

## 证据

- 锁定 uv：146 passed。
- EvidenceManifest：`qa-evidence/task-s07/qa-20260818T131148.537899Z-eba50a5ee88c/manifest.json`，verify 为 `VALID (passed)`。

