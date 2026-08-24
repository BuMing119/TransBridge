# 高性能统一 UI 基础框架实施计划

- **Feature slug**：`ui-foundation-framework`
- **状态**：已完成（2026-08-24）
- **日期**：2026-08-19；FR26 对齐及 FR24 实现：2026-08-24
- **对应需求**：[FR24.1～FR24.11、NFR1.4](../../docs/requirements.md)
- **架构**：[ADR-020](../../docs/adr/020-high-performance-ui-foundation.md)
- **前置 Epic**：[ui-presentation-modularization](../ui-presentation-modularization/plan.md)（FR25 / ADR-021）
- **交互前置**：[guided-ui-workflows](../guided-ui-workflows/plan.md)（FR26 / NFR1.6，S01～S13 已完成并冻结）
- **交接证据**：[FR26 → FR24 migration inventory](migration-inventory.md)、[FR26 S10 QA](../../docs/test-reports/guided-ui-workflows-s10-qa-2026-08-24.md)、[P0 journey evidence](../guided-ui-workflows/p0-journey-evidence.md)
- **承接延期项**：M69 i18n、M70 无障碍、M71 主题/颜色系统
- **目标平台**：Windows 10/11、Python 3.12、PyQt6 6.5+

## 目标

建立一个性能优先、可渐进迁移的 PyQt6 UI Foundation：用不可变语义令牌和应用级 `QPalette` 统一浅色、深色、跟随系统三种主题；为公共组件、自定义绘制、图标、i18n 与无障碍建立稳定合同；首期只注册内置主题，但保留经过版本和预算校验的 Theme Provider 接口。

交付完成后应满足：

1. GUI 只有一个主题状态 owner，启动、切换、持久化与系统主题变化走同一数据流。
2. 标准控件通过 Palette 继承，自定义绘制通过同一 snapshot；正常交互没有主题轮询、窗口树扫描和重复样式解析。
3. 核心窗口不再包含主题颜色常量；全量历史迁移有可执行清单和阻止新增违规的门禁。
4. 内置主题和未来 Provider 使用相同 schema/validator；无效主题原子拒绝并安全回退。
5. UI 初始化、切换、窗口打开和长时缓存满足 NFR1.4 的 P95/RSS 预算。

## 非目标

- 完整主题编辑器、用户皮肤导入、主题市场或在线下载。
- 任意 Python widget、全局 QSS 或脚本作为主题扩展执行。
- 可切换布局密度、复杂动效、全面自定义窗口装饰。
- 首期一次性翻译所有历史中文文案，或实现无需重启的全量 locale 热切换。
- 重写现有业务窗口、解析/翻译流程或 application/domain 契约。
- 引入第三方主题框架或抬高 `PyQt6>=6.5` 基线。

## 当前实现事实（FR24 完成后）

- `src/transbridge/ui/app.py` 在业务窗口前创建并启动唯一 `GuiFoundation`，向 `MainWindow` 显式注入 ThemeService、Registry、LocaleService 和统一 UI 配置；关闭顺序保持 UI Foundation 先于 AppRuntime。
- FR25 已把 shell、Workbench、AI Translator、Smart Assistant 及操作相关 UI 拆到公开 View/Presenter/Binding/coordinator 边界；FR24 只在 View/composition root 注入主题，不向 Presenter、application use case 或历史上帝文件增加职责。
- FR26 已完成开始中心、Action Catalog/Intent Router、Guidance、任务中心、命令搜索、AI config/scope/run/result、非模态 operation plan、安全拖放和关键可访问性合同。主题迁移必须保持这些 intent、焦点、取消、返回上下文和能力可见性。
- `build_runtime()` 已注册 `UiPreferenceRepository`，当前 `[ui]` section 持久化 guidance mode。FR24 应扩展这一 typed adapter 保存 theme/locale，而不是建立第二个 `QSettings` 或 ConfigRepository owner；`QSettings` 仅保留窗口 geometry/state 兼容用途。
- 当前生产可达的 Shell、Workbench/Step2、AI Translator、Smart Assistant、ParaTranz、Operation Plan 与 FOMOD 已消费 Palette/ThemeView/DomainBrushes；不可达 Step1/Step3 等兼容模块保留在审计清单，不重新接回生产组合。
- `scripts/audit_ui_foundation.py --final --include-pending-migrations` 返回 `blocker_count=0`；保留项均有结构化 owner、reason 与 removal gate，或属于不可达兼容模块。
- `tests/performance/` 已复用 versioned threshold registry，覆盖代表窗口树、20 样本 P95、10,000 行合同、100 次主题往返、RSS/缓存/idle/noop；FR26 J01～J09 已在 light/dark/system/running-switch 矩阵复验。
- PyQt6 最低版本 6.5 可读取并监听系统 color scheme；显式 scheme setter 不是最低基线的一部分。

