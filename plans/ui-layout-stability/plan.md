# 动态内容布局稳定专项计划

- **Feature slug**：`ui-layout-stability`
- **状态**：已完成（2026-08-24，UI 全量回归通过）
- **日期**：2026-08-24
- **关联能力**：[modern-workbench-visual-shell](../modern-workbench-visual-shell/plan.md)、[ui-foundation-framework](../ui-foundation-framework/plan.md)
- **触发问题**：长短项目名、文件名、状态说明和异步数据刷新会推动相邻按钮、表格列或固定操作区
- **实现证据**：共享稳定文本组件、Workbench/Shell、ParaTranz、AI Translator/Smart Assistant/Operation 四个分区均已落地；完整 `tests/ui` 回归 `358 passed, 1 skipped`

## 目标

消除生产可达 UI 中由动态文本、动态计数、ComboBox 内容和表格数据刷新引起的非预期控件漂移。在窗口外层尺寸不变时，切换极端长短内容不得改变同一布局模式下固定操作控件的位置；完整信息仍可通过 Tooltip 和无障碍描述获得。

## 非目标

- 不禁止用户拖动表格列、调整窗口或 splitter。
- 不取消用户主动展开/收起筛选、引导、菜单和侧栏造成的有意响应式变化。
- 不把所有对话框改成固定尺寸；展示前的自然内容适配若不影响操作区稳定，保持现状。
- 不改变翻译、同步、项目绑定和任务运行的业务语义。

## 当前事实与统一约束

- Workbench 顶部上下文由 ProjectBar、RemoteTargetView、翻译内容栏三个等权区域组成；普通 QLabel 和 `AdjustToContents` 会反向抬高父布局最小宽度。
- 多个状态栏、工具栏和任务卡片把任意长运行时文本与操作按钮放在同一水平布局中，离屏探针已观察到最小宽度扩大到 6,000～10,000 px。
- ParaTranz 多个表格在刷新后使用 `ResizeToContents`，会根据数据重排列边界并覆盖用户列宽。
- 修复必须沿用 Qt size policy、稳定布局槽和 delegate/header resize mode，不引入定时轮询或业务状态副本。
- 共享工作区已有未提交的 UI Foundation 与 ParaTranz 绑定改动；所有修改必须局部合并，不回滚或重排无关差异。

## 统一验收规则

1. 固定父控件尺寸，在同一界面状态内将短文本替换为超长项目名、文件名、路径、错误或状态信息后，固定操作按钮的 `x/y/width/height` 保持不变。
2. 动态文本不得使目标控件或所属生产窗口的 `minimumSizeHint().width()` 随内容增长。
3. 视觉省略的完整值必须保留在 Tooltip，并同步到 AccessibleDescription 或等价无障碍信息。
4. 表格装载长短两批数据后，固定列和用户已调整列不被自动重置；主要文本列用 Stretch 或稳定 Interactive 配额。
5. 状态/进度区域的出现与消失不得推动同一窗口中的固定操作区；有意展开/收起控件除外。

## Story 总览

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---|---|
| S01 | 共享单行省略文本和稳定宽度辅助能力 | P0 | UI Foundation |
| S02 | Workbench、引导条与壳层状态栏布局稳定 | P0 | S01 |
| S03 | ParaTranz 管理页筛选、表格、详情和进度布局稳定 | P0 | S01 |
| S04 | AI Translator、Smart Assistant 与操作对话框布局稳定 | P0 | S01 |
| S05 | 跨界面几何回归、静态检查与差异审查 | P0 | S02～S04 |

所有 Story 已完成；自动化验证证据见 S05。

## S01：共享稳定布局原语

**验收标准**：提供单行右省略 QLabel，水平最小宽度不由全文决定；公开完整文本属性；resize 后重新省略；调用方可保留更详细 Tooltip。提供按候选文案预留按钮最大宽度的轻量辅助函数。

**文件落点**：`src/transbridge/ui/foundation/components/__init__.py`；新增 Foundation 组件测试。

**实施步骤**：从 RemoteTargetView 的私有实现提取通用组件；保持 `set_full_text` 兼容；用 Qt font metrics 和 sizeHint 计算候选按钮配额；不引入样式颜色。

**测试**：极短/极长文本、resize、full_text、minimumSizeHint、候选按钮文案切换。

## S02：Workbench 与壳层稳定

**验收标准**：项目名、版本名、保存状态、ParaTranz 目标、翻译内容名、引导说明、操作禁用理由、状态栏用户名/项目名和进度文本变化不推动尾部按钮或相邻上下文区；翻译表 Key/#/标签列不因新数据自动重置；渲染进度槽不引起摘要纵向跳动。

**文件落点**：`ui/workbench/_project_bar.py`、`remote_target_view.py`、`widget.py`、`translation_table.py`、`step2.py`、`workflow_actions_view.py`、`progress_view.py`、`ui/guidance/qt.py`、`ui/shell/status_presenter.py` 及相关 `tests/ui`。

