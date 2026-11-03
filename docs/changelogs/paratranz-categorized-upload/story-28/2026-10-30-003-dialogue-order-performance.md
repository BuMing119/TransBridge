# S28：降低首次关系提取与表格重复排序成本

日期：2026-10-30（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S28。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局)、[开发说明](../../../dev/paratranz-categorized-upload.md)、[前轮内部显示](2026-10-29-002-plugin-dialogue-internal-display.md)。

## 原因与结果

用户授权 bm-pilot 优化首次排序等待和日常表格刷新。关系提取原先为整个插件的每个记录建立对象和列表；界面即使只改译文，也会重复检查或建立全表排序。本轮压缩身份索引、复用不可变关系与完整来源布局，并将完整秩和初始行位置准备移到 worker。重复刷新复用显示索引，小编辑仅在影响当前排序值时重新排序。

排序规则和输出保持一致：不改变权威 collection 顺序、任何词条字段、内部匹配或同一 DIAL 内 INFO/多响应顺序；不改变 AI 助手查询、分页和默认批任务顺序。没有加入 SCEN 阶段推断、XML 运行时输入、磁盘缓存、依赖或 schema 变更。

## 逐文件增量

本轮开始前已将当前未提交 `src/`、`tests/` 和相关文档复制到独占临时目录作为基线。下列变化相对该基线，不能把工作区全部相对 HEAD 的差异归入本轮。

生产路径相对 `src/transbridge/`：

- 修改 `parser/plugin/dialogue_order_binary.py`：提供可选紧凑身份索引，所有记录仍参与类型、删除、重复身份检查，仅相关四类生成详细 `EvidenceRecord`；物理 ordinal 和原迭代 API 保持。
- 修改 `parser/plugin/dialogue_order_evidence.py`：删除全插件逐身份列表及第二套对象索引，引用校验使用紧凑身份状态；证据模型、版本、诊断和序列化结构不变。
- 修改 `application/io/plugin_dialogue_order.py`：已验证 metadata 按摘要、版本和精确 payload 对象复用；保留 payload 防止对象 ID 重用，不保留源文件字节。仅深度不可变 payload 可缓存，每个缓存最多四项；不同 payload 仍校验，可变旧输入每次重验。初始插件捕获可直接复用自产证据。
- 新增 `application/io/dialogue_reading_cache.py`：最多保留两组、合计 250,000 个结构项的完成布局；以精确只读来源 tuple 和证据对象隔离，不持有可编辑词条或插件字节，可变 metadata 绕过缓存。
- 修改 `application/io/dialogue_reading_view.py`：完整来源查询先复用上述完成结果；未命中仍执行原布局，取消检查覆盖命中及发布边界。工作台与对话树传入同一完整来源时复用结果。
- 修改 `ui/workbench/dialogue_reading_order.py`：worker 生成不可变 ranks、完整来源身份与预排 source rows；交付和同来源刷新复用这些结构，原代际、关闭和未提交编辑保护保持。
- 修改 `ui/workbench/translation_table_sorting.py`：默认排序按成员及 ranks 失效；手动排序按当前列、方向和完整当前值失效，同值继续使用默认秩。输入 keys 转为 tuple，旧调用兼容，关闭清理全部缓存。
- 修改 `ui/workbench/entry_refresh.py`：保留结构和筛选检查；译文或状态改变未影响当前列排序值时，不再调用全表排序检查，显示仍安装当前词条对象。
- 修改 `ui/workbench/translation_table.py`：直接安装 worker 索引，复用未变的行定位映射；独立检查 legacy label key，避免同 EntryKey 的显示 ID 变化后定位陈旧。
- 修改 `ui/workbench/translation_model.py`：使用已提供的来源身份复核显示行，避免重复构造两套全表显示身份；仍更新当前 session、发出内容通知并保护持久 Qt index。

测试与工具：