## 实施原则

> **顺序约束（已满足）**：FR25 已于 2026-08-19 完成，FR26 已于 2026-08-24 完成 S01～S13 与 FR24 交接。FR24 S01～S09 可按本计划依赖顺序实施；页面迁移必须以交接清单中的当前生产 composition 为准，不得重新引入 FR26 已删除的“小工具”分类、旧 Step1/Step3 主页面、多层模态链或第二 intent/任务路径。

## FR25/FR26 稳定接入面

- 应用级服务从 `ui/app.py` / `MainWindow` composition 注入；coordinator 使用公开 Main/Workbench ports，不扫描窗口树。
- shell、Workbench、AI Translator、Smart Assistant 的具体 View 已与 Presenter/Binding 分开；Theme adapter 只进入 View/composition，Presenter 不依赖主题。
- `SubscriptionGroup`、Chat/Task binding、render/run generation 的 close 合同可复用，但 theme revision 必须与 projection/render/run generation 独立。
- `WorkbenchWidget.project_bar/preview/filtered_entries()/locate_entry()` 是稳定公开端口；不得恢复对 `_step2/_table` 的访问。
- FR25 的 `benchmark_ui_modularization.py` 与比较器作为 FR24 S01 的共享对照，不替代 FR24 自己的主题切换与缓存场景。
- Start Center、Guidance、Task Center、Command Palette/Context Help、Safe Drop 与 Operation Plan 已使用 canonical intent/公开状态；主题适配只消费它们的 ViewState，不改变 availability、draft、confirm token 或 command dispatch。
- FR26 的 J01～J09 固定旅程、D/M/N 结果和焦点/取消/返回合同成为 FR24 的行为对照；视觉迁移不允许以新增对话框或设置步骤换取实现便利。

1. **先测后迁移**：Story-01 冻结无 Foundation 基线，之后才允许大面积样式迁移。
2. **颜色单一所有权**：Qt 标准颜色归 `QPalette`，业务颜色归语义令牌；QSS 不拥有主题颜色。
3. **原子快照**：definition 完整校验和编译后才能替换当前 snapshot；失败不产生半主题。
4. **事件驱动**：仅用户选择或系统信号改变 effective scheme；相同 fingerprint 幂等。
5. **显式注入**：GUI Foundation 从 `ui/app.py` 注入，不使用模块级可写 singleton，不污染 `AppRuntime`。
6. **有界资源**：图标与派生资源按 revision/size/DPR 有成本上限；不预生成全部组合。
7. **渐进兼容**：按组件族迁移，旧浅色可作为临时 compatibility provider；删除兼容层要经过清单和门禁。

## Story 总览与依赖

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---:|---|
| S01 | UI 性能基线与样式迁移清单 | P0 | 无 |
| S02 | Qt-free 令牌、Provider、Registry、Validator 与内置主题 | P0 | S01 |
| S03 | ThemeService、Palette 应用、统一配置与系统模式 | P0 | S02 |
| S04 | 公共组件、主题适配器与有界图标/富文本缓存 | P0 | S03 |
| S05 | 主题设置、预览、持久化失败与恢复默认 UX | P1 | S03、S04 |
| S06 | Shell、开始中心与 Workbench 关键路径迁移 | P0 | S04、S05；FR26 交接已满足 |
| S07 | Smart Assistant、AI Translator、操作计划、ParaTranz/FOMOD 高风险视觉迁移 | P0 | S04、S06；FR26 交接已满足 |
| S08 | i18n 与无障碍基础合同及关键路径接入 | P1 | S04、S06；复用 FR26 可访问性合同 |
| S09 | 扩展合同验证、全量审计与最终性能/回退门禁 | P0 | S05～S08 |

## Story-01：UI 性能基线与样式迁移清单

**详细文档**：[story-01-performance-baseline-inventory.md](stories/story-01-performance-baseline-inventory.md)

