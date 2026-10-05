# 词条弹窗与任务／话题关联编辑

- 状态：Story 01–09 已完成；应用重复处理优化通过 156 项回归（2026-10-04）。
- 对应请求：双击词条弹出独立编辑窗口，不在主导航常驻；左侧改为 XT 式记录标识及 SCEN 场景，EET 的任务关联区域置灰。
- 约束：ADR-017；复用 dialogue-tree-order 的父 DIAL 与源顺序，不改变文件格式或条目身份。

## 范围

主导航仅保留开始、工作台、ParaTranz。双击工作台任意词条的数据列（复选框除外）打开非模态“词条编辑”窗口，原列表与当前位置保留。重复打开复用同一窗口并定位新词条，不新增常驻页面。

插件的 QUST/DIAL/INFO 词条在弹窗左侧按当前任务平列话题／场景记录，右侧显示相关文本，底部展示完整原文与译文编辑区。普通词条同样可弹窗编辑，没有任务关联时收起上下文区域。纯 EET 可编辑译文，任务树禁用并收起；插件叠加 EET/XT 译文不影响任务树。F2 保留译文行内编辑。

无内容时不弹出空编辑窗口。左侧采用 XT 式记录标识，显示 QUST/DIAL/SCEN 的 EditorID、类别及 FormID，不把对白用作标题。缺失父话题/任务关系使用显式未知分组，不根据相邻顺序猜测。包含 SCEN 场景节点及其明确话题引用；场景阶段演出、条件跳转和音频不在范围内。

## 实施事实与边界

- CollectionSlot 的 format_id/esp_path/eet_path 及正式登记源只决定任务树能力，不限制普通词条编辑。
- TranslationEntry 的 context 包含任务标识，metadata 包含父 DIAL 和 source_order；旧项目允许缺失。
- 编辑通过既有权威 Variant 命令或 Collection.apply；不直接修改投影字段。
- 主窗口、工作台超过职责审查阈值，仅添加组合入口/信号；完整职责放入 application/dialogue 和 ui/dialogue。
- 窗口由 MainWindow 持有；关闭应用时继续使用现有保存流程及草稿保护，无新持久化格式。

## Story 01：关联索引与来源资格

- 以完整 EntryKey 定位，按任务、父话题聚合，保留多响应与可翻译字段。
- 完整有效的源顺序用于编排；缺失/重复顺序保留集合顺序。
- 没有可翻译 DIAL 的 INFO 仍创建带 ID 的话题节点；未知关系明确标注。
- 文件：application/dialogue/index.py 与聚焦单元测试。

## Story 02：独立弹窗与安全编辑

- EntryEditorDialog 组合独立视图，非模态、可缩放、可复用；不占用主导航或切换主页面。
- 左侧任务选择和树可独立禁用；右侧文本编辑不受 EET 或无任务关联影响。
- 原文只读；译文保留换行和首尾空白，空译文与状态遵循既有语义。
- 未应用草稿跨词条/内容导航保留；关闭按钮、窗口 ×、Esc 以及关闭主应用均有草稿检查。
- 取消关闭保留草稿，确认放弃后再次打开读取已应用译文；应用后主表、项目脏状态和权威数据一致。
- 普通词条支持上一条/下一条；对话按当前任务浏览；跨工程/版本/来源禁止写入旧草稿。
- 后台索引不阻塞弹窗打开，完成时保留当前草稿及光标；忽略过期结果，关闭后不得被异步回调重新弹出。

## Story 03：双击接入与回归

- 直接处理鼠标双击，避免两次点击间表格刷新使 Qt 不发出 itemDoubleClicked；序号、标签、Key、原文、译文、状态均可激活一次。
- 复选框只负责选择，F2 保留行内编辑；未组合弹窗的独立表格仍使用原有行内编辑。
- MainWindow 只组合控制器；主导航移除原有“对话编辑”入口及相应页面可用性代码，保持原有三个页面。
- 覆盖完整 MainWindow、普通词条、EET、插件叠加 EET、空集合、缺关系、多响应、排序、草稿关闭、异步加载、冲突与权威保存。

## Story 04：XT 式记录导航

