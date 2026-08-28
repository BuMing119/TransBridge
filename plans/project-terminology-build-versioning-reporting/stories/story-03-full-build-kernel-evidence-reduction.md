# Story 03：全量构建内核与项目级双语证据归并

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

以 S01 捕获的权威输入和 S02 的领域合同实现确定性全量构建：显式关系组装证据、资格筛选、确定性/可选 LLM 抽取、全局归并、冲突分组、人工基线协调和不可变冻结。

## 原始验收标准

- [ ] 流水线严格执行 capture → parse registered sources → assemble evidence → eligibility → deterministic/optional LLM extraction → normalize/deduplicate/conflict group → reconcile manual/effective baseline → freeze。
- [ ] 插件原文、关联迁移源、STRINGS 和当前 Variant 状态按完整 `EntryKey`、fingerprint 兼容规则和显式关系形成一条来源链；不能按 local key 或扫描顺序覆盖。
- [ ] 首版只接受原文译文均非空且非 hidden/questionable 的证据；排除原因、失败/跳过来源、耗时和完整性进入结果。
- [ ] 同规范原名同译名合并全部证据并稳定累计；同原名多译名必建冲突组，不按频次/来源优先级选胜者；人工决定保留并把新增矛盾标为待复核。
- [ ] LLM 关闭/不可用/跳过/部分失败不阻断确定性结果；候选无法定位到同一证据、反序列化失败或迟于取消时只产生诊断。
- [ ] 相同输入全量构建产生相同候选、冲突、计数、排序和 canonical digest。

## 当前实现事实

- `ExistingTermSeeder` 的直接名称抽取、资格判断、NFKC/空白规范化、`StableContentBatcher` 和同证据定位规则可以窄复用。
- `ExistingTermSeeder.seed()` 直接写 `DynamicTermDatabase`、只接受单集合、自建线程池、整库跳过 LLM，并在冲突时整组丢弃，不能成为新生命周期。
- `TermDatabaseManager.load_all()/resolve_term()` 没有 Project/Variant/plugin context，只能通过窄 adapter 提供 legacy/effective baseline。
- `TranslationIoUseCase.parse()`、`ParseResult`、`VariantSnapshot.entries` 和完整 `EntryKey` 是现有输入边界；多数 adapter 仍返回整源 tuple/bytes。

## 数据流与计划接口

```text
BuildInputSnapshot
  -> parse each registered source
  -> EvidenceAssembler over explicit relation graph
  -> Variant overlay by full EntryKey
  -> EvidenceEligibilityPolicy
  -> deterministic extractor + optional LLM extractor
  -> CanonicalTerminologyReducer
  -> ManualBaselineReconciler
  -> BuildSummaryReducer
  -> immutable BuildResultRef
```

计划新增 `corpus.py`、`extraction.py`、`reducer.py`、`build.py`。若要复用旧行为，先把纯规则抽到小型 `existing_term_rules.py`，旧 seeder 改为委托并保持回归。

## 实施步骤

1. 只从 S01 `capture_build_input()` 取得输入，按稳定 source ID 排序并通过 capability gate 解析登记来源。
2. 对每个来源记录 status、adapter/version、timing 和 diagnostics；单源失败可 partial，全部不可读为 failed。
3. `EvidenceAssembler` 按显式关系图、完整 `EntryKey` 和 fingerprint compatibility 组装来源链，再应用当前 Variant state；缺失/歧义不猜测。
4. eligibility 只接受双非空且非 hidden/questionable，按原因计数；只有原文的条目不调用 LLM 制造译文。
5. 先确定性抽取，再通过注入的有界 executor/quota 运行可选 LLM；稳定分批且每个候选必须定位到同一 evidence。
6. reducer 按规范原名、作用域、译名和稳定 ID 排序；同译合并全部证据，多译创建 `ConflictGroup`，不自动选胜者。
7. 与人工/effective baseline 协调：人工值优先，新矛盾待复核，未解决/抑制项不进入 effective 候选。
8. 一次计算 summary、排除、来源耗时、LLM 状态、完整性与 canonical digest 后冻结；取消或迟到结果不提交正式 ref。

## 文件与测试

计划新增：

- `src/transbridge/application/terminology/{corpus,extraction,reducer,build}.py`
- `tests/application/terminology/test_corpus.py`
- `tests/application/terminology/test_extraction.py`
- `tests/application/terminology/test_reducer.py`
- `tests/application/terminology/test_build.py`

建议命令：

```powershell
uv run pytest tests/application/terminology tests/ai_translator/test_existing_term_extractor.py -q
```

关键场景覆盖多插件/XML/STRINGS、N:M、Variant 未落盘覆盖、相同 local key 跨 namespace、同译合并、三译冲突、插件特例、legacy/effective 冲突、LLM 各状态、单源/全源失败、输入重排与 golden digest。

## 边界、风险与回退

- 顶层 builder 必须逐来源/分片持久化后释放，不能同时保留全部 `ParseResult`。
- 新 reducer 不继承旧 seeder 的“冲突整组丢弃”；抽纯函数后仍要保持旧 `seed()` 行为回归。
- LLM partial 结果只有在同证据定位、未取消且未迟到时可进入，并明确标记 `llm=partial`。
- 本 Story 以内存 repository 建正确性基线，不加入增量捷径或 SQLite 特化；回退时旧动态术语初始化路径保持不变。
