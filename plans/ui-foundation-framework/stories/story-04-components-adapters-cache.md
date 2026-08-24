# Story-04：公共组件、主题适配器与有界资源缓存

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：已完成（2026-08-24）
- **优先级**：P0
- **前置依赖**：S03 ThemeService 与 Qt snapshot
- **下游依赖**：S05～S09

## 目标

让标准控件和不能依靠 Palette 的自定义绘制都消费同一 snapshot，同时限制资源成本和订阅生命周期。

## 原始验收标准

- [x] 公共组件约定覆盖按钮、输入、卡片、对话框、表格、标签、工具提示、空状态、进度、通知和焦点状态。
- [x] 标准组件优先使用 palette/property/font；静态结构 QSS 集中且不含主题颜色。
- [x] 提供 custom paint、item/delegate、Markdown/rich-text、消息气泡和业务状态色适配器；订阅句柄可释放，不因 widget 重建累积 listener。
- [x] 图标/派生 pixmap 按 revision/icon/size/DPR/state 缓存，默认成本上限 8 MiB，只在 GUI 主线程创建。
- [x] Markdown/rich-text 主题 CSS 每 revision 编译一次；内容变化不重复编译主题模板。
- [x] 组件销毁后切换主题不访问已删除 QObject，100 次构造/销毁 listener 数回到基线。

## 组件与适配边界

计划新增窄接口，而不是要求所有 widget 继承同一巨型基类：

```text
ThemeView
  snapshot() -> ThemeSnapshot
  subscribe(owner: QObject, callback) -> ThemeSubscription

ThemeSubscription.close() -> None

ComponentStyle
  apply_static(widget, component_kind, density=default)
  apply_state(widget, semantic_state)

DomainBrushes(snapshot)
  stage(stage_id) / task(state) / report(severity) / diff(kind)

RichTextThemeAdapter.stylesheet(snapshot) -> str
IconProvider.icon(icon_id, size, dpr, state, snapshot) -> QIcon
```

`ThemeView` 只暴露当前快照与可释放订阅，不允许组件写 preference。普通 QPushButton/QLineEdit/QDialog 等不订阅；它们通过 application palette 自动更新。只有缓存颜色、生成文本 CSS 或自定义 paint 的对象订阅。

公共组件采用组合/helper：例如 `make_primary_button()`、`configure_dialog()`、`ThemedCard`、`StatusBadge`。不建立会改变 Qt event/ownership 语义的万能 Widget 基类。

## 资源缓存设计

- cache key：`(snapshot.fingerprint, icon_id, logical_size, rounded_dpr, state)`，不得仅用 revision 跨进程持久化。
- cost 使用实际 pixmap `width * height * depth / 8` 估算；默认总上限 8 MiB，单资源和 manifest 总预算先由 S02 validator 限制。
- cache 只在 GUI thread 访问。Theme change 不同步遍历删除每项；切换 namespace 后旧项由 LRU 成本淘汰，连续切换测试确保不增长。
- SVG/矢量源只解析一次或缓存中间 representation；不预渲染所有尺寸/DPR/state。
- rich-text theme CSS 以 fingerprint 为 key，缓存字符串而非每个 QTextDocument；内容 parse/render 缓存与主题缓存分开。

## Markdown 与 infra 依赖方向

当前 `transbridge.infra.markdown_renderer.MarkdownRenderer.render(text)` 直接创建 PyQt widgets。迁移不得让 infra import `ThemeService` 或读取 GUI 配置。可选的最小合同是构造或 render 参数接受一个不可变 `MarkdownTheme`/CSS 字符串：

```text
MarkdownRenderer(theme: MarkdownRenderTheme | None = None)
render(text, *, theme=None) -> QWidget
```

UI adapter 从 snapshot 编译 `MarkdownRenderTheme` 并注入。fallback label 同样使用 palette/传入 theme，不保留硬编码深色代码块。现有无参数调用继续使用 Qt 默认 palette，保证兼容。

## 实施步骤

1. 定义 ThemeView/Subscription，并通过 QObject destroyed signal 自动 close；callback 使用弱 owner 或 Qt connection，不能强引用已关闭窗口。
2. 建立公共组件目录和状态 property 约定。先覆盖状态矩阵与 focus/disabled/hover/checked，不迁移业务窗口。
3. 把允许的结构 QSS 集中成静态常量，增加 AST/text gate 禁止其中出现颜色、gradient 或图片 URL。
4. 实现 DomainBrushes，把 canonical token 一次转换为 QBrush/QPen；组件 paint 只做 O(1) lookup。
5. 实现 IconProvider 的 cost-aware cache、thread guard、DPR key 和 namespace 淘汰。
6. 实现 rich-text/Markdown adapter；修改 renderer 为依赖注入，保持现有调用兼容和输入/块数安全限制。
7. 用 probe components 建立浅/深/state/DPI 测试矩阵和 100 次构造/销毁/切换稳定性测试。

## 边界与错误处理

- 请求未知 icon/domain state：返回统一 missing icon/neutral brush并聚合诊断，不在 paint 循环反复记录。
- callback 抛异常：ThemeService 记录 subscriber error 后继续通知其他订阅者；不得回滚已应用全局 palette。
- 非 GUI thread 请求 pixmap：返回 `theme_wrong_thread` 或排队到 GUI thread，合同与 S03 保持一致。
- cache 项超单项预算：拒绝缓存但可返回一次性渲染结果；若总资源定义超预算应已在 S02 拒绝。
- Markdown theme 编译失败：使用 last-good/Qt default CSS，不影响消息内容显示。

## 测试策略

- 组件：全部状态、focus/keyboard、existing/new child palette 传播。
- 订阅：owner destroy 自动释放、重复 close、100 次生命周期 listener 基线。
- cache：命中、LRU、8 MiB 上限、DPR/state 隔离、revision 往返稳定、main-thread guard。
- Markdown：有/无注入、代码块/链接/表格/fallback、CSS 每 fingerprint 一次。
- 审计：公共结构 QSS 零颜色，Foundation 无窗口树扫描。

## 文件变更清单

- 新增 `src/transbridge/ui/foundation/components/`
- 新增 `src/transbridge/ui/foundation/adapters.py`
- 新增 `src/transbridge/ui/foundation/icons.py`
- 修改 `src/transbridge/infra/markdown_renderer.py`
- 新增 `tests/ui/foundation/test_components.py`
- 新增 `tests/ui/foundation/test_theme_adapters.py`
- 新增 `tests/ui/foundation/test_icon_cache.py`

## 风险与回退

helper 过多可能演变为第二套 widget framework；只建立跨子系统复用的 primitive，业务组件仍留在各自模块。Markdown 保持无参数兼容，出现回归可暂时使用 default theme adapter，不回退 S02/S03。

## 未决问题

- 采用 Qt `QPixmapCache` namespace 还是本地 cost-aware LRU 由实现基准决定；两者都必须满足主线程、8 MiB 上限和可观测统计合同。
