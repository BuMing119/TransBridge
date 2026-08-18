# Story 02：Typed ParaTranz Client 与错误/重试合同

- 所属 Plan：[ParaTranz Sync Service V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR22.2、FR17.3；ADR-016/019；R-041/R-042
- 依赖：S01、platform S02、TaskRuntime cancel token

## 目标与验收

Agent 所需操作通过真实 port/API 实现；认证、授权、冲突、限流、超时、服务错误和取消有稳定类型；非幂等写不盲重试，item diagnostic 保留远端引用。

## 数据流与接口

typed request DTO → `ParaTranzPort` → HTTP adapter → response schema validate → typed DTO 或 `ExternalServiceError(category, status, request_id, retry_after, safe_context)`。RetryPolicy 根据 method/idempotency key/category 决定；CancellationToken 在请求前、退避中和响应后检查。

## 实施步骤

1. 按项目/文件/string/term/export 能力定义小型 ports，不让工具选择具体 API subclass。
2. 统一 `ParatranzClient._request` 的 timeout/status/body/schema/error 映射；移除 print token。
3. 修复 tool_paratranz 调用不存在的 `get_entries/upsert_entry/get_upload_history`，映射到已验证 API 或明确 unavailable。
4. GET/安全幂等操作支持有界指数退避和 Retry-After；写操作仅有 idempotency/业务确认时重试。
5. 记录 request correlation，不记录 secret/完整敏感 payload。

## 测试、边界与迁移

受控 HTTP server 覆盖 2xx、畸形 JSON、401/403/404/409/429/5xx、timeout、断线、cancel；断言重试次数/退避上限/非幂等零重试。旧 API class 保留 adapter，工具迁移后 parity 测试真实方法签名。
