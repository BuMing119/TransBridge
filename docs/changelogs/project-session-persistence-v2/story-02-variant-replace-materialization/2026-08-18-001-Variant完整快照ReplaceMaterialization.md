# Story 02：完整 Variant Snapshot 与 Replace Materialization

- 日期：2026-08-18
- Epic/Story：`project-session-persistence-v2/S02`
- 追溯：FR19.1～19.3、ADR-018、R-021/R-022/R-024
- 状态：实现完成，增量验证通过；待综合 QA

## 实现与验证

- V2 VariantAggregate 保存完整 EntryKey、空值/tombstone、stage、labels、provenance、revision、source namespace 与 fingerprint；VariantChangeSet 在校验完成后一次 swap，失败不改变既有状态。
- materialize 从 source baseline 重建再完整 replace，阻断 fingerprint 不匹配和多来源同 local key 覆盖；A→空→B 与重启后显式清空不复活。
- 旧 VariantStore 委托兼容投影；没有 source baseline 时发出 DeprecationWarning，不伪称实现无损 replace。
- 锁定 uv：`tests/persistence/v2` **49 passed**；[EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/persistence-s02/qa-20260818T085700.495934Z-b3a3dc1fc4fb/manifest.json) 已验证有效。Ruff check/format 与定向 diff 检查通过。

## 剩余门禁

旧 `list[TranslationEntry]` facade 不能无损恢复 labels/revision/provenance；GUI/session active-variant 生命周期和 baseline 注入留 S03，100k 性能证据留 release S03。未执行 Git commit/push。

## 2026-08-18 项目加载性能补充

- `VariantMaterializer` 改为每个 source baseline 只建立一次 `EntryKey` 索引，再以 O(B+V) 应用 Variant 状态，消除逐条重复扫描 baseline 的 O(B×V) 路径。
- loader 仅在 materialized aggregate 与已存 Variant 内容完全一致时复用 persisted revision；若 source materialization 改变内容，即使数字 revision 相同也保持 dirty 并写回，避免性能优化跳过必要持久化。
- 增加 baseline 单次迭代合同测试；真实 19,387 条目项目的 activate 阶段约 5.48 秒，原路径超过 90 秒仍未完成。
