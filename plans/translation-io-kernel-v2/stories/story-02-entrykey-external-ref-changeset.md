# Story 02：EntryKey、ExternalEntryRef 与受控 ChangeSet

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR18.3、FR19.3、NFR2.1；ADR-001/002/017；R-020
- 依赖：S01、platform S02

## 目标与验收

内部身份受来源命名空间约束，外部 ID 变化不改变 EntryKey；不同来源同 key 不覆盖；revision 冲突被拒绝；兼容 facade 不维护第二套索引。

> 实现记录：[2026-08-18-001-EntryKey 双 ID 与 ChangeSet](../../../docs/changelogs/translation-io-kernel-v2/story-02-entrykey-external-ref-changeset/2026-08-18-001-EntryKey双ID与ChangeSet.md)。主线正式 uv 回归 74 passed，EvidenceManifest 复验有效；ParaTranz 双 ID 具体 adapter 由 S03 承接。

## 当前事实、数据流与目标接口

当前 `TranslationEntry.id == key`，Collection 以可覆盖 dict 写入，字段可直接 mutation。计划新增不可变 `SourceNamespace`、`EntryKey(namespace, local_key)`、`ExternalEntryRef(system, opaque_id, metadata)`、`EntryRevision`、`Provenance`、`EntryPatch`、`ChangeSet(run_id, expected_revisions, patches)` 和 `MutationResult`。读取返回 snapshot/view，写入经 `CollectionMutationPort.apply()`。

## 实施步骤

1. 扩展/迁移 TranslationEntry schema，保留 V1 id/key 读取但新写出明确 V2 字段。
2. Collection 主索引切为序列化 EntryKey；ExternalEntryRef 建独立非唯一/冲突检测索引，不作为主键。
3. 所有 patch 校验 expected revision、run_id、StagePolicy 与字段权限，成功后统一递增 revision/写 provenance。
4. 为 TM、migrator、Variant 和旧工具提供 facade；直接字段 mutation 加弃用审计，逐 Story 封禁。
5. 提供只读 V1→V2 映射报告；冲突要求确认，不按遍历顺序覆盖。

## 边界、迁移与测试

namespace 由规范化来源 identity/fingerprint 产生但不包含易变绝对路径；外部 id 可缺失、重分配或多系统并存。迁移失败保留原数据。测试覆盖相同 local key 多 namespace、外部 id 变化、重复 external id、expected revision race、序列化 round-trip 和 legacy facade parity；属性测试保证 ChangeSet 原子性。
