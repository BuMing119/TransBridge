# Story 01：凭据存储、迁移与全通道脱敏

- 所属 Plan：[ParaTranz Sync Service V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR22.1/22.5、NFR4.1；ADR-012/016；R-040
- 依赖：platform S02/S04

## 目标与验收

配置不回写明文 token；401/异常/日志/遥测/Agent/MCP 结果不泄露 secret；无凭据返回 prerequisite；离线 JSON adapter 不读取凭据。

## 数据流与接口

配置保存 `CredentialRef` → SecretPort (`get/set/delete`) → Windows credential adapter 或 headless env provider → request header（最短生命周期）→ redactor 处理所有 diagnostic。计划接口：`SecretValue`（禁止 repr/str）、`CredentialRef`、`SecretStoreCapability`、`SecretRedactor.redact(object)`。

## 实施步骤

1. 把 `config/paratranz.py` 的 token 字段拆为 credential ref；优先系统安全存储，环境只读覆盖用于 MCP/CLI。
2. 迁移旧 INI 前确认可写安全存储，成功后清理/提示用户删除明文；失败保留原文件且标 degraded。
3. ParatranzClient 不记录 headers/token；共享 redactor 注入 logging、ToolResult、MCP、telemetry/report。
4. secret 仅在请求构造时解封，异常上下文使用 endpoint/request id。
5. 离线 adapter 包依赖测试禁止导入 SecretPort/client。

## 测试、边界与回退

用多种 canary（原文、Bearer、URL/query、嵌套 dict）扫描 stdout/stderr/log/report/Tool/MCP；覆盖旧配置迁移、环境优先级、存储失败/删除和无凭据。回退只能进入无网络 capability，不得恢复明文持久化。
