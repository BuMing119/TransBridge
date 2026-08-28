# FR5.16 项目术语基准规程

此目录保存项目全来源术语构建的可复验发布候选证据。Story 00 只冻结数据、环境、缓存和计时口径；它不新增、修改或放宽 FR5.16.33～FR5.16.40 的任何预算。

## 固定数据合同

- schema version：`1`；generator version：`1.0`；固定 seed：`516000`。
- smoke（CI 合同）：5 个来源、64 条证据、16 个术语、4 个冲突组、3 个历史版本。
- regular：50 个来源、250000 条有效双语证据、50000 个最终/待复核术语、5000 个冲突组、10 个历史版本。
- stress：200 个来源、1000000 条证据、200000 个术语、20000 个冲突组、50 个历史版本。
- adapter 组合固定为真实可读的 SSE plugin、EET XML、XT XML、STRINGS 和 ParaTranz JSON；manifest 保存 adapter id/version、模板大小和 SHA-256。逻辑大数据在运行时生成，不提交百万条固定夹具。
- 同 seed、同 generator、同 adapter fixture/version 必须产生相同来源 fingerprint、expected counts 和 canonical dataset digest。任一项变化都表示新数据合同，必须提升 schema 或 generator version，并保留旧证据。

## 正式参考设备记录

正式发布候选必须在指定 Windows 11 参考设备运行，至少 4 个现代 CPU 物理核心、16 GiB 内存和 SSD。首次正式校准时，在命令行和发布候选说明中填写以下不可省略字段；当前仓库不把未知开发机信息冒充参考机事实。

- reference device id
- CPU 完整型号、物理/逻辑核心数
- Windows edition、version、build
- 总内存 bytes
- 系统盘/数据盘介质（SSD、NVMe 或 SATA-SSD）
- Python、`transbridge.__version__`、psutil/pytest 版本和 `time.perf_counter` 计时器
- 防病毒实时扫描状态、数据是否在本地磁盘、任何可观察到的外部 I/O 等待

脚本会自动采集可用字段；CPU 型号、物理核心或内存无法可靠探测时必须显式传入覆盖值。磁盘类型始终要求显式输入。manifest 的 `reference_requirements_met=false` 时，该结果只能诊断测量链，不能作为已校准或性能达标证据。

## 缓存与五次运行规程

- 每个正式场景固定执行 5 次，保留每次原始 phase 样本和 P50/P95/mean/min/max；不能只保存聚合值。
- `full-cold`：每次清除项目结果缓存和本进程对象缓存并执行 GC。普通进程无权可靠清除 Windows 文件缓存，因此实际边界记录为 `os_file_cache=not-cleared-recorded-boundary`；若指定参考机采用管理员级清缓存步骤，必须在 RC 说明中逐字记录并保持跨候选一致。
- `full-warm`：首次装载后保留项目与进程缓存；5 个正式样本采用相同准备步骤。
- `repeat`：Project/Variant revision、来源 fingerprint、构建配置完全不变，保留并验证既有结果。
- `changed-10pct`：只改变不超过 regular 数据集 10% 的证据，manifest 必须记录复用与重算数，最终 digest 与同输入全量构建比较。
- `query`、`compare`、`report`、`changelog` 使用同一 dataset digest 和版本历史。查询、比较、报告、更新日志不得共享未记录的跨场景可变缓存。

外部 LLM 等待和外部 I/O 等待必须直接计入各自 phase 桶，不能通过总耗时减法倒推。LLM 关闭时仍写入 `external-llm-wait=0` 和 disabled 状态。FR5.16.35 的本地构建耗时只汇总 capture、parse、assemble、extract、reduce、persist；query、report、changelog 各自保留，外部等待不纳入本地预算。

## 命令与证据保留

CI 只运行缩小规模合同：

```powershell
uv run pytest tests/performance/terminology -m "not slow" -q
```

在参考机生成场景证据（示例值必须替换为该参考机真实信息）：

```powershell
uv run python scripts/benchmark_project_terminology.py --profile formal --scenario full-cold --scale regular --disk-type NVMe --reference-device-id win11-ref-01
uv run python scripts/benchmark_project_terminology.py --profile formal --scenario full-warm --scale regular --disk-type NVMe --reference-device-id win11-ref-01
uv run python scripts/benchmark_project_terminology.py --profile formal --scenario changed-10pct --scale regular --disk-type NVMe --reference-device-id win11-ref-01
uv run python scripts/benchmark_project_terminology.py --profile formal --scenario full-cold --scale stress --disk-type NVMe --reference-device-id win11-ref-01
uv run python scripts/benchmark_project_terminology_ui.py --output docs/test-reports/terminology-benchmarks/results/YYYY-MM-DD-build/supplemental-ui.json
uv run python scripts/aggregate_project_terminology_benchmarks.py --regular-dir <regular-dir> --stress-dir <stress-dir> --supplemental-evidence <supplemental-ui.json> --output <release-bundle.json>
```

自动探测失败时补充 `--cpu-model`、`--physical-cores` 和 `--memory-bytes`。默认结果写入 `results/YYYY-MM-DD-<transbridge-build>/<scenario>.json`；已有文件不会静默覆盖，只有明确传 `--overwrite` 才能替换。

每个发布候选保留独立目录，至少包含所有正式场景 JSON、该候选的 build/commit 标识、参考机说明和异常说明。不得用后一次运行覆盖先前候选。JSON 使用稳定字段顺序和明确 seconds/bytes 单位；顶层 `artifact_digest` 是排除该字段自身后对 canonical JSON 的 SHA-256，可用于传输和归档校验。

diagnostic smoke 永远 gate-ineligible。正式单场景 manifest 只证明该场景证据合同完整，`release_gate_eligible` 始终为 false；只有 regular/stress 20 个场景、digest-bound supplemental evidence 和预算聚合都完整时才能生成 release bundle。release bundle 仍须通过全部 SHALL 与 stage 所需 additional checks，不能因文件存在而开启门禁。