**目标**：在视觉实现变化之前建立可复现基线和完整迁移边界，避免主题方案只能凭感觉判断性能。

**验收标准**：

- [x] 代表性窗口树包含 MainWindow、Workbench、AI Translator、Smart Assistant、ParaTranz 和一个大表格/对话框组合，并固定 fixture fingerprint、字体、DPI、平台、Qt/PyQt 版本。
- [x] 窗口树同时覆盖 FR26 的 Start Center 与已恢复工程 Workbench 两种启动目的地，以及 Guidance、Task Center、Command Palette、Operation Plan 和 FOMOD；不得用已退出生产组合的 Step1/Step3 代替当前页面。
- [x] 冷进程记录启动至首个可交互窗口、RSS、UI Foundation 占位初始化耗时；热进程记录窗口打开 P50/P95、heartbeat 和 100 次空切换控制组。
- [x] 阈值扩展到现有 `THRESHOLDS_V1` 或明确的新 version；同一数字只有一个真源。
- [x] 生成机器可检查的硬编码颜色、`setStyleSheet`、自定义绘制、QSettings 与可访问属性清单，按子系统/风险分组。
- [x] 基线测试在无显示服务器时明确 skip/降级，不得用同步 callback 假装真实 Qt 窗口性能通过。

**文件落点**：

- 修改 `tests/performance/benchmark_cases.py`
- 新增 `tests/performance/test_ui_foundation_performance.py`
- 新增 `tests/ui/fixtures/ui_foundation_window_tree.py`
- 新增 `scripts/audit_ui_foundation.py`
- 新增 `plans/ui-foundation-framework/migration-inventory.md`

**验证**：运行审计脚本两次输出稳定；执行 UI/performance 基线测试；在 Windows 固定硬件档记录一份可复验 JSON 结果。

## Story-02：语义令牌、Provider、Registry 与内置主题

**详细文档**：[story-02-theme-contracts-registry.md](stories/story-02-theme-contracts-registry.md)

**目标**：建立不依赖 Qt 的稳定主题数据合同，并交付浅/深/兼容浅色定义。

**验收标准**：

- [x] `ThemeManifest`、`ThemeTokens`、`ThemeDefinition`、`ThemeProvider`、错误和注册结果为冻结值对象/Protocol，不 import PyQt。
- [x] 令牌覆盖基础、语义和业务三层；业务层至少覆盖 Stage、标签、差异、译文、任务、报告状态。
- [x] validator 一次性检查 schema/version、ID、token 完整性、颜色/数值、引用闭合、关键对比度、资源预算和冲突。
- [x] 无效 provider/theme 整体拒绝，Registry 不留下部分状态；同一 provider 重复注册结果幂等。
- [x] 内置 light/dark 在相同结构令牌下具有完整语义 token；兼容浅色只用于渐进回退并明确移除门禁。
- [x] 不允许 Provider 提供 Python widget、回调、网络资源或原始全局 QSS。

**文件落点**：

- 新增 `src/transbridge/ui/foundation/__init__.py`
- 新增 `src/transbridge/ui/foundation/model.py`
- 新增 `src/transbridge/ui/foundation/registry.py`
- 新增 `src/transbridge/ui/foundation/builtins.py`
- 新增 `tests/ui/foundation/test_theme_registry.py`
- 新增 `tests/ui/foundation/test_builtin_themes.py`

**验证**：Qt-free import 测试、schema/version 向前拒绝测试、token mutation 防护、对比度/预算边界、冲突和重复注册测试。

## Story-03：ThemeService、Palette 应用与系统模式

**详细文档**：[story-03-theme-service-system-mode.md](stories/story-03-theme-service-system-mode.md)

**目标**：建立 GUI 进程唯一主题 owner，并在应用构造阶段完成高性能 Palette-first 接线。

**验收标准**：

