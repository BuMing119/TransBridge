# S04：工作台完整列表与稳定滚动

- 日期：2026-11-09。
- Epic/Story：project-storage-redesign / S04。
- 依据：用户报告词条较多时向下滚动会循环，要求前台能完整从头浏览到尾；对应 [FR32.8](../../../requirements/project-storage-redesign.md) 与[实施计划](../../../../plans/project-storage-redesign/plan.md)。
- 分工：延续用户要求，独立 GPT-6.1 Sol / high Agent 分别处理生产代码和测试，主会话审查、执行总体验证和文档收尾。

## 问题与修复

原实现把缓存中的至多四页词条作为表格全部行。向下加载和淘汰前页后重新安装表格，Qt 的行数、相对行号、选择及滚动几何随之变化，无法提供稳定的完整列表。独立测试 Agent 在修复前以真实 Qt 复现：来源有 1261 条，模型只返回 100 行；新增回归在此断言失败。

现在表格从打开时就具有当前范围的完整逻辑行数，使用绝对行号。后台仍按可见区域读取页面，缓存淘汰只影响内容缓存，不删除前台逻辑行。页面返回后更新对应单元格，不重置模型或移动滚动条。用户可连续浏览到最后一条、直接拖到末尾、返回开头；筛选和排序结果使用相同机制。尚未载入的可见单元格异步补齐，不在 Qt `data()` 中读取数据库。

选择按逻辑范围和条目身份保留。跨未加载页的完整选择、弹窗导航和定位复用后台完整查询，标签、状态及编辑操作不再以当前缓存内容代替实际范围。跨缓存多选时，共用标签及标签修改使用已准备好的完整标签数据，保留其他标签和未选词条。编辑中的当前行在缓存切换时保留，迟到页仍核验项目、来源和读令牌。

## 本轮文件变化

本轮以开始时保存的十个目标文件副本核对增量，保留此前 FR32 及其他工作树改动；不将整个未提交工作树归为本次修改。

- `src/transbridge/ui/project_rows.py`：新增 `logical_rows`，将页映射为绝对行号；`missing_pages` / `install_pages` 替代前后翻页位置推算，保留当前视口所需页并淘汰远页。
- `src/transbridge/ui/workbench/project_paging.py`：按视口绝对行加载、合并跳转请求；页面安装只更新内容；默认和筛选排序共用完整逻辑范围；补齐跨缓存选择、标签、弹窗及定位的数据范围。
- `src/transbridge/ui/workbench/translation_model.py`：`install_logical`、完整 `rowCount` 与缓存内容分离；相同范围使用 `dataChanged`，保持索引；未载入行允许导航和选择，读取完成前禁止编辑。
- `src/transbridge/ui/workbench/translation_table.py`：逻辑范围安装、视口计算、选择区间和身份保留；避免页加载重新排序、重置滚动或覆盖正在编辑的行。
- `src/transbridge/ui/workbench/table_presenter.py`：增加 `render_logical` 与相应端口，将逻辑总行数和已物化行分别交给表格。
- `src/transbridge/ui/workbench/step2.py`：接入逻辑表格；完整选择、菜单标签/状态操作与编辑导航交给分页协调器。没有增加职责方法，原 48 个方法保持；743 行的既有规模债务仍在，新范围管理留在分页切片。后续新增工作台职责前须继续拆分。
- `src/transbridge/ui/workbench/entry_refresh.py`：编辑后返回使用完整范围定位，避免缓存相对行定位错误。
- `scripts/benchmark_project_open.py`：绘制探针识别逻辑范围渲染入口，分别统计逻辑行和物化行。
- `scripts/benchmark_project_storage.py`：首批物化行与完整逻辑行分别记录。
- `scripts/compare_project_storage.py`：输出 `materialized_table_rows`，避免把完整逻辑行数误认为已经加载的条目数。
- `tests/integration/gui/test_project_paged_workbench.py`：真实 Qt 逐段正反滚动、直达尾部、过期页与快速跳转、持久索引、跨页选择和编辑；新增真实右键菜单跨缓存标签修改及保存重开验证。旧首批 100 行断言改为完整逻辑行数，同时保留解码数量上限。
- `tests/ui/test_workbench_dialogue_reading_order.py`：按完整逻辑范围验证删除原插件后的跨页对话阅读顺序。
- `tests/performance/test_project_storage_scaling.py`：区分逻辑行数与首批读取量；继续断言首批只解码 100 条且不预载其他来源。
- 需求、S04 计划及相关索引同步当前合同和验证结果；既有实施增量及原始基准不改写。

