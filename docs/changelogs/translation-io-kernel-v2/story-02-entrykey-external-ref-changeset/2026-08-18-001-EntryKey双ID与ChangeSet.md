# Story 02：EntryKey、双 ID 与 ChangeSet

- 日期：2026-08-18
- Epic/Story：`translation-io-kernel-v2/S02`
- 追溯：FR18.3、FR19.3、NFR2.1、ADR-001/002/017、R-020
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增不含绝对路径的 SourceNamespace、稳定 EntryKey、与主身份解耦的 ExternalEntryRef、严格 EntryRevision 和 Provenance 合同。
- TranslationEntry 保留 V1 id/key facade，同时以 EntryKey 作为正式身份并序列化 V2 envelope；revision 仅接受非 bool 整数或 EntryRevision，字符串/布尔绕过被拒绝。
- TranslationEntryCollection 采用唯一序列化 EntryKey 主索引；legacy id/key 只读扫描，不维护第二套可写索引，歧义不会按遍历顺序误命中。
- ChangeSet 通过可信 RequestContext 校验 run、actor、字段权限和 expected revision；全部 patch 先投影、检查 ExternalEntryRef 冲突，再原子交换集合与索引。
- legacy updater 统一使用 `dataclasses.replace`，保留 namespace、external refs、revision、provenance 与 metadata；覆盖 facade 产生弃用审计。

## 验证证据

- I/O S01/S02 合同与显式历史 Collection 回归：74 passed，6 个 warning 均为预期的 compatibility facade 弃用提示。
- 新 I/O 文件与合同测试 Ruff/format 通过；历史 converter touched 文件及测试 Ruff 通过，未批量格式化历史文件。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/io-s02/qa-20260818T071937.593080Z-34c60020e704/manifest.json)：`passed`，schema/verdict/hash 复验有效。
- 未执行 Git commit/push。

## 剩余门禁

旧调用方仍可经 mutable entry 与 `add(overwrite=True)` facade 写入，但会被审计；后续 Story 应迁移至唯一 MutationPort。legacy facade 不提供以 V2 语义主动清空 metadata/external refs；ParaTranz 离线 JSON 双 ID 的具体映射由 S03 承接。

## 2026-08-18 Collection 构建性能补充

- `TranslationEntryCollection` 构造改为一次性建立 primary index，并在全部条目归并后只构建一次 external-ref index；后续单条 mutation 语义保持不变。
- 重复 `EntryKey` 仍遵循 last-wins，同时保留 revision、provenance 与 external metadata 合同，不以性能优化改变兼容行为。
- 增加 500 条目下 external index 仅构建一次的合同测试；真实约 19,470 条目 Collection 构建由约 50 秒降至约 0.15 秒。