- [x] `ThemeService.start/set_preference/snapshot/close` 和 `theme_changed` 遵守 ADR-020；只有 effective fingerprint 改变才递增 revision 和发信号。
- [x] 使用 Fusion + `QPalette` 应用标准控件颜色，不调用 `allWidgets()`、不对所有 widget 手动 polish、不使用颜色型全局 QSS。
- [x] `system` 通过 Qt 6.5 `QStyleHints.colorScheme/colorSchemeChanged` 事件驱动；Unknown 稳定回退浅色；显式 light/dark 不依赖 Qt 6.8 setter。
- [x] `ui/app.py` 在创建业务 widget 前构造并启动 `GuiFoundation`，显式传入 `ConfigRepository`；关闭时先断开 UI 信号再关闭 AppRuntime。
- [x] `[ui] theme_mode/theme_id/locale` 通过统一 repository 原子更新；无效值和写失败保留最后有效状态并返回稳定错误码。
- [x] ThemeService 只能在 GUI 主线程应用 Qt 快照；跨线程请求安全排队或明确拒绝。

**文件落点**：

- 新增 `src/transbridge/ui/foundation/qt_palette.py`
- 新增 `src/transbridge/ui/foundation/theme_service.py`
- 新增 `src/transbridge/ui/foundation/runtime.py`
- 修改 `src/transbridge/ui/app.py`
- 修改 `src/transbridge/ui/main_window.py` 的构造注入
- 修改或扩展 `src/transbridge/config/repository.py` 的 UI section 读取合同（不新增第二配置 owner）
- 新增 `tests/ui/foundation/test_theme_service.py`
- 新增 `tests/integration/gui/test_ui_foundation_startup.py`

**验证**：真实 `QApplication` 下 light/dark/system/unknown、重复切换、系统信号、配置写失败、apply 异常回滚、close 后信号不回调和 Qt 6.5 API 兼容测试。

## Story-04：公共组件、主题适配器与有界资源缓存

**详细文档**：[story-04-components-adapters-cache.md](stories/story-04-components-adapters-cache.md)

**目标**：让标准控件和不能依靠 Palette 的自定义绘制都消费同一 snapshot，同时限制资源成本。

**验收标准**：

- [x] 公共组件约定覆盖按钮、输入、卡片、对话框、表格、标签、工具提示、空状态、进度、通知和焦点状态。
- [x] 标准组件优先使用 palette/property/font；静态结构 QSS 集中且不含主题颜色。
- [x] 提供 custom paint、item/delegate、Markdown/rich-text、消息气泡和业务状态色适配器；订阅句柄可释放，不因 widget 重建累积 listener。
- [x] 图标/派生 pixmap 按 revision/icon/size/DPR/state 缓存，默认成本上限 8 MiB，只在 GUI 主线程创建。
- [x] Markdown/rich-text 主题 CSS 每 revision 编译一次；内容变化不重复编译主题模板。
- [x] 组件销毁后切换主题不访问已删除 QObject，100 次构造/销毁 listener 数回到基线。

**文件落点**：

- 新增 `src/transbridge/ui/foundation/components/`
- 新增 `src/transbridge/ui/foundation/adapters.py`
- 新增 `src/transbridge/ui/foundation/icons.py`
- 修改 `src/transbridge/infra/markdown_renderer.py`，接受 UI 层注入的渲染主题合同而不反向依赖 ThemeService
- 新增 `tests/ui/foundation/test_components.py`
- 新增 `tests/ui/foundation/test_theme_adapters.py`
- 新增 `tests/ui/foundation/test_icon_cache.py`

**验证**：组件状态矩阵、palette 传播、自定义绘制 revision、缓存命中/淘汰/DPR、线程约束、QObject 生命周期和无颜色 QSS 审计。

## Story-05：主题设置、预览与恢复 UX

**详细文档**：[story-05-theme-settings-preview.md](stories/story-05-theme-settings-preview.md)

**目标**：让用户可安全选择主题，并明确“已应用”和“已持久化”的区别。

**验收标准**：

- [x] 通用设置入口提供 system/light/dark、当前 effective scheme、即时预览、应用、取消和恢复默认。
- [x] 预览只使用隔离 preview widget/snapshot，不修改业务窗口或持久化配置；取消后无残留 revision/cache/listener。
- [x] 应用成功后当前和新窗口一致；重复应用当前值幂等。
- [x] 写入失败时用户可选择保持本次会话主题或恢复持久化主题，提示不泄漏底层路径。
- [x] 未知 theme ID、Provider 移除和系统 scheme unknown 有稳定回退说明。
- [x] 首期界面不出现导入、编辑、市场或任意皮肤入口，但展示 Provider 元数据的控件边界可复用。

**文件落点**：