- 修改 `tests/parser/test_dialogue_order_evidence.py`：补齐无关记录 live/deleted/wrong/missing、跨类型重复及三次重复、物理 ordinal、原迭代 API 与两种输入取消。
- 新增 `tests/contracts/io/test_dialogue_order_cache.py`：覆盖已验证 metadata 复用、同摘要不同 payload、可变输入变更、取消与容量淘汰。
- 修改 `tests/contracts/io/test_dialogue_reading_view.py`：覆盖相同 frozen 来源复用、来源成员/证据变化、可变词条或 metadata 绕过缓存及容量淘汰。
- 修改 `tests/application/test_dialogue_loading.py`：验证对话树复用工作台已完成的完整来源布局，节点和条目定位不变。
- 修改 `tests/ui/test_translation_table_sorting.py`：覆盖重复默认/手动显示、当前对象、过滤/增删、预排索引、同值秩、可变 keys、legacy ID、原地译文及标签变化。
- 修改 `tests/ui/test_entry_refresh.py`：覆盖无关字段和大小写等价编辑不重排、当前列变化必须重排。
- 修改 `tests/ui/test_workbench_dialogue_reading_order.py`：验证后台准备秩及完整来源位置、刷新复用与生命周期。
- 新增 `tests/ui/test_dialogue_order_assistant.py`：屏幕真实重排后，助手仍按原集合分页，按身份修改正确词条，并定位到新显示行；其余词条及来源字段保持。
- 新增 `scripts/benchmark_dialogue_order.py`：可指定源码树与已保存 artifact，五轮测量冷提取、metadata 冷/热读取、布局冷/热计算、首装和重复刷新；仅输出耗时和摘要。禁止网络、XML、`.transbridge` 读取及输出目录外写入。提取专项明确剔除 metadata，防止新 artifact 被误测成缓存命中；metadata 独立测量。

文档：更新 S28 计划、开发说明和两个直接索引；新增本增量。既有增量和 QA 报告保持不变。所有本轮生产模块均少于 500 行，最大主类为 `TranslationTable` 的 28 个方法，没有新增尺寸例外。

## 性能验证

环境为同台 Windows、Python 3.12.12、现有 uv 环境与锁定依赖、Qt offscreen。基线是本轮开始前的实际工作区代码，HEAD 为 `512e0b4ba4de458d281fe5d454d94f46df10fa96`，并非直接使用 HEAD 内容。测量期间暂停其他 agent 测试；宿主负载仍有波动，数字不是跨设备承诺。

输入：249,752,131 字节 captured plugin、64,998 条来源词条；51,374 条关系记录、7,804 条链接、1,502 组话题映射。source SHA-256：`e198c3b85e5e48e0c92a6580d8f66e644256b68d812ee32b61735cf9b753df73`。读取已存 artifact，不打开原插件路径或 XML，不写真实工程；测试期间的原文和译文不导出为报告。

最终各五轮中位数及最小～最大值：

- 冷关系提取：**10.112 秒（9.222～10.748）→ 3.953 秒（3.880～4.476）**，中位下降 60.9%。五轮 GC 各代 collections 从 `[12497, 1136, 17]` 降为 `[1442, 131, 1]`；未把 collection 次数当作内存用量。
- 第一次将阅读顺序应用到原序表格：**246.34 毫秒（240.23～272.94）→ 77.76 毫秒（45.78～81.88）**。每轮计时前恢复原序并处理 Qt 事件；新版的秩/预排索引由后台准备，不计入主线程安装耗时。
- 同条件重复表格刷新：**206.21 毫秒（198.58～210.90）→ 35.87 毫秒（33.67～43.41）**，中位下降 82.6%。
- 重复默认索引查询：31.61 毫秒（28.91～80.34）→ 16.03 毫秒（13.90～16.60）；重复原文列排序：179.26 毫秒（170.48～191.42）→ 64.12 毫秒（63.46～121.76）。
- INFO 子集刷新：126.19 毫秒（107.13～150.28）→ 19.67 毫秒（18.49～119.74）。新版首轮仍需投影，随后复用，不能据中位数声称首次筛选只有 20 毫秒。
- 布局冷计算：2.688 秒（2.215～2.726）→ 2.947 秒（2.495～3.123），**未证明冷布局本身提速**。同一来源重复布局：2.721 秒（2.183～2.761）→ 0.007 毫秒（0.003～0.023）；这是已完成结果的复用。
- 已存 metadata 首次解码/检查：442.49 毫秒（402.46～919.41）→ 576.53 毫秒（496.08～603.98）；新增不可变性检查有首次成本。后续读取：436.85 毫秒（413.60～860.06）→ 0.006 毫秒（0.004～0.025）。
- 后台提取期间，每轮事件泵最大间隔的中位数：967.42 毫秒 → 66.04 毫秒；五轮最坏值：977.34 → 528.19 毫秒。布局阶段最坏值仍约 539 毫秒，**没有达到完全无停顿**；事件泵是响应探针，不等同于人工交互延迟。

早期探索轮冷提取为 5.565 → 2.229 秒、重复刷新 112.72 → 21.70 毫秒；同样明显改善，但与最终轮绝对耗时不同，故不混用两轮最佳值。缓存不可变性检查优化后重跑最终 after；最终证据采用 `final-before.json` 与 `verified-after.json`，临时原始输出在归纳后清理。

完整证据、身份顺序和话题顺序摘要前后一致：

