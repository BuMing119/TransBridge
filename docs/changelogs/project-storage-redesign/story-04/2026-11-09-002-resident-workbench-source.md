# S04：当前来源后台完整加载与前台驻留

- 日期：2026-11-09。
- Epic/Story：project-storage-redesign / S04。
- 依据：用户反馈快速拖动仍反复出现“正在加载”，要求当前页面全部加载；对应 [FR32.8](../../../requirements/project-storage-redesign.md) 和[计划](../../../../plans/project-storage-redesign/plan.md)。
- 分工：独立 GPT-6.1 Sol / high Agent 分别实现生产代码和回归测试，主会话审查、总体测试和文档收尾。

## 行为变化

此前稳定逻辑行修复解决了列表循环，但仍按视口读取并淘汰页面，快速跳转会等待补页。这次打开当前来源后先显示首屏，随后自动在后台读取该来源全部词条，完成后保留前台驻留投影。即使停在顶部不滚动，也会完成加载；完成后的首尾跳转直接访问内存，筛选和排序复用同一份来源数据。

其他来源不预加载；切换来源取消旧任务并释放旧投影，关闭时释放协调器、表格与展示器的引用。后台结果核验来源、revision 和读令牌，迟到结果不能覆盖加载期间的新编辑、保存或来源切换。保存继续走权威稀疏 patch，前台完整加载不会变成整源写回。

表格只绘制可见单元格，普通滚动不重建全量模型。译名方案预览仍按可见区域计算，仅替换预览内容和重绘，不重复安装整个列表。初次完整加载尚未完成时，远端行仍可能短暂显示加载占位；本次不承诺十万条瞬间就绪。

## 文件与职责

按本轮开始时的九个文件副本核对实际增量，不把此前 FR32 或其他并行工作树改动归入本轮。

- `src/transbridge/ui/workbench/resident_rows.py`：新增完整前台结果、身份索引、后台加载/筛选与稀疏状态覆盖；tuple 和索引在后台构建。
- `src/transbridge/ui/workbench/project_paging.py`：主动启动当前来源加载，管理取消、revision、驻留结果和筛选；滚动复用完整结果，译名预览仅更新可见范围。
- `src/transbridge/ui/project_rows.py`：提供 `pending_view_states`，从权威工作副本获取当前来源修改，更新驻留投影而不重新读取全部数据。
- `src/transbridge/ui/workbench/translation_model.py`：直接读取驻留索引，保留编辑行临时覆盖和稳定 Qt 索引。
- `src/transbridge/ui/workbench/translation_table.py`：直接复用完整身份索引和选择区间；关闭时释放自身引用，不清空共享索引对象。
- `src/transbridge/ui/workbench/table_presenter.py`：接收驻留 tuple，避免每次展示重建行字典。
- `src/transbridge/ui/workbench/step2.py`：连接驻留表格、译名预览重绘和关闭释放；只修改既有接线。
- `scripts/benchmark_project_storage.py`、`scripts/compare_project_storage.py`：分别计时首屏与当前来源完整驻留，退出时等待新增读取 worker。
- `tests/integration/gui/test_project_paged_workbench.py`：自动加载、阻断数据库范围读取后的远跳/筛选/排序、稀疏保存及保存/切换/关闭竞态回归；更新既有页窗口假设。
- `tests/performance/test_project_storage_scaling.py`：首屏仍门控验证只解码 100 行；另测自动完整驻留、内存筛选、心跳和 RSS，不解码其他来源。
- FR32.8、ADR-054、S04 计划与索引更新为当前合同，既有增量记录保持原样。

职责审查：分页协调器 439 行、33 个方法，仍负责当前 UI 范围生命周期，数据构建和过滤抽出至 117 行的 resident_rows；TranslationTable 395 行、34 个方法仍围绕视图/选择。既有 Step2 752 行、48 个方法仅改接线，未添加职责；后续新增工作台职责前必须拆分。过滤/排序会临时持有第二份引用 tuple 和索引，但不重复解码来源数据。