- 新增 `src/transbridge/ui/settings_dialog.py`
- 新增 `src/transbridge/ui/foundation/preview.py`
- 修改 `src/transbridge/ui/main_window.py` 的设置菜单
- 保留 `src/transbridge/ui/paratranz/config_dialog.py` 为 API 专用对话框或作为设置子页适配，不复制主题状态
- 新增 `tests/ui/test_ui_settings_dialog.py`

**验证**：apply/cancel/default、写失败、未知主题、重复操作、关闭预览资源释放和菜单入口测试。

## Story-06：Shell、开始中心与 Workbench 关键路径迁移

**详细文档**：[story-06-main-workbench-migration.md](stories/story-06-main-workbench-migration.md)

**目标**：先迁移用户最常驻、窗口树最大的 UI，验证 Palette-first 在真实表格和状态色上的效果与性能。

**验收标准**：

- [x] MainWindow 壳、菜单/状态栏、Start Center、Guidance、Task Center、Command Palette/Context Help 与当前 Workbench/Step2/project bar/workflow slices 使用语义/业务令牌。
- [x] Step1/Step3 及旧 operation cards 先经过生产可达性审计；不可达的兼容模块只登记 owner/删除门禁，不为了“全量迁移”重新接回当前界面。
- [x] Stage、标签、隐藏/锁定、已翻译/未翻译、focus/filter 等状态在浅/深主题均清晰，关键状态具有文字/图标/边框等非纯颜色信息。
- [x] Step2 大表格主题切换保持 row identity、选择、滚动位置、编辑内容和增量 render generation；不得全量重建业务数据。
- [x] 迁移文件不再出现裸主题颜色；仍保留的结构 QSS 有审计豁免原因。
- [x] 主窗口 geometry 的历史 `QSettings` 与 UI preference 权威状态分离，主题不得从 QSettings 读取。
- [x] 窗口打开 P95 和主题切换 heartbeat 满足 NFR1.4。

**文件落点**：

- 修改 `src/transbridge/ui/main_window.py`
- 修改 `src/transbridge/ui/shell/*.py`、`src/transbridge/ui/guidance/*.py`
- 修改 `src/transbridge/ui/workbench/*.py`
- 修改 `src/transbridge/ui/workbench/cards/*.py`
- 新增 `tests/ui/test_workbench_theme_migration.py`
- 新增 `tests/ui/test_shell_theme_migration.py`
- 更新 `plans/ui-foundation-framework/migration-inventory.md`

**验证**：浅/深/系统真实 widget 测试、Step2 大表增量渲染回归、状态非纯颜色断言、审计清单和性能门禁。

## Story-07：Smart Assistant、AI Translator、操作计划与 ParaTranz/FOMOD 视觉迁移

**详细文档**：[story-07-tools-paratranz-migration.md](stories/story-07-tools-paratranz-migration.md)

**目标**：迁移自定义 QSS、富文本、Delegate 和业务报告最集中的 Smart Assistant、AI Translator、Operation Plan、ParaTranz 与 FOMOD 表面。

**验收标准**：

- [x] Smart Assistant 的 message bubble、thinking、tool/plan card、session list、task monitor、quick actions 与 Markdown 使用 Foundation snapshot。
- [x] AI Translator 的配置、批次、进度、预览和报告窗口使用语义/业务令牌，成功/失败/警告/差异在两种主题中可读。
- [x] ParaTranz tabs、dialogs、Stage 颜色和 `_NavItemDelegate` 使用同一 domain tokens；Delegate 不在每次 paint 解析颜色字符串。
- [x] 非模态 Operation Plan、结果/预检状态与 FOMOD panel 使用同一语义/业务令牌；主题变化不得重新生成 draft、confirm token、preflight 或 Run ID。
- [x] 已打开对话框与后续新建对话框在一次 revision 后一致；销毁窗口不泄漏 subscription。
- [x] 主题切换不影响正在运行的 Task、输入内容、选择、报告数据或网络请求。
- [x] 上述表面迁移后的裸颜色/QSS 清单归零或只有带理由的结构豁免。

**文件落点**：

- 修改 `src/transbridge/ui/tools/smart_assistant/*.py`
- 修改 `src/transbridge/ui/tools/ai_translator/*.py`
- 修改 `src/transbridge/ui/paratranz/*.py`
- 修改 `src/transbridge/ui/operations/*.py`
- 修改 `src/transbridge/ui/tools/fomod/*.py`
- 修改/适配 `src/transbridge/infra/markdown_renderer.py`
- 新增 `tests/ui/test_tool_theme_migration.py`
- 新增 `tests/ui/test_paratranz_theme_migration.py`
- 更新迁移清单

