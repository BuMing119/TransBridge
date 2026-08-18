# Story 02：完整 Variant Snapshot 与 Replace Materialization

- 所属 Plan：[Project Session Persistence V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR19.1～19.3；ADR-018；R-021/R-022/R-024
- 依赖：S01、I/O S02/S05

## 目标与验收

Variant 完整保存条目身份、译文、显式清空、Stage、标签、provenance、revision 与来源；A→空→B 不串版，清空后重启不复活，同 key 多来源隔离。

## 数据流与接口

SourceSnapshot/materialized baseline + `VariantSnapshot(source_namespaces, entries, revision)` → validate fingerprint → 重建临时 Collection → 对目标 namespace 完整 replace → 单一 ChangeSet commit → projection refresh。字段采用 explicit value/presence 语义，空字符串/空集合是有效状态，不用 truthy 过滤。

## 实施步骤

1. 定义 VariantEntryState，包含 EntryKey、translation、stage、labels、provenance、entry revision；snapshot 带 source fingerprints。
2. `collect_from` 改为从 Aggregate 生成全量 snapshot，不复用旧缓存值。
3. `materialize` 先从 source baseline 恢复目标 namespace，再应用完整 Variant；缺项按基线/显式策略处理。
4. fingerprint 不匹配生成 conflict/migration plan，禁止按相同 local key 直接覆盖。
5. 旧 VariantStore `apply_to` 委托新 use case，并记录调用方迁移。

## 边界与测试

来源删除/新增、条目集合变化、空 Variant、显式清空、标签清空、unknown EntryKey 都需诊断。测试首先固化现有“from-A 残留”和“old 复活”探针，再覆盖多 namespace/fingerprint、Stage/provenance 往返、原子 ChangeSet failure。用 100k fixture 测 snapshot/restore 时间与内存，基准结果交 release S03。
