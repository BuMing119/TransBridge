# Story 01：V2 Schema、Repository 与安全迁移框架

- 日期：2026-08-18
- Epic/Story：`project-session-persistence-v2/S01`
- 追溯：FR19.3/19.7、NFR2.1/NFR4.1、ADR-018、R-022/R-026
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增 V2 Project/Variant/Session JSON envelope、DTO、strict JSON/schema/语义校验，以及受限 opaque Project/Variant/Session ID；路径只由 root 与编码 ID 派生，文件内 identity 必须匹配请求 reference。
- Repository 通过可注入的 filesystem port 执行 staging、replace 和 verified copy；真实 OS adapter 与故障注入内存 adapter 均被测试。Variant 的 record、backup、quarantine 和 staging 路径包含编码 ProjectId 命名空间，消除跨项目同名 Variant 冲突。
- V1 Project/Variant/Session 在副本中确定性迁移，先创建 hash 验证的 backup，再原子替换。历史 Variant display name 可含空格或 Unicode，迁移为稳定 `legacy-<hash>` opaque ID；缺失 Stage/provenance 明确标为 unknown，不伪造历史。
- 损坏、类型错误、路径/ID 欺骗或断裂引用生成同根 verified quarantine 副本，并保留原件。backup、quarantine 或 replace 失败均停止且清理 staging，不将失败记录视为可用空对象。
- 主线复审补充 save fail-closed：future schema、未迁移 V1、损坏或 reference 不匹配的已有目标拒绝覆盖，抛出带稳定 code 的 `ReadOnlyWriteRefused`，确保 bytes 与磁盘写操作均不变。

## 验证证据

- 锁定 uv 运行 `tests/persistence/v2`：**37 passed**，覆盖 V1/V2 往返、迁移幂等、空译文、备份/隔离/replace/read fault、future/invalid save 拒绝、root/path/ID 边界和 Unicode 临时真实文件系统。
- `ruff check`、`ruff format --check` 与定向 `git diff --check` 通过。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/persistence-s01/qa-20260818T075850.252252Z-c000800275c6/manifest.json)：`passed`，已用 verify 复验 schema、verdict 与 hash。
- 未执行 Git commit/push。

## 剩余门禁

本 Story 只建立 V2 kernel，不迁移旧 ProjectHandle/VariantStore/SessionManager facade，也没有实现完整 Variant replace materialization、跨文件 UnitOfWork、Session aggregate 或 owner 生命周期。它们分别由 S02～S05 承接；正式综合 QA 前，V2 Repository 不代表旧 GUI/Agent 调用链已切换。
