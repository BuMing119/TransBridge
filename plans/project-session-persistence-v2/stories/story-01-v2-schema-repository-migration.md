# Story 01：V2 Schema、Repository 与安全迁移框架

- 所属 Plan：[Project Session Persistence V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR19.3/19.7、NFR2.1/NFR4.1；ADR-018；R-022/R-026
- 依赖：platform S02/S03、I/O S01/S02

## 目标与验收

建立可验证的 Project/Variant/Session V2 schema、Repository 与迁移/隔离流程。合法 V1 可确定性迁移；恶意 ID/路径不能越界；迁移失败不改变原文件。

## 数据流与接口

repository `load(ref)` → 读取 bytes/计算 hash → schema envelope 校验 → version migrator chain 在副本转换 → domain aggregate rehydrate/引用校验 → 返回 aggregate 或 `QuarantineResult`。保存走 DTO→schema validate→staging→replace。计划接口：`ProjectRepository/VariantRepository/SessionRepository`、`SchemaEnvelope(version, id, revision)`、`MigrationReport`、`QuarantineRef`。

## 实施步骤

1. 在 `persistence/v2/` 定义 JSON schema/DTO 与 domain mapper，Project/Variant/Session ID 使用受限 opaque value object。
2. Repository 仅通过 FilesystemPort，路径由根目录+编码后 ID 推导，文件内部 ID 必须与引用一致。
3. 实现 V1→V2 migrator，迁移前创建可验证备份，输出字段默认/丢失/冲突报告。
4. 不可迁移文件移动到同根 quarantine（保留原 hash/原因/恢复指引），不当作空对象。
5. 旧 `ProjectHandle/VariantStore/SessionManager` 先变 facade，避免同时写 V1/V2。

## 边界、回退与测试

未知未来版本只读拒绝；部分 JSON/类型错误不可静默修补；备份/隔离失败则停止迁移。测试覆盖 V1 fixtures、缺字段/错类型/引用断裂、`../`/绝对 ID、文件名内部 ID 欺骗、磁盘 fault、重复迁移幂等和 V2 round-trip。回退通过原备份与只读导出，不覆盖 V2 数据。
