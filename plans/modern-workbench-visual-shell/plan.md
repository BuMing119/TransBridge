# 现代流体化翻译工作台实施计划

- **Feature slug**：`modern-workbench-visual-shell`
- **状态**：已完成（S06 渐进式顶部菜单通过 QA，2026-08-24）
- **日期**：2026-08-24
- **对应需求**：[FR27.1～FR27.9、NFR1.7](../../docs/requirements.md)
- **架构**：[ADR-022](../../docs/adr/022-modern-workbench-visual-composition.md)
- **基础依赖**：[ui-foundation-framework](../ui-foundation-framework/plan.md)、[ui-presentation-modularization](../ui-presentation-modularization/plan.md)、[guided-ui-workflows](../guided-ui-workflows/plan.md)
- **视觉基准**：用户于 2026-08-24 确认的现代浅色 TransBridge 工作台效果图
- **实现证据**：离屏实际窗口与确认稿逐区复核；Theme/Foundation/Guidance 105 项、Workbench/Shell/UX/Integration 47 项、10,000 行热切换专项和最终性能门禁通过；S06 菜单、主题、启动、用户旅程与失败恢复 38 项通过；UI Foundation 最终审计 `blocker_count=0`

## 目标

在不改变业务命令、数据 owner、筛选语义和任务终态的前提下，把主窗口与工作台升级为现代桌面生产力应用：固定左侧导航、紧凑上下文、单一统计/筛选/操作区和高密度翻译表格；译文和 Stage 修改必须局部刷新并保持状态显示一致。

## 非目标

- 不重写 Project/Variant/Collection、TaskRuntime、AI 或 ParaTranz 业务流程。
- 不引入第三方主题框架、主题编辑器、动画系统或任意皮肤加载。
- 不在本 Epic 把 QTableWidget 迁移为 QAbstractTableModel；保留现有增量渲染合同。
- 不逐像素复刻生成图中的文字和图标瑕疵。

## 当前实现事实与约束

- MainWindow 使用 QStackedWidget 包含开始中心和带两个页签的 QTabWidget；MenuBuilder/IntentComposition 是命令权威。
- WorkbenchWidget 已组合 ProjectBar、翻译内容栏、GuidanceBanner 和 Step2 公开 facade。
- Step2 已拆为 Summary、FiltersView、TranslationTable、WorkflowActionsView、ProgressView；右键 Stage 与译文编辑已有单行同步路径。
- FR24 的 ThemeService/QPalette/DomainBrushes 与 10,000 行性能测试必须继续通过；表格不得使用常驻 cellWidget。
- 当前工作区包含尚未提交的 FR24 变更；实施只做局部 patch，不重排或回滚无关文件。

## Story 总览与依赖

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---|---|
| S01 | 现代结构样式与壳层导航（已完成） | P0 | FR24 |
| S02 | 紧凑 Workbench 上下文与引导（已完成） | P0 | S01 |
| S03 | 统计/筛选/操作命令区重排（已完成） | P0 | S02 |
| S04 | 高密度表格、批量选择与克制状态绘制（已完成） | P0 | S03 |
| S05 | 单行编辑/Stage 一致性与性能回归（已完成） | P0 | S04 |
| S06 | PyCharm 式渐进顶部菜单与历史入口恢复（已完成） | P0 | S01 |

## S01：现代结构样式与壳层导航

**验收标准**：左侧导航可切换工作台和 ParaTranz；设置、帮助、关于转发既有 Intent；菜单 action/快捷键仍存在；开始中心和工作台页面生命周期不变。

**文件落点**：新增 `src/transbridge/ui/shell/navigation_rail.py`；修改 `ui/main_window.py`、`ui/foundation/components/__init__.py`；增加 shell/UI 测试。

**实施步骤**：建立窄 NavigationRail signal API；用 WorkspaceShell 包装现有页面容器；集中现代结构样式和 object property；连接 StatusPresenter 的用户状态，不复制账户状态。

**验证**：页面切换、Intent 转发、action 可达、主题切换保持当前页、构造销毁测试。

## S02：紧凑 Workbench 上下文与引导

**验收标准**：工程/版本/翻译内容在顶部紧凑呈现；导入/管理与当前内容同区；Guidance 保持可折叠且不影响状态；表格获得更多垂直空间。

**文件落点**：`ui/workbench/widget.py`、`_project_bar.py`、`ui/guidance/qt.py` 及相关测试。

**实施步骤**：重排现有公开 View，不复制其信号；统一边距/间距/控件高度；为上下文区设置稳定 accessible name 与响应式 size policy。

