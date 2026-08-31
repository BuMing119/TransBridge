# 词条弹窗与任务／话题关联编辑

- 状态：Story 01–04 已完成，XT 式记录导航验证通过（2026-08-31）。
- 对应请求：双击词条弹出独立编辑窗口，不在主导航常驻；左侧改为 XT 式记录标识及 SCEN 场景，EET 的任务关联区域置灰。
- 约束：ADR-017；复用 dialogue-tree-order 的父 DIAL 与源顺序，不改变文件格式或条目身份。

## 范围

主导航仅保留开始、工作台、ParaTranz。双击工作台任意词条的数据列（复选框除外）打开非模态“词条编辑”窗口，原列表与当前位置保留。重复打开复用同一窗口并定位新词条，不新增常驻页面。

插件的 QUST/DIAL/INFO 词条在弹窗左侧按当前任务平列话题／场景记录，右侧显示相关文本，底部展示完整原文与译文编辑区。普通词条同样可弹窗编辑，没有任务关联时左侧置灰。纯 EET 可编辑译文，但任务树禁用并说明原因；插件叠加 EET/XT 译文不影响任务树。F2 保留译文行内编辑。

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
- 应用译文、刷新与场景内上一条/下一条保持 SCEN 选中；主表再次双击时回到对应 DIAL，普通任务导航不重复经过场景引用的词条。
- 保留完整 EntryKey 定位及来源隔离；缺少原始来源的旧数据从词条身份恢复内部标识，不猜造场景节点。
- 文件：parser/plugin/dialogue_catalog.py、application/dialogue 的标签/索引/加载器、ui/dialogue 的模型与控制器，以及解析、索引、加载器、UI 聚焦回归。
- 二进制字段依据：[xEdit TES5 定义](https://github.com/TES5Edit/TES5Edit/blob/dev-4.1.6/Core/wbDefinitionsTES5.pas)。SCEN 动作内 PNAM 是 Package，只有动作之外的 PNAM 才是父 Quest；仅 Dialogue 动作的 DATA 作为 DIAL 引用。

## 验证与兼容性

- 最终相关回归：140 passed；Ruff check、Ruff format --check（1,105 个文件）及 Git diff --check 通过。
- 验证命令：`uv run --offline --no-sync pytest tests/parser/test_dialogue_catalog.py tests/parser/test_plugin_tree_order.py tests/application/test_dialogue_index.py tests/application/test_dialogue_loading.py tests/ui/test_dialogue_scenes.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_authority.py tests/ui/foundation/test_visual_style.py tests/ui/test_modern_workbench_visual_shell.py tests/ui/test_translation_table_sorting.py tests/ui/test_step2_incremental_rendering.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_theme_migration.py tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/integration/gui/test_app_context_projection.py -q -p no:cacheprovider`。
- 规范检查：`uv run --offline --no-sync ruff check src tests`、`uv run --offline --no-sync ruff format --check src tests`、`git diff --check`。
- 对本机 HLIORemi.esp 只读验证：3,252 个 DIAL、378 个 SCEN，完整 MainWindow 的鼠标双击能定位 `DIAL {Scene} [0529349A]`，点击 `SCEN {HLIORemiFollowerJzargo03} [052A791E]` 展示两条关联对白。离屏截图检查 DIAL/SCEN 标识、主导航仍为三项、EET 置灰且右侧可编辑。
- 真实插件的快照重建与直接解析索引一致。单次本机测量：已有插件建立目录/索引约 0.091 秒，快照解析/索引约 0.517 秒，复用缓存建立索引约 0.045 秒；仅作冒烟证据，不是通用性能承诺。
- 未在用户实际桌面执行人工验收，未修改插件/项目数据，未提交、发布或构建安装包；联网仅核对 xEdit 字段定义。使用现有 uv 环境，无依赖/锁文件/schema 改动。
- 本次不重跑全仓库测试。此前全 UI 扩大回归的本地化模板缺项和菜单收起时序问题属于已有记录，不作为本次弹窗验收的通过证据。
