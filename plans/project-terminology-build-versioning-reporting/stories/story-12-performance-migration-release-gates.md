# Story 12：性能收口、迁移演练与发布验证

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

在 S00 固定的参考设备和真实 adapter 数据上完成性能、内存、取消、迁移与故障恢复验证；任何 SHALL 预算或迁移安全性未达标时，明确记录事实并阻止形成正式发布候选，不影响已安装应用中的用户操作。

## 原始验收标准

- [ ] 在 Story 00 固定的参考设备和真实 adapter 数据上，逐项满足 FR5.16.33～FR5.16.40；报告区分冷启动、热缓存、LLM 等待、外部 I/O 和 renderer。
- [ ] 常规/压力构建、重复复用、≤10% 增量、5 次内存稳定性、查询/历史/比较、质量 Excel、5 万项 changelog、取消响应均留下可复验 manifest。
- [ ] 全量与增量 canonical digest 一致，性能优化不抽样、不截断、不跳过证据；超过格式容量明确分卷。
- [ ] Project source migration 和 terminology SQLite migration 在真实副本演练；失败可回退代码并保留 SQLite 只读资产，不能反向降级覆盖新数据。
- [ ] 发布候选 evidence 只由 CI/发行流程消费，不进入 GUI、command、TaskRuntime runner 或 translator；运行时仅保留完整性、新鲜度、存储与并发修订等业务校验。
- [ ] 相关聚焦、集成、性能、全仓 Ruff 和格式检查完成，发布候选 QA 记录兼容/回退和所有未满足预算。

## 当前实现事实

- `tests/performance/measure.py` 已有 wall time、p50/p95、RSS、隔离运行与汇总工具。
- `tests/performance/benchmark_cases.py` 有 threshold/corpus/hardware-tier 模式，但包含可能漂移的硬编码包版本；新脚本必须读取 `transbridge.__version__`。
- `scripts/benchmark_ui_modularization.py` 可作为 JSON stdout runner 参考。
- 当前没有 terminology performance suite、benchmark script 或 RC 报告；发布证据模型只用于测试和发行工具，不接入用户运行时。

## 发布验证与证据流

- S00 数据/测量合同继续作为唯一输入；可新增 `budgets.py` 作为 FR5.16.33～40 的单一机器可读预算表。
- feature stage 建议使用 typed `TerminologyFeatureStage` 与 capability projection，避免散落 bool。

```text
validated S00 environment + dataset
  -> regular/stress benchmark profiles
  -> correctness parity + resource/cancel/export evidence
  -> migration/fault rehearsals
  -> dated machine manifest + RC report
  -> accept or reject the release candidate
```

## 实施步骤

1. 校验 manifest 已固定 CPU/cores/Windows/memory/disk/Python/build/tool versions、seed、adapter 组合、cold/hot 和 5 次规程。
2. 分别计时 capture/parse/assemble/extract/reduce/persist/query/report/changelog、LLM wait 与 external I/O。
3. 执行常规/压力、exact reuse、≤10% incremental、5 轮内存、query/history/compare、50k quality/changelog 和 cancel 场景。
4. 每个增量案例与清 cache 全量比较 canonical digest；导出超限时核对总行数和分卷 manifest。
5. 在真实副本演练 Project source migration 与 SQLite migration，覆盖 disk-full、corrupt、future schema、no-WAL/network path、crash 和 artifact retry。
6. 验证构建、报告、draft/publish、effective、history/revert/changelog 与 partial 策略；发布级失败只阻断发行候选，运行时失败仍由对应业务校验明确报告。
7. 生成 `docs/test-reports/terminology-benchmarks/<date>-release-candidate.md`；未达 SHALL 明确记录并保持 Plan/发布候选未完成。
8. 运行聚焦、集成、性能、相关回归、Ruff、format 与 `git diff --check`，记录所有未运行检查。

## 文件与验证命令

计划完善 S00 文件，新增 budget/RC manifest 支持和 dated report；证据由测试与发行工具消费，不接入应用 composition/config。

```powershell
uv run pytest tests/application/terminology tests/persistence/terminology tests/contracts/terminology -q
uv run pytest tests/integration/terminology tests/ui/tools/terminology -q
uv run pytest tests/performance/terminology -m slow -q
uv run python scripts/benchmark_project_terminology.py --scenario regular
uv run python scripts/benchmark_project_terminology.py --scenario stress
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
git diff --check
```

另需回归 Project V2 migration/lifecycle、I/O adapters、TaskRuntime、ExistingTermSeeder、TermDatabaseManager 与 translation/postprocess report。

## 边界、风险与回退

- 普通 CI 的缩小数据只证明合同，不能替代指定 Windows 参考设备证据。
- LLM 等待从本地 90 秒/8 分钟预算中剔除，但必须保留独立统计；external I/O 也必须单列。
- RSS 必须用 psutil 或明确标注近似；不能用 tracemalloc 结果冒充进程峰值。
- 不得通过抽样、截断、跳过证据或调宽阈值通过发布验证。
- 发布候选失败不修改或删除 SQLite 与新 Project schema 资产，也不反向降级覆盖。