- 在 parser/plugin 的独立模块提取只读 QUST/DIAL/SCEN 目录及 SCEN 对话动作的话题引用；不制造翻译词条，不修改既有解析输出或持久化 schema。
- 通过 application/dialogue 的后台加载器读取现有 plugin 或 SourceSnapshot.content；按来源对象缓存目录，译文刷新时不重新解析插件。EET 不加载场景。
- 左侧按当前 Quest 平列记录，按 FormID 排列节点；DIAL 采用 EditorID，缺失时显示已解析类别（如 Scene）；SCEN 显示 EditorID/FormID。悬停显示完整标识和关联词条数。
- 右侧 DIAL 保持源记录顺序；SCEN 汇总明确引用的话题文本，不解释条件和演出时序。重复引用不重复计数；无可编辑文本的节点清空底部编辑区，避免误改上一个词条。
- 应用并下一条、刷新与场景内上一条/下一条保持 SCEN 选中；普通应用成功后关闭弹窗返回工作台。主表再次双击时回到对应 DIAL，普通任务导航不重复经过场景引用的词条。
- 保留完整 EntryKey 定位及来源隔离；缺少原始来源的旧数据从词条身份恢复内部标识，不猜造场景节点。
- 文件：parser/plugin/dialogue_catalog.py、application/dialogue 的标签/索引/加载器、ui/dialogue 的模型与控制器，以及解析、索引、加载器、UI 聚焦回归。
- 二进制字段依据：[xEdit TES5 定义](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.6/Core/wbDefinitionsTES5.pas)。SCEN 动作内 PNAM 是 Package，只有动作之外的 PNAM 才是父 Quest；仅 Dialogue 动作的 DATA 作为 DIAL 引用。

## Story 05：应用返回与导航闭环

- 普通应用成功并处理同原文同步后关闭，失败保留草稿及窗口；其他词条草稿必须经统一关闭保护，取消关闭保留它们。
- 应用并下一条后恢复译文焦点；末条改为明确的完成返回动作。普通词条沿用打开时完整筛选/排序结果，显示导航位置；对话继续采用当前任务/场景范围。
- 返回工作台不清除筛选、排序或滚动状态；关闭检查不提前删除草稿，以免后续主窗口关闭失败时丢失。
- 文件：ui/dialogue/controller.py、view.py；workbench 的公开导航/返回接口；tests/ui 的编辑器回归。

## Story 06：本地同原文译文同步

- 当前内容/版本内精确同原文且译文不同的条目作为候选；展示位置、旧译文与状态。空原文/空译文不发起批量传播。
- 有未应用草稿的候选受到保护，已审核候选默认不选；用户选择后批量原子提交，校验版本及词条快照，保留既有高状态。
- 当前条先成功提交；跳过同步不撤销当前条，批量失败明确提示并留窗。
- 独立候选、确认视图及提交模块，避免给控制器添加批量数据职责。覆盖冲突、来源/版本隔离、草稿保护及权威/普通集合路径。

## Story 07：按内容调整编辑空间

- 无任务关联时收起空任务区与冗余单行列表，正文获得主要空间；有任务时恢复上下文与用户调整的分栏比例。
- 不改变复制粘贴、不新增相关菜单、按钮、常驻原文/译文标题或快捷键。
- 文件：ui/dialogue/view.py；新增布局回归测试。

## Story 08：减少编辑热路径全量重建

- 相同投影和集合版本的导航跳过重复同步；来源/版本/集合变化可靠失效，不能忽略外部写入。
- 本地提交完成且权威快照已应用到集合后保留同步凭据；应用返回再打开不得再次全量投影。通知期间发生嵌套修改、集合替换或版本切换时凭据失效，不能把外部变化误判为已同步。
- 纯译文修改保留任务结构索引，避免每次重新排后台索引任务。
- 工作台可局部更新时保留表格实例和上下文；筛选/排序/术语等复杂状态正确回退或重算，不以性能牺牲正确性。
- 独立 projection_sync 与工作台刷新切片，避免扩大超限模块职责；聚焦回归后比较相同合成样本的五次耗时。

## Story 09：减少应用时的重复数据处理（已完成）

