# Story 05：独立 MCP/CLI/GUI Adapter 与入口等价基线

- 所属 Plan：[Platform Contract Foundation V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR17.1/17.5、NFR6.1；ADR-012/016；R-004/R-007/R-047
- 前置依赖：S01～S04

## 目标与原始验收

MCP 在 Windows 上作为独立 stdio 进程握手、列工具和关闭，不依赖 GUI 内存；至少一个无状态能力和一个需上下文能力通过 GUI/Agent/MCP 语义等价测试。

> 实现记录：[2026-08-18-001-独立 MCP 与入口等价基线](../../../docs/changelogs/platform-contract-foundation-v2/story-05-entrypoint-parity-mcp/2026-08-18-001-独立MCP与入口等价基线.md)。正式 uv 证据为 36 passed，包含安装态 Windows console-script 真实 stdio 成功链；仍待下游业务 adapter parity 与 Phase 5 综合 QA。

## 事件顺序与接口

MCP client 启动 `transbridge-mcp` → server 从 Composition Root 创建 headless runtime → initialize 协商 protocol/capabilities → initialized → tools/list/call → OperationResult 映射 JSON-RPC → shutdown/EOF 释放 runtime。stdout 仅协议帧，stderr 承载日志。CLI/GUI/Agent adapter 都调用同一 use case，不直接构造 parser/client。

## 实施步骤

1. 新增 `entrypoints/mcp.py` 和 `entrypoints/cli.py`，迁移现有 `MCPServer/MCPAdapter` 为 transport adapters。
2. 去除 GUI daemon thread 读取 stdin；Windows 不依赖 `select(stdin)`。
3. 实现 initialize version negotiation、capability report、标准 method-not-found/invalid-params 和 graceful EOF。
4. stdio 凭据从环境/安全存储注入；无 Project 时服务仍可 list tools，调用返回 prerequisite error。
5. 建立 parity harness：固定 RequestContext/fixture，比对 outcome、diagnostic code、ChangeSet/artifact 摘要，忽略显示文案。

## 边界、迁移与回退

MCP 不暴露 GUI 未保存内存；IPC 未实现的能力标 degraded。旧 `smart_assistant.mcp` import 保留 facade，启动入口切换可回退但 GUI 内 stdin server 不再作为受支持拓扑。

## 测试策略

以真实子进程发送 JSON-RPC，验证协议协商、stdout 无日志、stderr 脱敏、无上下文降级、SIG/EOF 关闭和非 ASCII cwd。parity 至少覆盖 capability query 与离线纯函数/需 Project 操作；后续 I/O/Task Plan 复用 harness 扩展。建议 `uv run pytest tests/integration/entrypoints -q` 加安装态 smoke。