**验证**：Project/Variant/Collection 切换信号、保存状态、引导强度与内容切换回归。

## S03：统计、筛选和操作命令区重排

**验收标准**：四项统计形成单行摘要；常用搜索/筛选只有一个命令区；高级筛选能力保持；AI/检查/写回/更多只出现一次且与范围信息相邻。

**文件落点**：`workflow_actions_view.py`、`filters_view.py`、`step2.py` 及 UX 测试。

**实施步骤**：增强 Summary 视觉结构；把统一搜索作为主输入，Key/原文/译文和标签条件保留为可展开高级项；将 WorkflowActions 放到表格标题行；保留 presenter 和 compatibility aliases。

**验证**：每种 FilterState、summary 点击、清除、selection scope、intent ID 和禁用原因。

## S04：高密度表格、批量选择与克制状态绘制

**验收标准**：列顺序为选择/序号/标签/Key/原文/译文/类型状态；无空列；正常状态低噪声，异常状态明显；译文静止时无常驻编辑框；常驻 cellWidget 数为零。

**文件落点**：`translation_table.py`、必要的 delegate/组件测试。

**实施步骤**：扩展 item/delegate 列模型；用 check state 表达批量选择；从同一 TranslationEntry 绘制状态；只在编辑事件创建 editor；保持 250 行增量批次、定位、选择和滚动恢复。

**验证**：列合同、item flags/check state、delegate 状态矩阵、10,000 行批次和 identity 回归。

## S05：单行更新、最终 QA 与回退门禁

**验收标准**：非空译文首次编辑从未翻译变为已翻译；显式 Stage 被保留；右键 Stage 与译文编辑在筛选成员不变时不创建新 RenderSession；状态文字、UserRole 和视觉同步。

**文件落点**：`step2.py`、`translation_table.py`、`tests/ui/test_step2_incremental_rendering.py`、`tests/ui/ux/` 和性能测试。

**实施步骤**：补齐单行 update 覆盖全部列与 check/selection 状态；验证只有成员关系变化才 re-filter；复跑 FR24 Foundation 审计、UI 旅程和静态检查。

**验证**：focused pytest、10,000 行性能场景、Ruff check/format；人工离屏截图仅作辅助。

## S06：渐进式顶部菜单与历史入口恢复

**验收标准**：顶部栏默认显示紧凑的 TransBridge 菜单触发项；悬停触发项或键盘聚焦后在同一高度原位显示七个既有一级菜单；指针离开菜单与下拉面板后延迟收起；打开下拉菜单期间保持展开。设置中的 ParaTranz 账户信息等历史入口重新可发现，所有 QAction、快捷键和回调仍由 MenuBuilder 唯一拥有。

**文件落点**：新增 `ui/shell/progressive_menu_bar.py`；修改 `ui/main_window.py` 与 `ui/foundation/visual_style.py`；更新 shell/视觉样式测试。

**实施步骤**：以 QMenuBar 子类封装顶层 QAction 可见性与单次延迟计时；MenuBuilder 完成后注册现有一级菜单，不复制菜单树；用菜单 show/hide、指针位置和焦点状态保护展开态；由 MainWindow composition 安装该菜单栏；在 ThemeService 编译样式中补齐菜单栏的 palette-role 外观。

**验证**：默认/悬停/焦点/离开延迟/下拉打开状态机；七个菜单标签、账户 action identity 与回调；浅深主题样式合同；无周期性 timer、无新增业务调用。

## 依赖顺序

`S01 -> S02 -> S03 -> S04 -> S05`，`S01 -> S06`

## 风险与回退

- 壳层切换可能影响既有 QTabWidget 测试：保留 `mode_tabs` compatibility port 和相同页面索引。
- 新列可能影响依赖列常量的调用方：保留公开常量别名并更新 focused tests。
- 视觉样式导致平台差异：颜色只来自 ThemeSnapshot，由 ThemeService 原子应用 palette 与单一编译样式；Windows 为权威平台。
- 任一 Story 回退不得修改业务数据、配置 schema 或任务记录。

## 明确假设

- V1 实现浅色确认稿的信息结构，同时通过既有主题令牌自然支持深色；不另建只支持浅色的硬编码皮肤。
- 左侧导航默认展开为文字模式；本 Epic 不实现用户可配置宽度或复杂折叠动画。
- 状态列保留“类型 · Stage”文字；正常状态可以使用中性 pill，异常状态使用 DomainBrushes。