## 主会话验证

Windows 沙箱使用 `UV_NO_CACHE=1`、`QT_QPA_PLATFORM=offscreen`，通过 `uv run --offline --no-sync python -` 调用 `pytest.main`。临时根仅属于本任务，包装临时目录创建以规避沙箱目录权限及路径长度限制；未修改业务权限。

- 首轮工作台 GUI、对话顺序、表格模型/阅读/排序、编辑恢复/关闭与切片：85 passed，26.52 秒。
- 最终生产版本的 `tests/ui/workbench`、story07/draft_focus/filter_layout/context_layout、characterization workbench contract、download projection、project open operation/guards，加三个 translation_table 文件、两个 inline_edit 文件及 workbench_slices：174 passed，7.35 秒。
- `tests/performance/test_project_storage_scaling.py -s`：3 passed，51.82 秒。
- `uv run --offline --no-sync ruff check src tests scripts/benchmark_project_open.py scripts/benchmark_project_storage.py scripts/compare_project_storage.py` 与相同范围的 `ruff format --check`：通过，2533 文件已格式化；`git diff --check` 通过。
- `uv run --offline --no-sync python scripts/compare_project_storage.py --source-root . --rows 1261 --sources 1 --repeats 1 --output <本记录旁 resident-probe.json>`：退出码 0，首屏物化 100 行，随后驻留完整 1261 行。

独立测试 Agent 的红证：旧实现打开 600 条来源后停在顶部，等待四秒仍无法访问全部条目。新回归阻断 `ProjectStore.read_page/iter_entries`，快速跳转、筛选排序和保存仍成功，保存只读取目标条目；受控迟到结果覆盖保存、来源切换、关闭三个场景。

## 实测与限制

原始样本：[存储与十万条 Qt 基准](2026-11-09-resident-scaling.json)、[独立打开探针](2026-11-09-resident-probe.json)。每个 Qt 场景为创建合成数据后首次打开加五次重载，未清空系统文件缓存。下列为五次重载中位数，心跳和 RSS 为这些重载的最大值。

- 十万条分在 80 个来源，当前来源 1250 条：首屏 31ms，之后完整驻留 63ms，驻留后筛选 156ms；加载期间心跳最大间隔 32ms，进程采样 RSS 峰值约 145MiB。
- 单个来源十万条：首屏 47ms，之后完整驻留 2.797 秒（最慢 3.454 秒），驻留后筛选 172ms；加载期间心跳最大间隔 94ms，进程采样 RSS 峰值约 244MiB。
- 10k/100k 存储与增加 1000 项独立任务历史：首批读取仍为 3800 VM steps，单条保存修改 3 条数据库记录、16KiB 页面，journal 12824 字节，无修改保存零写入；未扫描任务库。

驻留时间从释放首屏测试门控起计，需与首屏时间区分；筛选时间包含既有搜索防抖。RSS 是整个测试进程采样值，不是仅来源净增量或严格瞬时峰值。测试使用 offscreen Qt 和合成数据，不代表所有用户工程及机器。完整加载按当前来源规模使用内存，切换再进入会重新准备该来源。

本轮不改变数据库格式与旧格式拒绝策略，未重跑完整全库 pytest、真实用户工程冷开、远端 LLM 或发行构建；既有三个无关失败仍见原 FR32 实施记录。未提交 Git，未迁移或清理用户项目。

最终集成复验：`tests/integration/gui/test_project_paged_workbench.py tests/ui/test_workbench_dialogue_reading_order.py`，23 passed，11.22 秒；包括新增译名方案下的首/中/尾快速跳转，覆盖译文正确，基础内容不变，无数据库范围读取、模型重置或重新安装。结合最终 174 项相关回归和 3 项性能测试，本轮最终分组共 200 项通过。再次全库 Ruff/格式检查通过，2533 文件；本轮临时测试根、日志和九份代码基线副本已清理。
