# ParaTranz Sync Service V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR22、FR18.3～18.5、FR17、NFR2.1、NFR4.1
- **架构**：ADR-016/017/019、ADR-012 的 2026-08-18 增量
- **问题**：R-040～R-042，以及 R-013 的网络边界
- **依赖**：`platform-contract-foundation-v2` S02～S05；`translation-io-kernel-v2` S02～S03/S06；`unified-task-translation-runtime-v2` S01～S03

## 目标与边界

把离线 ParaTranz JSON Adapter 与网络同步分为两个 use case，但复用字段映射。网络同步提供 typed response、错误分类、dry-run plan、显式确认、事务式本地合并、partial/retry/cancel 语义和凭据安全。

本 Plan 不让远端 ID 替代内部 EntryKey，也不让网络状态改变离线文件转换。UI、Agent、MCP 只做 adapter。

## Story 清单

### Story 01：凭据存储、迁移与全通道脱敏

[详细设计](stories/story-01-credentials-redaction.md)

- **目标**：消除 token 明文打印/INI 保存和结果泄露。
- **文件落点**：安全存储 port/Windows adapter、`config/paratranz.py`、client logging、共享 redactor、迁移说明。
- **实施**：凭据引用与配置分离；优先系统安全存储，headless 可从环境注入；旧明文配置只读迁移并提示清理；日志/异常/遥测/Agent/MCP 统一脱敏。
- **验收**：源码配置不回写明文；401/异常不含 token；未提供凭据返回 prerequisite error；离线 Adapter 无需凭据。
- **测试**：secret canary 全通道扫描、旧配置迁移、环境注入、凭据缺失和安全存储失败。

### Story 02：Typed ParaTranz Client 与错误/重试合同

[详细设计](stories/story-02-typed-client-errors.md)

- **目标**：修复 Agent 调用不存在 API，并统一认证、限流、超时、服务错误、冲突与取消。
- **文件落点**：`paratranz/paratranz_client.py`、`paratranz/api/`、application port/adapter、contract tests。
- **实施**：定义 API request/response DTO；统一 endpoint 方法；安全幂等操作才自动重试；有界指数退避并尊重 Retry-After；请求上下文不含 secret；取消传播 TaskRuntime token。
- **验收**：Agent 所需方法存在且通过 port；错误类型稳定；非幂等写不盲重试；partial item diagnostics 保留 remote reference。
- **测试**：受控 HTTP server 的 2xx/401/403/409/429/5xx/timeout/cancel；调用签名契约。

### Story 03：Sync Plan、Dry-run 与显式确认

[详细设计](stories/story-03-sync-plan-confirmation.md)

- **目标**：在上传、下载、合并前展示新增/更新/冲突/跳过/删除影响。
- **文件落点**：新增 sync planner/models/use cases；GUI/Agent/MCP adapters。
- **实施**：基于 EntryKey+ExternalEntryRef+revision 生成 immutable plan；冲突策略显式；破坏性/覆盖性执行需要 confirmation token 绑定 plan hash；计划过期需重算。
- **验收**：dry-run 无远端/本地写入；删除/覆盖未确认不可执行；相同 snapshot 计划确定；远端变化使旧确认失效。
- **测试**：plan fixtures、无副作用断言、确认 token/过期、冲突策略与多入口计划 parity。

### Story 04：事务同步、部分失败与 Artifact 原子发布

[详细设计](stories/story-04-transactional-sync-artifact.md)

- **目标**：在隔离副本合并并验证，失败不留下半合并集合或半成品。
- **文件落点**：sync executor、uploader/downloader/artifact facade、repository UoW、publish port。
- **实施**：批量执行记录 item outcome；下载/Artifact 使用 `.part`/staging+manifest；合并在副本完成并原子提交；partial 保留重试 token/依据；取消经过 commit guard。
- **验收**：部分失败可定位且正式本地状态一致；取消后不提交迟到批次；Artifact 校验后发布；重试不重复已确认成功项。
- **测试**：真实受控服务成功链、批次中断/限流、磁盘 fault、取消 race、幂等重试、GUI/Agent/MCP 结果一致。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR22.1；R-013 | S03/S04 + I/O S03 | `paratranz-integration`: 离线/网络边界 `superseded_by` 本 Plan与 I/O Plan |
| FR22.2～22.4；R-041/042 | S02～S04 | `paratranz-integration`、`agent-tool-expansion` S11：`partially-verified`, `blocked_by` 本 Plan |
| FR22.5；R-040 | S01 | 旧安全完成声明 `blocked_by: paratranz-sync-service-v2/S01` |

## 风险、回退与完成门禁

- 风险：远端 API 行为/配额变化。控制：client contract 与 fixture 分层，live smoke 只在显式凭据环境运行。
- 风险：错误冲突策略覆盖用户数据。控制：plan hash + 显式确认 + 隔离合并。
- 回退：网络 adapter 可回切只读能力；不得回退到明文凭据或逐条直接写正式集合。
- 完成门禁：受控 HTTP 成功链、事务故障/partial/cancel、secret canary 与跨入口 parity 全部通过。