**验证**：运行中切换、Markdown/rich-text、Delegate paint、报告差异、listener 释放、功能状态保真和性能回归测试。

## Story-08：i18n 与无障碍基础合同

**详细文档**：[story-08-i18n-accessibility-foundation.md](stories/story-08-i18n-accessibility-foundation.md)

**目标**：解决 M69/M70 的框架缺口，但避免把全量历史文案迁移塞入主题热路径。

**验收标准**：

- [x] `LocaleService` 使用统一 gettext catalog、source locale、fallback 和配置持久化；首期 locale 切换明确重启生效。
- [x] 缺失 catalog/msgid 回退源语言并聚合诊断，不在 paint/刷新热路径重复日志。
- [x] 公共组件不固化中文，关键路径（应用菜单、设置、主题错误/回退）完成 msgid 接入。
- [x] 公共组件设置 accessible name/description、合理 focus policy、可见焦点和键盘顺序；仅颜色状态有等价文本/图标。
- [x] 保留并扩展 FR26 已通过的 `tests/ui/test_accessibility_contracts.py`，主题/locale 接入不得改变 Enter/Esc、默认焦点、危险操作和命令搜索快捷键合同。
- [x] 关键文字/背景和 focus/selection 组合通过对比度检查，字体与 DPI 缩放不截断关键设置控件。
- [x] 最低 PyQt6 6.5 路线不依赖 Qt 6.10 accessibility hints；未来 hints 有显式适配接口。

**文件落点**：

- 新增 `src/transbridge/ui/foundation/locale_service.py`
- 新增 `src/transbridge/ui/foundation/accessibility.py`
- 新增 `src/transbridge/ui/i18n/` 及首期 catalog/template
- 修改公共组件、`ui/app.py`、`ui/main_window.py`、`ui/settings_dialog.py`
- 新增 `tests/ui/foundation/test_locale_service.py`
- 新增 `tests/ui/foundation/test_accessibility_contract.py`

**验证**：locale 存在/缺失/损坏、重启生效、fallback 聚合、键盘遍历、accessible properties、对比度和 100%/150%/200% DPI 布局测试。

## Story-09：扩展合同、全量审计与最终性能门禁

**详细文档**：[story-09-extension-final-gates.md](stories/story-09-extension-final-gates.md)

**目标**：证明 Provider 接口真实可扩展、迁移没有留下双主题状态，并完成发布前性能与回退证据。

**验收标准**：

- [x] 使用一个仅存在于测试的第三方 Provider 证明无需改业务组件即可注册、resolve、apply 和回退；不实现动态发现或用户安装。
- [x] forward schema、缺 token、超资源预算、ID 冲突、非法路径、异常 provider 均原子拒绝，当前主题不变。
- [x] 审计阻止新裸颜色、颜色型局部 QSS、UI theme QSettings、直接 Provider 执行和无界 theme cache；豁免有 owner 与移除条件。
- [x] 冷初始化新增 P95 ≤75 ms、RSS ≤12 MiB；热切换 P95 ≤250 ms、heartbeat ≤200 ms；窗口打开回归 ≤5% 或 10 ms；100 次切换预热后 RSS 增长 ≤2 MiB。
- [x] idle 期间 Theme/Locale 无 timer、无窗口树扫描；重复选择当前主题零 apply/零 signal。
- [x] compatibility provider/旧 QSS 删除门禁满足，或把残留项以明确 blocker 和后续 Story 保留，不能伪称全量完成。
- [x] 回退到内置浅色后 GUI 核心操作可用，业务数据和统一配置不损坏。
- [x] FR26 J01～J09 固定旅程保持相同 canonical intent、D/M/N、焦点、取消点与返回上下文；主题切换期间 application command、网络/文件副作用和 Task Run ID 计数均不增加。

**文件落点**：

- 修改 `scripts/audit_ui_foundation.py`
- 修改 `tests/performance/benchmark_cases.py`
- 完成 `tests/performance/test_ui_foundation_performance.py`
- 新增 `tests/contracts/ui/test_theme_provider_contract.py`
- 新增 `tests/integration/gui/test_theme_failure_recovery.py`
- 扩展 `tests/ui/ux/test_current_user_journeys.py` 的 FR24 theme matrix，并复用 `plans/guided-ui-workflows/p0-journey-evidence.md` 的固定 fixture
- 更新 `plans/ui-foundation-framework/migration-inventory.md`

