# ADR-022：现代工作台视觉组合与 ThemeService 编译样式边界

- **状态**：已接受（2026-08-24 视觉复核后修订）
- **日期**：2026-08-24
- **对应需求**：[FR27.1～FR27.9、NFR1.7](../requirements.md)
- **关联 ADR**：[ADR-020](020-high-performance-ui-foundation.md)、[ADR-021](021-ui-presentation-modularization.md)

## 背景与约束

FR24 已建立单一 ThemeService、语义令牌、应用 QPalette、公共组件和表格 delegate；FR25/FR26 已冻结 View/Presenter/Intent 与工作流合同。用户确认的新视觉基准要求左侧导航、分层 surface、统一圆角、紧凑命令区和高密度数据表。现有 Fusion 默认外观即使使用更现代的 palette，仍不足以表达这些结构关系。

本决策必须同时满足：不引入第二主题 owner、不把业务状态复制进视觉层、不为表格行创建 QWidget、不用视觉刷新触发 projection/render revision，并继续满足 10,000 行和主题切换性能预算。

## 决策

### 1. ThemeService 同时拥有 Palette 与单一编译样式

ThemeService、ThemeSnapshot 和已验证主题令牌继续是颜色唯一权威。FR27 不引入第三方主题库、任意皮肤加载或第二 ThemeManager。

单纯依赖 Fusion 与 QPalette 无法表达确认稿的浅色选中底、克制边框、surface 层级和统一控件状态；逐控件挂载无颜色 QSS 还会让未指定的边框退回平台黑色。ThemeService 因此 SHALL 从同一个 ThemeSnapshot 编译一份应用级结构 QSS，并由 QPalette 提供其全部颜色角色。二者作为同一主题事务应用、失败时共同回滚，并按 fingerprint 缓存。普通明暗主题切换只替换 palette，不重新解析已有 10,000 行窗口树；仅当排版/尺寸令牌令编译结果实际变化时才替换应用样式。主题切换不得重建工作台布局和业务 projection。

公共组件只设置 `tbComponentKind`、`tbSemanticState` 等稳定属性，不调用局部 `setStyleSheet()`。编译器只消费归一化 RGBA、排版、间距和圆角令牌；不得包含独立于主题 Provider 的产品配色。业务状态仍由 DomainBrushes/delegate 绘制，不复制 entry.stage 或其他状态 owner。

### 2. 新建可复用应用壳层组件

在 `ui/shell/` 增加 NavigationRail/WorkspaceShell 组合：NavigationRail 只发出稳定页面索引或 IntentId，MainWindow composition 负责连接现有 QStackedWidget/QTabWidget 页面和 ShellIntentComposition。导航不得直接调用 repository、网络或业务 use case。

历史菜单 action 继续由 MenuBuilder 构造以保留快捷键和 Action Catalog。主窗口在同一个 QMenuBar 上增加渐进式呈现：默认只显示一个紧凑触发项；触发项悬停或菜单栏获得键盘焦点时，原位显示 MenuBuilder 创建的全部一级菜单；指针离开且没有下拉菜单打开时延迟恢复紧凑状态。呈现层只能切换顶层 QAction 的可见性，不得复制 QAction、菜单内容、快捷键或 command。左侧设置、帮助、关于按钮继续转发既有 intent。

### 3. Workbench 只重排公开 View，不重写状态机

WorkbenchWidget 组合 ProjectBar、翻译内容栏、GuidanceBanner 和 Step2 的公开 View。Step2 继续使用 FiltersPresenter、TablePresenter、WorkflowPresenter、TranslationTable 和现有 application/projection command。

统计、筛选和操作区的视觉合并不得合并它们的状态职责。FilterState、row selection、label scope 和 ContextActionViewState 仍分别由现有 owner 管理。

### 4. 表格采用 item/delegate，不采用单元格控件

TranslationTable 保持 QTableWidget 增量批次与稳定 entry identity。批量选择使用 item check state 或 selection model；标签数量、状态胶囊、悬停/选中编辑提示由 QStyledItemDelegate 绘制。只在实际编辑期间创建 Qt editor。

译文/Stage 更新通过 `update_rendered_entry()` 同步同一行全部 UserRole 和显示字段；只有筛选成员关系变化时才启动新 RenderSession。

### 5. 视觉基准是行为规范，不是像素快照

确认稿约束信息层级、控件关系、密度和反馈。Windows 字体、DPI、平台 style 的像素差异允许存在；生成图中的错误中文、无来源图标和空白占位不得实现。视觉验收使用结构断言、代表性离屏渲染和人工截图复核共同完成，截图不替代行为测试。

## 关键契约

- `NavigationRail.page_requested(index)` 只切换现有页面；`intent_requested(intent_id)` 只进入 ShellIntentComposition。
- 渐进式菜单只控制 MenuBuilder 现有顶层 QAction 的可见性；任一时刻每个 Intent 仍只有一个权威 QAction 和回调。
- 下拉菜单可见、菜单栏具有键盘焦点或指针仍位于顶部栏时不得自动收起；延迟计时不得形成持续轮询。
- Theme revision 不改变 current page、FilterState、selection、RenderSession generation 或 entry 数据。
- 单行编辑/Stage 更新未改变筛选成员关系时，不调用全表 render。
- 表格常驻 cell widget 数为零；编辑器生命周期由 Qt delegate 管理。
- 壳层最小内容宽度不足时导航保持固定紧凑宽度，主表获得剩余空间并允许列滚动/缩放。

## 备选方案

### A. 引入 QFluentWidgets 或完整第三方主题库

不采用。它会扩大分发依赖、重新定义主题 owner，并与现有 Theme Provider/语义令牌重复。

### B. 在各 View 内使用局部颜色 QSS 复刻效果图

不采用。它会形成多个颜色 owner、阻断主题继承并扩大迁移审计面。应用级编译 QSS 是 ThemeService 的一个输出，不是新的主题来源。

### C. 全面实现 QProxyStyle

暂不采用。确认稿所需的可控范围可由集中编译样式与 delegate 达成；全面 style 会显著扩大菜单、滚动条、原生对话框和无障碍回归面。

## 影响与风险

- 渐进式菜单依赖悬停发现完整入口；紧凑触发项必须持续可见并提供工具提示、可访问名称和键盘聚焦展开，回归测试需证明账户等历史 action 仍可达。
- QTableWidget 在 10,000 行下仍有 item 成本；本需求保持既有 250 行增量批次，不在视觉改造中迁移模型架构。
- Qt palette role 在不同平台 style 下可能存在细节差异；Windows 10/11 是权威视觉平台，其他平台以功能和可读性为先。

## 迁移与回退

1. 先增加 NavigationRail 和结构组件，不删除既有页面或 Intent。
2. 重排 Workbench/Step2，保留所有 public facade 与 compatibility aliases。
3. 增强表格 delegate 和局部更新测试，再启用现代密度。
4. 任一阶段可恢复 QTabWidget 常驻页签和旧布局；回退只影响展示，不迁移数据或配置。
5. 渐进式菜单可通过移除可见性控制恢复为常驻 QMenuBar；MenuBuilder、Action Catalog 和业务回调无需迁移。
