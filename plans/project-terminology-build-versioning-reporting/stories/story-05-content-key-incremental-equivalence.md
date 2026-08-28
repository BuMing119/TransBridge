# Story 05：内容键增量构建与全量等价性

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

以 versioned 内容键精确复用完整 `BuildResultRef`，并只重算变化关系连通分量；无论全量还是增量，最终都由 S03 同一个 global reducer 处理完整逻辑集合并得到相同 canonical digest。

## 原始验收标准

- [ ] 完全相同输入在验证 Project/Variant/source/effective/draft 基线后复用 `BuildResultRef`，不重新 parse 或调用 LLM。
- [ ] 单个来源变化时只重算受影响的关系连通分量和 parse/evidence/extraction fragment；全局 normalization、冲突、人工协调和 summary 仍走与全量相同的 reducer。
- [ ] 关系、adapter/version、parse options、normalization/extractor/prompt/model/config、draft identity/base/decision digest 任一变化均使对应缓存失效。
- [ ] 增量与相同输入的无缓存全量构建 canonical digest 完全一致；异常/损坏 cache 自动回退全量，不改变业务结果。
- [ ] 结果报告复用/重算来源和分片数量；缓存清理不删除正式历史事实。

## 依赖与当前事实

- 依赖 S01 `BuildInputSnapshot`/relation graph、S02 identities、S03 build/reducer、S04 cache adapter。
- `FormatAdapter.adapter_id()/adapter_version()`、`ParseRequest`、`ParseResult`、`SourceSnapshot` 提供 parse key 输入。
- 当前没有 terminology incremental/cache coordinator；旧 `ExistingTermSeeder` 不能作为 cache 生命周期。

## 关键接口与数据流

- `incremental.py`：计划新增 `RelationComponentDigest`、`RecomputePlan`、`BuildReuseDecision`、`IncrementalBuildPlanner`。
- 关系边虽然有业务方向，但受影响范围按无向连通性求组件，避免漏掉上/下游证据。

```text
BuildInputSnapshot -> build_key
  -> exact hit: verify every pinned baseline + referenced content digest
  -> miss: compute sorted relation components/digests
       -> reuse unchanged parse/extraction fragments
       -> recompute affected components
       -> full logical fragment set
       -> shared reduce_fragments(...)
       -> canonical digest + BuildResultRef
```

## 实施步骤

1. 固定 build/parse/extraction key 的 schema、字段顺序与 invalidation matrix。
2. 按稳定 source/relation ID 排序生成关系组件，relation policy/version 必须进入 digest。
3. exact reuse 不只查 build key；还要复核 Project/Variant/source/effective/draft 基线及 ref payload digest。
4. 生成显式 `RecomputePlan`，记录 reused/reparsed/reassembled/reextracted 的来源、组件和数量。
5. cache decode/schema/digest 异常时记录诊断并切换无缓存全量，不混用可疑 fragment。
6. 全量和增量统一调用 `reduce_fragments(...)`；禁止 delta-only append、只追加不撤销或顺序敏感计数。
7. 冻结前生成 canonical digest；每个增量案例与清空 cache 的全量结果比较，同时上报复用/重算统计。

## 文件与测试

计划新增 `application/terminology/incremental.py`、`tests/application/terminology/test_incremental.py`、`tests/integration/terminology/test_incremental_equivalence.py`；计划修改 S03 `build.py/reducer.py` 和 S04 `cache.py`。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_incremental.py tests/integration/terminology/test_incremental_equivalence.py -q
```

矩阵覆盖 0%、单来源、≤10% evidence、relation policy、adapter/version/options、normalization/extractor/prompt/model/config、Variant/effective、draft rebuild/rebase，并用 spy 验证 parse/LLM 调用数。

## 边界、风险与回退

- draft 放弃后即使 revision 数值重复，也必须因 draft ID/base/decision digest 不同而失效。
- 损坏或未来 cache 回退全量；全量也失败时返回真实失败，不能用旧结果冒充当前。
- 取消分片只允许 run-scoped staging，不进入正式 cache/ref。
- 最危险的是 invalidation 漏字段；cache 可全部关闭/清空，正式历史不受影响，性能目标留 S12 验证。
