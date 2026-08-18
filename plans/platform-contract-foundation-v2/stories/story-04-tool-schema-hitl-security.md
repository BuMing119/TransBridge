# Story 04：工具注册、Schema、HITL 与安全合同

- 所属 Plan：[Platform Contract Foundation V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR17.2/17.4、NFR4.1；ADR-012/016；R-005/R-009～R-011/R-040
- 前置依赖：S02/S03

## 目标与原始验收

修复 wildcard 早展开、非规范 schema、写后审批、路径策略分裂和 Observation 丢 schema。预置 Agent 能力准确；拒绝 HITL 不产生写入；路径按授权根判断；secret canary 不出现在任何输出。

> 实现记录：[2026-08-18-001-工具 Schema 与 HITL 安全合同](../../../docs/changelogs/platform-contract-foundation-v2/story-04-tool-schema-hitl-security/2026-08-18-001-工具Schema与HITL安全合同.md)。正式 uv 证据为 30 passed、1 skipped，广泛 Smart Assistant 回归为 463 passed；仍待 S05 MCP/入口 parity 与 Phase 5 综合 QA。

## 数据流与接口

Tool definition → registry validate/freeze → Agent capability resolve → request schema validate → path/auth/HITL preflight → use case → canonical result → redaction → adapter summary。`ToolSpec.parameters` 改为 JSON Schema；计划新增 `ToolInvocation`, `AuthorizationDecision`, `PathGrant`, `ConfirmationToken`, `SecretRedactor`。完整结构化结果与 LLM 展示摘要分开存储。

## 实施步骤

1. ToolRegistry 注册完成后再解析 namespace/wildcard，并冻结 Agent tool set；重复名/非法 schema 启动失败。
2. 用标准 JSON Schema validator 代替 `str/list` 自定义类型；错误定位到 JSON Pointer。
3. 将 PermissionGuard/HITL 前移至任何 mutation/文件/网络调用之前，确认 token 绑定 request hash 和 owner。
4. 文件路径 `resolve(strict as policy)` 后检查授权根、junction/symlink 与目标创建父目录；不一律拒绝绝对路径。
5. 复用单一 redactor 处理日志、ToolResult、MCP、遥测和持久化；Observation 仅截断 display summary。

## 文件、边界与迁移

修改 `tool_registry.py`、`tools/types.py/base.py`、`guardrails/*` 和 Agent 初始化；新增 application tool/security adapters。旧 ToolSpec 由一次性兼容转换器读取，无法转换即标 capability unavailable。确认超时/owner 变化/plan hash 变化必须拒绝执行。

## 测试策略

使用真实 registry 初始化验证工具数量与 namespace；覆盖 JSON schema 正反例、拒绝确认零副作用、路径逃逸/Unicode/junction、确认重放、token canary 扫描、4180 字符帮助经 Observation 后仍可从结构化 channel 获取完整 schema。建议命令：`uv run pytest tests/smart_assistant tests/contracts/security -q`。
