# Story 07：Task Monitor、Session/Agent/GUI 集成与兼容删除门禁

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR20.7、FR17.1/17.4；ADR-019；R-028～R-030/R-047
- 依赖：S01～S06、persistence S04、platform S05

## 验收记录（2026-08-18）

- 综合 QA：生产入口绑定同一 runtime，Session 以真实 JobRef/run_id 门禁迟到事件；checkpoint Windows 原子替换与并发 revision 已收口。最新 task-s07 EvidenceManifest `qa-20260818T131148.537899Z-eba50a5ee88c`，146 passed。
- 63 passed（`tests/contracts/tasks` + task runtime/checkpoint 核心回归）；EvidenceManifest [task-s07](../../../docs/test-reports/requirement-code-review-2026-08-18/qa-evidence/task-s07/qa-20260818T110604.244537Z-52110446aece/manifest.json) 通过 verify。
- 新增 `application/tasks/entrypoints.py`（RuntimeTaskBridge/SessionJobGate/TerminalOutcome）、`application/tasks/projection.py`（只读投影 + capability 控制）；兼容清单与删除门禁见 `docs/dev/task-runtime-compat.md`。

## 目标与验收

生产入口取得 JobRef；AWAITING_TASK 可达；Session/Monitor/报告终态一致；旧会话迟到事件被拒绝；Monitor 只读且按钮按 capability；旧 facade parity 通过。

## 事件流、接口与调用方

GUI/Agent/MCP use case submit → JobRef 绑定 Session/Project owner → SessionController 进入 AWAITING_TASK → Task events 经过 owner filter → TaskMonitor projection/入口 response 更新 → terminal outcome 生成报告/Session transition。QThread worker 只转 backend progress。

## 实施步骤

1. 迁移 translator/postprocess/Graph 工具返回 Deferred JobRef，修复 `handle_task_started/completed` 生产连接。
2. TaskMonitorWidget 接收 JobSnapshot projection，移除 reset/set_status 权限；action 调 runtime control。
3. SessionController 用 JobRef/run_id 匹配完成事件，切换后旧事件仅审计。
4. Agent/MCP 将 JobSnapshot/OperationResult 序列化为公共 schema；GUI 文案从同一 snapshot 派生。
5. 建立旧 TaskManager/MixedWorker/Graph API 调用清单、parity 证据和删除门禁；本 Story 不直接删除公开入口。

## 测试、边界与回退

端到端覆盖 GUI/Agent/MCP 提交、pause capability、cancel、failed、partial outcome、Session 中途切换、shutdown 和 Monitor dispose；断言同一 run_id 的终态/diagnostic 一致且无 writable mirror。兼容 facade 可回退调用适配，但统一 Job 状态不可回退。
