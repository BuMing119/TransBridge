# S16：术语状态、分页与冲突证据读取性能

- 日期：2026-11-13
- Epic：project-terminology-build-versioning-reporting
- Story：S16，见 [计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 范围：本轮读取性能修复；不把工作区已有 LLM、导出、布局及其他模块改动归入本记录。未提交、未发布。

## 问题与结果

打开术语工作台时重复解码完整构建、草稿和版本，隐藏列表与方案映射也提前读取；冲突分页和单组详情加载全部证据。现在主页面读取轻量状态，进阶窗口显示时才加载列表及映射，冲突次数按当前页统计，展开证据只读选中组。任务准备只取构建引用。

仍使用 SQLite 及原有序列化格式，无数据迁移，不删除用户数据。翻译消费继续使用原有完整快照校验；本轮分页快路仅供浏览，保留当前版本、方案修订、跨连接变化、游标失效和取消保护。

## 文件与符号

路径均相对仓库根目录。

- 新增 `src/transbridge/application/terminology/workbench_status.py`：显式 BuildStatus、DraftStatus、WorkbenchStatus，只携带界面所需摘要。
- 新增 `src/transbridge/persistence/terminology/workbench_status.py`：SQLite JSON 标量投影及复核计数，不反序列化完整领域快照；兼容旧发布路径未填充 version_conflicts 的记录。
- 新增 `src/transbridge/persistence/terminology/conflict_reads.py`：ConflictReads 的分页、引用、单组详情、证据及次数查询；复用既有冲突投影与证据去重身份。
- 新增 `src/transbridge/persistence/terminology/browse_pages.py`：生效术语紧凑索引、方案覆盖、当前页批量 SQL 与对象解码；最多两个索引、各八组筛选。
- 新增 `src/transbridge/persistence/terminology/draft_browse_pages.py`：草稿一次元数据扫描保留序列化行，当前页才解码；避免每页重新解析整份草稿。
- 修改 `src/transbridge/persistence/terminology/repository.py`：轻量 latest_build_ref/effective_ref、workbench_status、draft_browse_page 与 conflict_reads 委托；状态投影检测跨连接更新并重试。主仓储超过规模阈值，本轮仅增加薄委托，新职责均放独立模块；后续新增仓储职责仍应继续抽取。
- 修改 `src/transbridge/persistence/terminology/effective.py`：公开可选浏览分页端口。
- 修改 `src/transbridge/application/terminology/browse_queries.py`：优先使用可选分页端口，保留内存适配器等既有回退。
- 修改 `src/transbridge/application/terminology_profiles/browse_queries.py`：捕获方案修订并检查读令牌，变化时重试。
- 修改 `src/transbridge/application/terminology_profiles/effective.py`：提取已有方案快照身份构造，分页与原消费使用同一身份规则，未引入新的哈希语义。
- 修改 `src/transbridge/bootstrap/terminology_workloads.py`：工作台摘要、轻量构建/版本引用及任务准备读取。
- 修改 `src/transbridge/bootstrap/terminology_review.py`：复核计数、详情、决定服务改走定向查询，保存前不读取整份构建证据。
- 修改 `src/transbridge/ui/tools/terminology/asset_snapshot.py`：主页面一次读取状态，移除默认完整方案映射读取，保留旧适配器回退。
- 修改同目录 `workflow_state.py`、`build_projection.py`、`extraction_options.py`：支持显式摘要并保持旧提取模式恢复语义。
- 修改同目录 `asset_presentation.py`、`object_views.py`、`source_browser.py`：延迟隐藏的方案映射展示；新增 `source_context_loader.py`，复用 AsyncActionRunner、取消和关闭保护，失败可见。
- 修改同目录 `term_browsing.py`：隐藏列表标脏，显示时才绑定读取；可见列表刷新保留已有分页机制。
- 修改同目录 `workflow_controller.py`：主页面不读取完整映射时，从当前方案控制器获取选择状态。
- 修改同目录 `conflict_evidence_panel.py`：仅统计当前页、按需读取一个冲突，切换/收起取消旧查询，迟到结果不覆盖当前选择。
- 新增 `tests/persistence/terminology/test_workbench_status.py`、`test_status_consistency.py`、`test_conflict_reads.py`、`test_browse_pages.py`、`test_draft_browse_pages.py`：禁止完整解码、跨连接变化、旧发布兼容、分页一致性、批量查询次数、取消和版本失效。
- 新增/修改 `tests/ui/tools/terminology/test_source_context_loader.py`、`test_source_browser.py`、`test_conflict_evidence_panel.py`、`test_extraction_mode_restore.py`、`test_effective_browsing.py`、`test_browsing_failures.py`：延迟读取、真实 SQLite、迟到结果和模式恢复回归。
- 修改同目录 `test_filter_interactions.py`、`test_operation_feedback.py`、`test_workflow_routes.py`、`test_workbench_simplification.py`：测试先打开进阶视图再断言数据；补足方案状态；关闭前等待真实保存与资产刷新完成，避免待保存确认中止测试。
- 更新本计划 S16、plans/INDEX.md、docs/changelogs/INDEX.md；不改既有增量日志。

## 性能证据

同机 Python 3.12.12、现有 uv 环境、合成数据，前后各五轮串行；冷启动指新仓储/应用缓存，不清空 OS 缓存。单位为毫秒，中位数；括号为修复后最小～最大值。

- 1 万术语、5 万双语证据、300 冲突：页面资产读取（传入浏览服务）4532.86 → 255.41（250.43～267.27）。完整方案映射延后至实际打开进阶窗口，此项是页面路径工作量减少。
- 同场景最新构建引用：815.93 → 0.29（0.28～0.86）。
- 同场景复核计数：1272.22 → 275.62（265.00～287.40）。
- 同场景冲突分页 100 组：2614.83 → 20.96（17.45～37.39）。
- 同场景单组详情：1321.88 → 41.01（39.67～42.56）。
- 旧面板全量证据读取/统计约 1055.61；新路径当前 100 组次数统计 170.60（164.44～190.12），选中组证据 2.63（2.44～4.14）。工作范围不同，不作为同一算法加速倍数。
- 5 万术语生效列表同步冷首屏：2871.89 → 892.44（871.19～996.09）；草稿冷首屏 1109.86 → 837.55（821.45～946.53）。
- 5 万术语 Qt 查询到 page_loaded：9165.03 → 873.50（834.06～962.66）；1 万条 1710.55 → 179.63（161.42～222.68）。不含表格绘制。
- 5 万条 Qt 最大定时器间隔的中位数：约 202.5 → 79.26（53.74～81.70）；包含调度及 GIL 争用，不等同纯 CPU 阻塞。
- 1 万草稿修改一条：约 1054.5 → 859.52（852.08～971.56）。仍有完整草稿校验/写入成本。
- 代价：分页对象改为按需解码，5 万条暖翻页生效列表约 0.90 → 3.52 ms、草稿约 0.26 → 2.12 ms。避免完整对象图，保留毫秒级交互；不声称所有指标均下降。
- 完整构建持久化约 1400.76 ms、完整构建读取约 796.93 ms，未针对这些必要全量路径做结构迁移；未复测真实 LLM 或宣称构建全流程提速。

复测命令：

```powershell
uv run --no-sync --cache-dir .tmp-perf-fix/uv python scripts/benchmark_terminology_storage.py --workspace .tmp-perf-fix/bench-storage-final --output .tmp-perf-fix/storage-final.json --sizes 1000 10000 50000 --edit-sizes 1000 10000 --repeats 5
uv run --no-sync --cache-dir .tmp-perf-fix/uv python .tmp-perf-fix/stages.py
uv run --no-sync --cache-dir .tmp-perf-fix/uv python scripts/benchmark_terminology_browse_ui.py --workspace .tmp-perf-fix/bench-ui --output .tmp-perf-fix/ui.json --sizes 10000 50000 --repeats 5
```

原始五轮 JSON、比较 JSON 与复测脚本归档在本机会话产物 `C:/Users/admin/.codex/visualizations/2026/11/13/01a1d364-cbe6-79c3-91ce-d3c6c77cceaa/terminology-performance-after/`；修复前在相邻 `terminology-performance/`。临时合成数据库不进仓库并在收尾删除。

## 验证

- `uv run --no-sync --cache-dir .tmp-perf-fix/uv pytest tests/persistence/terminology tests/contracts/terminology tests/application/terminology tests/application/terminology_profiles tests/integration/bootstrap/test_terminology_conflict_judgment.py tests/integration/bootstrap/test_terminology_llm_build.py -q --basetemp .tmp-perf-fix/backend --tb=short`：495 passed。
- UI 核心 10 个文件（asset_refresh、browsing_failures、effective_browsing、conflict_evidence_panel、source_browser、source_context_loader、extraction_mode_restore、filter_interactions、operation_feedback、workflow_guidance）：57 passed。
- UI workflow_routes、workbench_simplification：27 passed。此前全集合两次未正常结束，不计为通过；定位为测试过早关闭时待保存确认，修正等待后单独回归通过。
- `tests/persistence/terminology/test_conflict_reads.py` 加 `tests/integration/bootstrap/test_terminology_task_wiring.py`：11 passed（前 3 项与上述重复）。
- 最后将生效页逐条 SQL 改为一次批量查询后，browse_pages、draft_browse_pages、application 的 browse_queries、profile_browsing：29 passed。
- 使用现有 uv 环境中的 `.venv/Scripts/ruff.exe check src tests` 与 `format --check src tests`：通过，2593 文件格式一致；相关 diff 空白检查通过。
- Windows 临时项目硬链接测试在获准的非沙箱执行中验证；初次沙箱权限错误未计为功能失败。未运行整个仓库测试、真实 API、用户数据库测量或打包。

## 遗留边界

大型草稿保存仍整份写入，完整翻译快照验证与进阶方案映射仍有全量成本；保留正确性，不在本轮更换存储格式。分页元数据索引与旧 TermBrowseIndex 存在少量筛选/游标逻辑重复；后续需要扩展筛选时应抽取共享元数据索引契约，避免改变本轮稳定的领域对象路径。五万条场景仍有约 79 ms 的事件循环间隔，不能宣称完全无卡顿。