- evidence payload：`3b6a843e6c0844330c90e20544e748ad329d1c85958f5b89b1e7735b3e8e6769`。
- 全部有序 EntryKey：`b241d1496a4a59001dabe9487292db622bf5ac7c24b9f7e73c98f12f200829a1`。
- 全部任务话题顺序：`fb97d5ad77efcd832e06762305d78d6cdc60248e2f67bdeb0f44afb03c42b3e2`。
- 每轮前后 `TranslationEntry.to_dict()` 摘要及原集合身份顺序不变。

可复测命令（基线副本须在修改前创建；输出文件必须不存在）：

```powershell
.\.venv\Scripts\python.exe -B scripts/benchmark_dialogue_order.py --source-root .tmp-dperf/before/src --artifact-dir data/source_artifacts --artifact-id b47d938be4ebc6d855965e1d7242dd6710ec9702dcc3ab2f8b1e612e867a3110 --output .tmp-dperf/final-before.json
.\.venv\Scripts\python.exe -B scripts/benchmark_dialogue_order.py --artifact-dir data/source_artifacts --artifact-id b47d938be4ebc6d855965e1d7242dd6710ec9702dcc3ab2f8b1e612e867a3110 --output .tmp-dperf/verified-after.json
```

以上是分阶段测量，文件恢复与初始对象准备在计时外，不是完整打开项目时间，也不是实际 AI 服务耗时。

## 回归、审查与检查命令

最终主会话组合 **432 passed，29 个既有 deprecation warnings，54.77 秒**；另选助手分页/筛选 **8 passed，88 deselected**。两组无重叠，共 440 个用例；子 agent 的 73/80 项及早期 32 项聚焦结果已被覆盖，不累计。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:UV_CACHE_DIR = 'D:/MyCode/TransBridge/.tmp-dperf/uv'
$env:TEMP = 'D:/MyCode/TransBridge/.tmp-dperf'
$env:TMP = $env:TEMP
$testFiles = @(rg --files tests/ui | Where-Object { $_ -match 'test_(dialogue_|translation_table_|step2_|entry_refresh|interaction_filters|workbench_filter|workbench_dialogue|project_source_hydration|parse_|inline_edit_)' } | Sort-Object)
uv run --no-sync pytest @testFiles tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/contracts/io/test_dialogue_reading_view.py tests/contracts/io/test_dialogue_order_projection.py tests/contracts/io/test_dialogue_order_cache.py tests/application/projects/test_dialogue_order_publication.py tests/application/test_dialogue_order.py tests/parser/test_dialogue_order_evidence.py tests/paratranz/test_dialogue_order_file_retry.py tests/smart_assistant/tools/test_original_match_tool_paths.py tests/smart_assistant/test_immediate_entry_projection.py tests/performance/test_workbench_story07_performance.py -q --basetemp=.tmp-dperf/pt-all -o cache_dir=.tmp-dperf/pc --tb=short
uv run --no-sync pytest tests/smart_assistant/test_agent_tool_integration.py -q -k 'get_visible_entries or FilterEntries' --basetemp=.tmp-dperf/pt-ai -o cache_dir=.tmp-dperf/pc
uv run --no-sync ruff check src tests scripts/benchmark_dialogue_order.py
uv run --no-sync ruff format --check src tests scripts/benchmark_dialogue_order.py
git -c core.safecrlf=false diff --check
```

全库 lint、含基准脚本的 2,333 文件格式检查通过。新增 7 个性能行为用例在独立进程加载 `.tmp-dperf/before/src` 时全部预期失败：6 项旧实现仍对无关/等价编辑调用全表排序，1 项旧实现重复手动排序不复用行索引；同一用例在当前实现全部通过。失败进程打印实际旧模块路径，未修改基线源码。

独立 Astra 静态审查未发现生产正确性问题；指出基准脚本对新 metadata artifact 的冷提取口径问题，已以独立 `binary_snapshot` 修正。当前真实输入本身没有该 metadata，因此既有冷提取数据不受此修正影响。主会话修正新增文件导入/格式问题后，完整检查通过。

## 兼容、清理与未完成项

旧调用接口兼容，只有内存缓存，不需迁移工程；关闭软件后仍需重新建立结果。原始插件关系取证、缺失/坏证据回退、取消和身份检查保持。用户原有 `.transbridge/` 与早期人工核对目录不动；本轮独占 `.tmp-dperf/` 在验证及记录完成后清理，无 commit/push。

实现范围内未完成项：无。已知性能边界：冷布局未提速、首次 metadata 校验有额外成本、大来源布局仍可能出现约半秒响应探针间隔；未把这些报告为已消除。没有执行全仓 pytest、真实云端/LLM、游戏或构建，也没有建立完整导入峰值内存和不同硬件基准。