**实施步骤**：动态文本改用稳定省略槽；ComboBox 改用最小内容策略；已隐藏但需保留几何的动作/进度控件启用 retain-size；按钮预留候选文案宽；停止每次 render 的按内容列宽重算并保留用户列宽。

**测试**：短/超长项目、版本、集合、说明、禁用理由和 Key 两轮切换；绑定状态、保存状态、API 三态与渲染开始/结束几何对比。

## S03：ParaTranz 管理页稳定

**验收标准**：字符串文件筛选、项目概览、联系人标题、词条详情、讨论标题、页码与贡献统计不因内容长度推动操作区；文件/术语/历史/成员/贡献/字符串表格的数据刷新不重排稳定列；导出和文件任务的进度状态不推动按钮或表格。

**文件落点**：`ui/paratranz/strings_tab.py`、`overview_tab.py`、`mails_dialog.py`、`string_detail_dialog.py`、`issues_tab.py`、`terms_tab.py`、`contribution_tab.py`、`files_tab.py`、`history_tab.py`、`members_tab.py`、`export_tab.py` 及相关测试。

**实施步骤**：应用共享省略文本；为筛选 ComboBox、页码和摘要分配稳定配额；把表格列从全量 ResizeToContents 改为固定/Interactive 辅助列加 Stretch 主文本列；为进度/状态建立保留高度的槽。

**测试**：每类视图装载短/超长两批数据，比较关键 section position/size、splitter 配额、按钮与正文起点几何。

## S04：工具与操作窗口稳定

**验收标准**：AI 运行条件、上传文件摘要、工具结果、任务信息、翻译目标、日志路径、翻译进度与批量配置不会扩大窗口最小宽度或推动动作列；预检结果在固定窗口内滚动，不推动底部确认区。

**文件落点**：`ui/tools/ai_translator/config_view.py`、相关 progress/target/log/batch 视图、`ui/tools/smart_assistant/input_view.py`、`tool_card.py`、`task_monitor.py`、`ui/operations/plan_dialog.py` 及相关测试。

**实施步骤**：状态和路径文本使用省略槽与 Tooltip；上传区显示有界摘要、Tooltip 保留全量文件名；动作列固定配额；预检信息使用有限高滚动内容区，底部按钮保持独立。

**测试**：1/50 个长文件名、长模型/插件/路径/错误/任务描述及多条预检结果下的几何对比。

## S05：QA 与回退门禁

**验收标准**：聚焦几何测试、相关既有 UI 回归、Ruff check/format 和 diff check 通过；未发现由修复引入的业务调用、主题状态、选择或数据刷新回归。

**验证策略**：先分区运行新增测试，再运行 Workbench/Shell/ParaTranz/AI/Smart Assistant 相关测试；最后运行受影响目录 Ruff 和格式检查。真实 Windows 窗口人工复验作为自动几何测试之外的最终视觉检查项。

**实际结果**：

- 新增/聚焦布局与相关回归：`84 passed`；Task Monitor 兼容回归：`30 passed`。
- 完整 UI 测试：`358 passed, 1 skipped, 6 warnings`；警告均为既有 `TranslationEntry` 直接赋值弃用提示。
- 本任务 41 个生产/测试文件 Ruff check 与 format check 通过；`git diff --check` 通过。
- 标准 `uv run` 因本地 uv 缓存目录冲突及仓库 `.venv` 指向已不存在的 Python 3.12 路径无法启动；实际测试使用系统 Python 3.13.5。
- 仓库全量 Ruff 仍存在本任务外历史问题，format check 报告 152 个既有文件待格式化；未把这些无关改动混入本次修复。
- 尚未执行用户真实窗口人工视觉复验。

## 依赖顺序与并行边界

`S01 -> (S02 || S03 || S04) -> S05`。S02、S03、S04 文件边界互不重叠，可并行实现；共享组件只由主会话修改。

## 风险与回退

- 省略后可能丢失可见上下文：Tooltip 与 AccessibleDescription 必须保存全文。
- 固定配额可能影响窄窗口：使用 Ignored/Expanding 与最小内容长度，不用全局硬编码窗口宽度。
- 表格列策略变化可能影响用户习惯：保留 Interactive 能力，主要文本列 Stretch；不禁止用户拖动。
- 若某处内容高度变化本身是用户主动展开行为，保留原行为，不用空白占位伪装。

## 明确假设

- “所有控件漂移”指生产可达界面中由运行时内容变化导致的非预期几何变化，不包括窗口缩放、splitter 拖动和明确的展开/收起交互。
- Windows/PyQt6 是几何回归的权威平台，离屏 Qt 测试用于稳定复现，人工实窗用于最终视觉确认。