职责审查：TranslationTable 为 392 行、34 个方法，新增方法仍围绕同一 Qt 视图的逻辑范围、选择与定位；模型、分页协调器和数据库读取分别独立。若后续继续增加非视图职责，应从对应边界拆分，不继续扩大此例外。

## 主会话验证

Windows 沙箱继续使用任务自有临时根，通过 `UV_NO_CACHE=1 uv run --offline --no-sync python -` 调用 `pytest.main`，`QT_QPA_PLATFORM=offscreen`。只对自有根调整临时目录创建模式；未修改生产权限或用户数据。

- `tests/integration/gui/test_project_paged_workbench.py`、`test_workbench_dialogue_reading_order.py`、三个 translation_table 测试文件、两个 inline_edit 测试文件及 `test_workbench_slices.py`：最终 **81 passed，22.51 秒**。
- `tests/ui/workbench`、workbench story07/draft_focus/filter_layout/context_layout、characterization workbench contract、download projection、project open operation/guards：**111 passed，5.01 秒**。
- `tests/performance/test_project_storage_scaling.py -s`：**3 passed，92.56 秒**，覆盖 10k/100k 存储及两个真实 Qt 100k 场景。
- `uv run --offline --no-sync ruff check src tests scripts/benchmark_project_open.py scripts/benchmark_project_storage.py scripts/compare_project_storage.py`：通过；本轮 13 个代码、脚本、测试文件的 `ruff format --check` 通过，`git diff --check` 通过。较早全库格式检查 2531 文件通过；最终复查时，非本轮修改的 `application/io/initial_translation.py` 与 `ui/coordinators/guided_project_coordinator.py` 出现两项格式差异。保留这些其他工作树改动，不顺手格式化，不宣称最终全库格式全绿。
- 独立运行 `compare_project_storage.py --source-root . --rows 1261 --sources 1 --repeats 1`：退出码 0；模型 1261 行、首批物化 100 行，首屏绘制约 37ms。该小样本用于验证测量探针，不能替代十万条性能证据。

新增回归逐条核对 1261 条默认列表和 840 条筛选排序结果，前后移动时滚动范围和逻辑行保持稳定；另验证 611 行跨缓存选择，以及右键菜单修改 521 条所选词条后保存、重开，其余标签和两条未选词条均保留。

十万条样本中，80 来源首批约 62～94ms，单大来源首批约 31～78ms；完整单来源筛选仍约 3.41～8.91 秒。测试断言前台逻辑行数为当前来源全量（1250 / 100000），首批解码仍为 100。样本在合成数据创建后测量，没有清空系统缓存，不宣称真实用户工程冷开耗时。

原始样本：[存储与 Qt 性能](2026-11-09-scroll-scaling.json)、[逻辑行/物化行探针](2026-11-09-scroll-probe.json)。

## 边界与收尾

本轮相关测试无失败；最终全库格式检查的两项非本轮差异如上，未重跑完整全库 pytest、远端 LLM 或发行构建。此前三个无关基线失败仍见原实施记录。本轮未改变数据库格式、来源存储或旧格式兼容策略，未读取或清理用户真实项目；临时测试和基线副本已清理，未提交 Git。