**验证**：全量 UI Foundation tests、现有 UI/integration 回归、审计脚本、Windows 固定硬件性能报告和浅色回退冒烟。

## 依赖顺序与交付门禁

```text
S01 baseline
  -> S02 contracts
    -> S03 runtime
      -> S04 components/adapters
        -> S05 settings
        -> S06 main/workbench
             -> S07 tools/paratranz
             -> S08 i18n/a11y
S05 + S06 + S07 + S08 -> S09 final gates
```

- S01 未完成不得宣称性能预算可验收。
- S03 未完成不得在业务组件内临时创建第二个 ThemeManager。
- S04 未完成不得直接批量替换颜色常量，否则会产生新的散落 helper。
- S04/S05 可以先交付组件状态和设置合同，但不得把旧菜单分类、旧 Step1/Step3/操作卡层级或旧模态链写成永久公共组件 API。
- S06 通过后才能迁移自定义绘制密集的 S07，以先验证主窗口数据/性能保真。
- FR26 的 S07/S08/S09/S13 交接门禁已经满足；实现时只需验证消费的是当前公开 View/composition 与固定旅程，不再等待新的 FR26 状态。
- S09 通过前不得删除 compatibility provider 或把 Plan 标记完成。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Fusion 与当前平台样式视觉差异 | 用户感知变化、局部布局偏差 | S01 固定窗口树；S06 前做兼容浅色；关键窗口渲染检查 |
| Palette 无法覆盖圆角/复杂状态 | 组件外观不一致 | 公共组件集中使用主题无关结构 QSS；颜色仍由 token/palette 所有 |
| 大表切换触发重绘抖动 | UI 卡顿 | 不重建 model/items；只更新 palette/domain brushes；heartbeat/P95 门禁 |
| 订阅未释放 | QObject 崩溃或内存增长 | 返回可释放句柄、QObject destroy 自动断开、构造/销毁稳定性测试 |
| Provider 接口变成任意代码入口 | 安全与性能不可控 | 首期只内置注册；声明式 schema；禁止动态 import/QSS/widget 回调 |
| i18n 扩大范围 | 延误主题主线 | 首期只建合同和关键路径；完整历史文案独立后续迁移 |
| 迁移已退出生产组合的旧 UI | 浪费工作并可能把 Step1/Step3、旧菜单或旧模态链重新带回 | S01 先做 production reachability；以 FR26 migration inventory 和固定旅程为唯一页面边界 |
| 主题 revision 污染 FR26 状态 | 重复 command、Run ID、预检或副作用 | composition-root 订阅；J01～J09 theme matrix 断言 intent/数据/Task 计数不变 |
| 当前工作区已有无关改动 | 索引或配置冲突 | 每 Story 只做局部 patch；提交前按 feature diff 审查，不覆盖其他工作线 |

## 回退策略

- 保留 `transbridge.compat-light` 作为迁移期 provider；故障或不兼容配置回退到它/内置浅色。
- ThemeService 替换 snapshot 前保留 last-good palette；apply 失败恢复，不写新偏好。
- 每个子系统迁移按文件族独立提交，功能回归时可恢复该适配层而不撤销主题模型和 Registry。
- UI 配置使用通用 `[ui]` section；未知未来 theme ID 保留诊断但运行时回退，避免删除用户偏好。
- 回退不触碰业务聚合、项目文件、翻译数据、任务或凭据。

## 明确假设与未决项

- 源语言暂定 `zh-CN`，gettext locale 变更首期重启生效；若要求全量即时语言切换，需要新增独立 Story。
- “跟随系统”表示跟随 light/dark scheme，不承诺复制操作系统全部品牌色或窗口装饰。
- 结构令牌在内置浅/深主题间一致；未来改变密度/结构的主题包需要组件级重布局能力和新的性能门禁。
- NFR1.4 数值已由 S01/S09 的固定窗口树、20 样本 P95 和 100 次生命周期证据验证，本次未调整阈值。
- ADR、Plan 与 S01～S09 已按最终 QA 证据标记完成；后续扩展主题、全量历史文案迁移和不可达兼容 UI 清理由独立需求承接。