- 保持同步应用：检查当前词条 → 既有工程命令提交 → 更新集合和工作台 → 同原文同步询问 → 成功后关闭或下一条。保存、线程和冲突保护不变。
- 提交前直接读取不可变快照，只为本次草稿保留查找结果；不展开整份数据。
- 提交后建立一次状态索引，复用于所有来源集合；未变化词条复用原对象。
- 界面从只读快照读取必要字段；仅复制需要普通 JSON 容器的小型元数据和引用信息。标签采用来源与词条键定位，保留未变化集合，避免重复序列化身份。
- 现有冻结数组只补充只读遍历和长度访问；序列化结果、工程文件格式及修改入口保持不变。
- 不引入后台提交、新修订协议、忙碌锁或新队列；不改复制粘贴，不扩展到工程提交实现。
- 文件：application/projections/models.py、ui/entry_projection.py、ui/dialogue/editing.py、ui/context.py、独立标签读取模块及聚焦回归。超限 AppContext 仅替换已有读取逻辑，标签处理移出，避免新增职责。
- 验证：相同 8,291 条合成数据，完整 apply 调用前后各十次；另一次独立 tracemalloc 测峰值 Python 分配。此场景使用真实 Variant 提交及界面刷新，内存测试工程，无真实磁盘自动保存或人工确认等待，不等同用户工程绝对耗时。
- 阶段：基线、实现、QA 与增量记录均完成。156 项回归、全 src/tests Ruff 与 diff 检查通过；中位耗时 374.699 → 196.815 ms；峰值 Python 分配 26.343 → 14.294 MiB。证据及边界见 [S09 增量](../../docs/changelogs/dialogue-context-editor/story-09-apply-cost/2026-10-04-001-reduce-snapshot-copies.md)。

## Story 01–08 执行记录

- 分析及计划：完成，用户明确排除复制粘贴改动。
- 实现：应用后重开缓存修复完成；复制粘贴及相关入口保持不变。
- QA：本轮 146 项真实提交后重开、外部变更、嵌套通知及关联回归通过，全 src/tests Ruff 通过；8,291 条合成数据五次应用后重开 1.395–6.387 ms（仅离屏重开路径）。此前 173 项相关回归和 41 项投影调用方回归已通过。
- 增量记录：完成，见 [应用后重开](../../docs/changelogs/dialogue-context-editor/story-05-08-editing-flow/2026-10-04-003-reopen-after-apply.md) 及 [Story 05–08](../../docs/changelogs/dialogue-context-editor/story-05-08-editing-flow/2026-10-04-001-editor-flow-and-refresh.md)。仅记录本轮差异，不纳入已有 AI 翻译等改动。

## 验证与兼容性（Story 01–04 历史验证）

- 最终相关回归：140 passed；Ruff check、Ruff format --check（1,105 个文件）及 Git diff --check 通过。
- 验证命令：`uv run --offline --no-sync pytest tests/parser/test_dialogue_catalog.py tests/parser/test_plugin_tree_order.py tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/ui/test_dialogue_scenes.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_authority.py tests/ui/foundation/test_visual_style.py tests/ui/test_modern_workbench_visual_shell.py tests/ui/test_translation_table_sorting.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_theme_migration.py tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/integration/gui/test_app_context_projection.py -q -p no:cacheprovider`。
- 规范检查：`uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests`、`git diff --check`。
- 对本机 HLIORemi.esp 只读验证：3,252 个 DIAL、378 个 SCEN，完整 MainWindow 的鼠标双击能定位 `DIAL {Scene} [0529349A]`，点击 `SCEN {HLIORemiFollowerJzargo03} [052A791E]` 展示两条关联对白。离屏截图检查 DIAL/SCEN 标识、主导航仍为三项、EET 置灰且右侧可编辑。
- 真实插件的快照重建与直接解析索引一致。单次本机测量：已有插件建立目录/索引约 0.091 秒，快照解析/索引约 0.517 秒，复用缓存建立索引约 0.045 秒；仅作冒烟证据，不是通用性能承诺。
- 未在用户实际桌面执行人工验收，未修改插件/项目数据，未提交、发布或构建安装包；联网仅核对 xEdit 字段定义。使用现有 uv 环境，无依赖/锁文件/schema 改动。
- 本次不重跑全仓库测试。此前全 UI 扩大回归的本地化模板缺项和菜单收起时序问题属于已有记录，不作为本次弹窗验收的通过证据。
