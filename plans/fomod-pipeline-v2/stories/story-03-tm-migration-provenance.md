# Story 03：TM、Key Migration、Stage 与 Provenance 合同

- 所属 Plan：[FOMOD Pipeline V2](../plan.md)
- 状态：已确认（2026-08-18）
- 追溯：FR23.3、FR18.9；ADR-014/015/017；R-044
- 依赖：S02、I/O S02/S05、TaskRuntime S05

## 目标与验收

调用方显式提供的内存 TM 候选保留 locale/Stage/provenance/dictionary source；Key migration 使用 namespace/fingerprint；冲突可见并显式仲裁；不同 locale 不串用，locked-empty 阻断发布。

## 数据流与接口

source collections + fingerprints → KeyMigrationPlan(match/conflict/unmatched)；TM query(context: EntryKey, locale, stage policy, dictionaries) → Candidate list/provenance → conflict strategy/confirmation → CandidateSet → AI fallback → unique ChangeSet commit。TM 不直接 `apply_to_collection` 写正式状态。

## 实施步骤

1. 内存候选保留 source locale、target locale、stage、provenance、dictionary id/revision、source namespace 和 fingerprint。
2. KeyMigrator 返回计划和诊断，不按文本相同静默覆盖；fingerprint 变化标 stale。
3. 查询按 locale/source/scope 优先，所有冲突保留候选及理由。
4. StagePolicy 排除 hidden/locked；locked-empty 产生 publish blocker；AI 只处理未解决候选。
5. 最终提交复用 CandidateSet/ChangeSet，报告记录来源链。

## 测试、边界与迁移

覆盖多 locale、多来源同键/同文本冲突、source change、STALE、hidden/locked/empty 和取消。缺少显式 TM 候选时，继续使用归档迁移结果与可选 AI 翻译。FOMOD 不读取或迁移磁盘词典。
