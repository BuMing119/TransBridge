# Story 00：固定性能基准环境与可复现数据合同

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

在任何术语性能优化或达标声明之前，固定可复现的数据、参考设备、测量阶段、缓存边界和证据格式，使 S12 能对 FR5.16.33～FR5.16.40 做跨发布候选比较。

## 原始验收标准

- [ ] 在实现性能优化前记录参考设备 CPU 型号、核心数、Windows 版本、内存、磁盘类型、Python/TransBridge build 和测量工具版本。
- [ ] 固定常规/压力数据集的生成种子、真实 adapter 可读格式组合、来源/证据/术语/冲突/版本规模，以及冷缓存、热缓存、LLM 排除计时和 5 次重复运行规程。
- [ ] 基准结果可区分 capture/parse/assemble/extract/reduce/persist/query/report/changelog、外部 LLM 等待和外部 I/O 等待；证据落在发布候选可保留的位置。
- [ ] 校准只固定测量口径，不修改 FR5.16.33～FR5.16.40 的已确认预算。

## 当前实现事实

- `tests/performance/measure.py` 已提供 `sample_time()`、`measure_rss_growth()`、`run_isolated()`、`summarize()` 和 `current_rss_bytes()`，并区分 psutil RSS 与 tracemalloc 近似值。
- `tests/performance/benchmark_cases.py` 已有 `HardwareTier`、`Thresholds`、`BenchmarkCase`、稳定 corpus fingerprint 和小型真实格式样本生成方式。
- 可复用真实 adapter fixture 包括 `tests/parser/data/sample.esp`、`tests/contracts/io/fixtures/eet-small.xml`、`xt-small.xml` 与 localized strings integrity fixtures。
- TransBridge build 必须读取 `transbridge.__version__`；不得复制现有 benchmark 文件中可能漂移的硬编码版本。
- 仓库已注册 `slow`、`integration`、`llm` markers。

## 数据合同与计划接口

- `tests/performance/terminology/dataset.py`：计划新增 `TerminologyDatasetSpec`、`GeneratedTerminologyDataset`、`generate_terminology_dataset()`、`dataset_manifest()`、`canonical_dataset_digest()`。
- `tests/performance/terminology/measure.py`：计划新增 `TerminologyPhase`、`PhaseTiming`、`BenchmarkRun`、`BenchmarkManifest`、`measure_phase()`。
- 场景名固定为 full-cold、full-warm、repeat、changed-10pct、query、compare、report、changelog；扩展时提升 manifest schema。
- manifest 至少包含环境、seed、adapter/version、规模、expected counts、cache preparation、5 次原始样本、汇总统计、外部等待和 artifact digest。

## 实施步骤

1. 固定 dataset schema/version、seed、真实格式组合与常规/压力规模；大数据运行时生成，不提交百万条二进制夹具。
2. 为数据集保存来源 fingerprint、证据/候选/冲突/版本预期数和 canonical digest，使“同 seed”可被自动验证。
3. 复用现有计时/RSS helper，增加术语阶段计时；LLM 与外部 I/O 必须是独立桶，不能从总耗时倒推。
4. benchmark 脚本采集 CPU、物理/逻辑核心、Windows、内存、磁盘类型、Python、`transbridge.__version__` 和测量库版本；采集不到的必填项要求显式录入。
5. 为冷/热缓存提供明确准备钩子，并把实际清理或保留的缓存层记录进 manifest。
6. 每个正式场景执行 5 次，保留原始样本与聚合结果；JSON 使用稳定字段顺序、明确单位和 schema version。
7. 在 `docs/test-reports/terminology-benchmarks/README.md` 固定命令、参考机、seed、缓存和 LLM 排除规程；RC 证据使用带日期/build 的独立目录或文档。
8. CI 只执行缩小规模合同；完整常规/压力基准标记 `slow` 并在指定 Windows 参考设备执行。

## 文件变更清单

计划新增：

- `tests/performance/terminology/__init__.py`
- `tests/performance/terminology/dataset.py`
- `tests/performance/terminology/measure.py`
- `tests/performance/terminology/test_dataset_contract.py`
- `tests/performance/terminology/test_manifest_contract.py`
- `scripts/benchmark_project_terminology.py`
- `docs/test-reports/terminology-benchmarks/README.md`

## 边界、测试与风险

- psutil 不可用时只能标记 `tracemalloc approximation`，不能把结果声明为进程 RSS。
- 磁盘类型未知、真实 adapter 未运行或缓存清理边界不明时，结果不得标为已校准。
- 防病毒、网络路径和外部服务等待单列，不得以此修改 90 秒/8 分钟等预算。
- S00 不实现硬预算断言；S03 前固定规程，S12 执行完整门禁。

建议命令：

```powershell
uv run pytest tests/performance/terminology -m "not slow" -q
uv run pytest tests/performance/terminology -m slow -q
uv run python scripts/benchmark_project_terminology.py --scenario full-cold
```
