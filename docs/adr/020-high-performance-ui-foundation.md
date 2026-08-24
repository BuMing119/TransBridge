# ADR-020：高性能 UI 基础框架、语义令牌与版本化主题扩展

- **状态**：已接受并实现（2026-08-24）
- **日期**：2026-08-19；FR25/FR26 接入修订：2026-08-24
- **对应需求**：[FR24.1～FR24.11、NFR1.4](../requirements.md)
- **关联 ADR**：[ADR-008](008-smart-assistant-code-layering.md)、[ADR-016](016-modular-monolith-application-composition.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-021](021-ui-presentation-modularization.md)
- **承接延期项**：`deferred-ui-architecture-upgrades` M69（i18n）、M70（无障碍）、M71（主题/颜色系统）

## 背景与约束

当前 PyQt6 GUI 没有统一主题所有者。只读盘点显示 `src/transbridge/ui/` 中约 36 个 Python 文件直接调用 `setStyleSheet()`、约 33 个文件包含硬编码颜色，只有 1 个文件设置了可访问名称。颜色、字号、圆角和控件状态分别由窗口或组件决定；`ui/app.py` 创建 `QApplication` 后直接构造主窗口，没有主题初始化、系统主题监听或统一 UI 配置注入。

本决策必须满足以下约束：

1. 性能优先于完整皮肤能力；未切换主题时不得产生轮询、窗口树扫描或重复样式解析。
2. 首期只交付内置浅色、深色、跟随系统，不交付完整主题编辑器、任意皮肤导入或主题市场，但必须保留稳定扩展接口。
3. 项目最低依赖是 `PyQt6>=6.5`。Qt 6.5 已提供 `QStyleHints.colorScheme` 与 `colorSchemeChanged`，可事件驱动地跟随系统；显式设置系统 color scheme 的 API 到 Qt 6.8 才提供，因此首期不得依赖该 setter。
4. `ConfigRepository` 是 `transbridge.ini` 的唯一所有者；UI 偏好不得再建立独立 `QSettings` 权威状态。
5. `AppRuntime` 和 application/domain 层保持无 PyQt 依赖。主题、locale 和可访问性属于 GUI adapter，不得进入业务运行规格或任务状态。
6. 迁移必须渐进，主题失败不得影响解析、翻译、保存、同步和发布数据。

Qt 官方说明 `QApplication.setPalette()` 可改变应用级 palette，但部分原生样式不会为全部绘制使用 palette；官方同时警告不要让 Qt Style Sheets 与 palette 共同争夺颜色属性。因此必须明确颜色所有权，不能继续混合无约束的局部 QSS 与应用 palette。

### 2026-08-24 接入修订：FR25/FR26 已完成

本 ADR 的主题模型、Palette-first、Provider 校验和性能预算不变，但生产接入基线已经更新：

- FR25/ADR-021 已把 MainWindow、Workbench、AI Translator 和 Smart Assistant 拆到公开 View/Presenter/Binding/composition 边界；FR24 不再向历史上帝窗口增加主题职责。
- FR26 已完成开始中心、Action Catalog/Intent Router、状态引导、任务中心、命令搜索、AI 快速运行、非模态操作计划、安全拖放和关键无障碍合同。FR24 迁移这些现有 View，不重建导航、工作流或第二条 command 路径。
- `build_runtime()` 已注册统一 `UiPreferenceRepository`；主题和 locale 偏好扩展该 `[ui]` 适配器及其原子 `ConfigRepository` 写入，不创建第二配置 owner。ThemeService 本身仍是 GUI adapter，由 `ui/app.py` 在 `QApplication` 创建后、`MainWindow` 构造前创建，不注册进 Qt-free `AppRuntime`。
- 当前 `WorkbenchWidget` 的生产组合以 Workbench/Step2 公开 facade、Guidance 和工作流切片为核心；历史 Step1/Step3 文件不是默认迁移目标，只有可达性审计证明仍由生产入口构造时才迁移。
- Theme/Locale revision 与 project/projection/render/run/operation revision 正交。订阅由 shell、Workbench、AI、Smart Assistant、ParaTranz/FOMOD 等 composition root 持有并随 root 释放；一次视觉 revision 不得提交 intent、重新预检、创建 Run ID、重建业务数据或触发网络/文件副作用。

权威交接清单见 [FR26 → FR24 migration inventory](../../plans/ui-foundation-framework/migration-inventory.md)，验收证据见 [FR26 S10 QA](../test-reports/guided-ui-workflows-s10-qa-2026-08-24.md)。

## 决策

### 1. 建立 GUI 专属的 UI Foundation

计划新增以下边界；名称是架构落点，具体拆分由 Plan 细化：

```text
src/transbridge/ui/foundation/
  model.py              # Qt-free immutable tokens/theme contracts
  registry.py           # provider registration + validation
  builtins.py           # TransBridge light/dark definitions
  theme_service.py      # GUI process theme owner (QObject adapter)
  qt_palette.py         # validated tokens -> QPalette / Qt snapshot
  icons.py              # theme/revision/size/DPR bounded cache
  locale_service.py     # gettext-backed locale contract (restart-first)
  accessibility.py      # focus/name/description/contrast conventions
  components/           # shared widget primitives and adapters
```

`ui/app.py` 在创建 `QApplication` 后、创建任何业务窗口前构造一个 `GuiFoundation`，显式注入 `ConfigRepository`，再把只读访问点传给 `MainWindow` 和需要的组件。禁止模块级可写 singleton；测试可为每个 `QApplication` 注入隔离实例。

`AppRuntime` 不拥有 `QObject`、`QPalette`、`QIcon` 或 UI 生命周期。GUI 关闭时由 `GuiFoundation.close()` 断开系统信号并释放本地缓存，不修改业务 runtime 的关闭合同。

### 2. 采用 Palette-first，而不是全局动态 QSS

首期使用稳定的 Qt `Fusion` style 和应用级 `QPalette` 作为标准控件颜色的唯一权威：

- 主题令牌在注册时解析、校验并编译成不可变 Qt 快照；切换时只调用一次应用级 palette 应用。
- 标准 Qt 控件依靠 palette/font 的既有继承与 `ApplicationPaletteChange` 更新；不得遍历 `QApplication.allWidgets()` 逐个重设样式。
- 主题相关颜色不得进入全局或局部 QSS。局部 QSS 只允许表达 palette 无法表达且与主题无关的静态结构属性，并须集中在公共组件内。
- 圆角、间距、控件高度等结构令牌由公共组件在构造时应用。首期浅/深主题共享结构令牌，因此切换颜色主题不重建布局。
- 自定义绘制、富文本、Markdown、Delegate、消息气泡和业务色块读取同一个 `QtThemeSnapshot`；只有这些不能依靠 palette 继承的适配器订阅主题 revision。
- 不在首期引入自定义 `QStyle`/`QProxyStyle`。若真实基准证明 Fusion/palette 无法达到视觉或性能门禁，再以独立 ADR 评估，不能在组件中零散覆写绘制。

这避免每次普通状态变化重新解析 QSS，也避免 palette 与 QSS 对同一颜色属性发生优先级冲突。历史 QSS 在迁移期可保留，但迁移清单必须标记其颜色所有权；被迁移组件不得继续写入裸颜色。

### 3. 令牌分为基础、语义和业务三层

`ThemeDefinition` 是 Qt-free、冻结且带 slots 的值对象，至少包含：

```text
ThemeManifest
  schema_version, provider_id, theme_id, display_name, version
  supported_schemes, resource_budget, compatibility

ThemeTokens
  primitives: neutral/accent/status scales, typography, spacing, radius, size
  semantic: window/surface/text/border/focus/selection/disabled/link/status
  domain: stage/label/diff/translation/task/report states
```

业务组件只引用 `semantic.*` 或 `domain.*`；不得引用 `primitives.blue_500` 一类具体色阶。基础色阶仅供主题提供者推导完整语义令牌。

`ThemeValidator` 在注册时一次性完成：

- schema/version 与稳定 ID 校验；
- 全部必需令牌存在、类型和值域合法；
- 前景/背景引用闭合且关键组合满足对比度门禁；
- 状态不仅有颜色，还声明文字、图标或边框等非颜色提示能力；
- 资源数量、单项大小、总大小和路径均在预算内；
- provider/theme ID 不冲突。

运行时热路径不再重复执行 schema、颜色字符串或对比度解析。

### 4. ThemeService 是唯一状态所有者

核心合同如下：

```text
ThemePreference = system | light | dark

ThemeRegistry.register(provider) -> RegistrationResult
ThemeRegistry.resolve(theme_id, scheme) -> ThemeDefinition | ThemeError

ThemeService.start() -> ThemeSnapshot
ThemeService.set_preference(preference) -> ThemeApplyResult
ThemeService.snapshot() -> ThemeSnapshot
ThemeService.theme_changed(revision, snapshot)  # only on effective change
ThemeService.close() -> None
```

`ThemeSnapshot` 至少包含单调递增 revision、theme/provider ID、effective scheme、token fingerprint、不可变 tokens、`QPalette` 和资源 cache namespace。

状态转换规则：

1. 启动读取统一配置 `[ui] theme_mode/theme_id/locale`；缺失时默认 `system + transbridge.default`。
2. `system` 模式读取 `QApplication.styleHints().colorScheme()`，并连接 `colorSchemeChanged`；不轮询注册表或操作系统设置。
3. `light/dark` 使用框架自身 scheme，不调用 Qt 6.8 才出现且并非所有平台支持的 `setColorScheme()`。
4. 新定义先完整 resolve/validate/compile，成功后才原子替换当前快照并应用；失败保留最后有效快照。
5. theme ID、scheme 与 fingerprint 未变化时返回 `unchanged`，不递增 revision、不写配置、不发信号、不触发重绘。
6. 配置持久化失败时当前内存主题不伪称已保存；界面提示“本次会话有效”，下次启动仍使用最后持久化值。

系统主题信号与用户操作都必须在 GUI 主线程串行处理。非 GUI 线程只能请求切换，不能创建或访问 `QPalette/QPixmap/QIcon`。

### 5. 扩展接口是声明式 Provider，不是皮肤代码执行口

预留的 `ThemeProvider` Protocol 只返回 manifest、令牌与受预算约束的资源：

```text
provider.manifest() -> ThemeManifest
provider.load(theme_id, scheme) -> ThemeDefinition
```

首期 registry 只注册代码内置 provider，不扫描用户目录、不动态 import、不执行外部脚本。未来主题编辑器或主题包必须先生成同一 schema 的声明式定义，再经过同一 validator；业务组件和 ThemeService 不因来源不同而分支。

禁止 provider：

- 注入任意 Python 回调、widget subclass 或原始全局 QSS；
- 修改 `QApplication`、配置文件或业务状态；
- 声明无界图片、字体或网络资源；
- 覆盖框架保留的稳定 theme/provider ID。

这样既保留编辑器、品牌主题和外部主题包接口，又避免首期承担插件发现、安全沙箱、兼容迁移和任意样式性能成本。

### 6. 资源与缓存按 revision 有界

- `ThemeDefinition`、已解析颜色和 `QPalette` 按 `(provider_id, theme_id, version, scheme, fingerprint)` 缓存；内置浅/深主题首次使用后可常驻。
- 图标/派生 pixmap 按 `(revision, icon_id, logical_size, DPR, state)` 缓存，默认成本上限 8 MiB；主题 revision 变化时旧 namespace 可整体淘汰，不逐 widget 清理。
- Qt pixmap 资源只在 GUI 主线程创建。可以使用带 TransBridge namespace 的 `QPixmapCache` key 或等价的本地 cost-aware LRU；不得依赖无上限 Python dict。
- 文本、Markdown 和 rich-text 的主题 CSS 按 revision 编译一次并复用；文档内容变化不得重新编译主题 CSS。
- 不预生成所有图标尺寸；按需生成并由有界缓存淘汰。

### 7. i18n 与无障碍是同一 Foundation 的兄弟合同，不进入主题热路径

主题、locale 和 accessibility 共享公共组件边界，但状态与事件分离：

- `LocaleService` 首期采用 Python `gettext` 资源和统一 `_()` 入口。切换 locale 默认写入配置并提示重启生效，避免扫描全部历史 widget 调用 `retranslateUi()`；接口保留未来显式 `locale_changed` 适配器。
- 缺失 locale 或 msgid 回退到源语言并记录一次聚合诊断，不在每次 paint/log 中重复报警。
- `accessibility.py` 提供公共组件构造期校验/帮助函数，不启动后台服务。可访问名称、描述、focus policy、焦点环和非颜色状态由组件合同承担。
- Qt 6.10 的系统 accessibility hints 可作为未来增强，但最低 PyQt6 6.5 路线不得依赖它。

### 8. 性能门禁先于全量迁移

扩展现有 `tests/performance/` 的版本化 registry，而不是新建第二套阈值：

1. 固定代表性窗口树：MainWindow、Workbench、AI Translator、Smart Assistant、ParaTranz 和一个表格/对话框组合；记录 fixture fingerprint、字体、DPI、平台和 Qt/PyQt 版本。
2. 冷进程测主题初始化耗时与 RSS 增量；热进程测浅/深往返切换 P95、最长事件循环 heartbeat、窗口打开回归。
3. 双主题预热后执行 100 次往返切换并强制稳定采样，验证 RSS 增长和缓存上限。
4. 断言 idle 时没有 Foundation timer；重复选择当前主题不产生 palette apply 或 `theme_changed`。
5. 开发机门禁提供早期反馈；Windows 10/11 固定硬件档位提供发布权威证据。

NFR1.4 的初始预算是单一真源。实现不得以“让测试通过”为由静默放宽；任何调整须带基线、原因和用户确认。

## 关键错误语义

错误以稳定 code 返回并聚合日志，不将底层路径或 provider 异常直接显示给用户：

| Code | 含义 | 行为 |
|---|---|---|
| `theme_unknown` | 配置或请求引用未知主题 | 回退内置默认，保留诊断 |
| `theme_schema_unsupported` | schema/version 不兼容 | 原子拒绝，不注册 |
| `theme_tokens_invalid` | 缺失/非法/对比度不合格 | 原子拒绝，不产生快照 |
| `theme_resource_budget_exceeded` | 资源超预算 | 原子拒绝，不加载资源 |
| `theme_apply_failed` | Qt 应用阶段异常 | 恢复最后有效 palette/snapshot |
| `theme_config_write_failed` | 偏好持久化失败 | 内存可继续使用，明确未保存 |
| `system_scheme_unknown` | 平台无法判定系统 scheme | 稳定回退内置浅色，不轮询 |
| `locale_resource_missing` | locale 资源不存在 | 回退源语言，提示并保持一致 |

## 备选方案

### A. 引入 qdarktheme、qt-material、QFluentWidgets 等第三方主题库

不采用。它们可快速获得视觉效果，但增加运行时/分发依赖，设计令牌、业务语义颜色、扩展 schema、性能缓存与迁移边界仍需自行建设；也会把长期 UI 合同绑定到外部库。未来若基准和维护成本证明有明显收益，应通过独立 ADR 引入，而不是从 Provider 接口绕过验证。

### B. 全局 QSS 模板作为唯一主题引擎

不采用。集中 QSS 比当前散落样式更好，但主题切换会重新应用整份样式，颜色和结构规则容易扩大匹配范围；自定义绘制仍需第二套颜色来源。Qt 还明确说明 style sheet 与 palette/外观 API 存在优先级和传播差异。首期只允许公共组件内的主题无关静态结构 QSS。

### C. 仅使用操作系统原生主题

不采用。实现成本和稳态成本最低，但 Windows/macOS 原生 style 对自定义 palette 的覆盖并不一致，无法提供稳定浅/深主题、业务语义色和未来主题 Provider 合同。

### D. 自定义 QStyle/QProxyStyle 全面绘制

暂不采用。控制力最强，也能减少 QSS，但实现面和回归面远大于当前需求；复杂表格、菜单、平台行为和无障碍都需长期维护。Palette-first 达不到门禁时再以基准证据评估。

## 迁移与回退

1. **基线先行**：先冻结窗口树与性能基线，不改现有视觉。
2. **Foundation 骨架**：实现 Qt-free model/validator/registry、内置主题、ThemeService、配置和性能门禁；仍可用兼容 palette 接近当前浅色外观。
3. **公共组件与关键入口**：迁移应用入口、主窗口、Workbench 基础控件和设置入口；验证数据行为零变化。
4. **高风险自定义绘制**：迁移表格/Delegate、Stage/标签/差异颜色、Smart Assistant 富文本和 AI 报告。
5. **其余窗口与门禁**：按子系统移除颜色 QSS，完成硬编码颜色清单；未迁移项必须显式登记。
6. **i18n/a11y 基础**：建立资源和组件合同，迁移关键路径；完整历史文案分阶段处理。

每一步都可通过关闭 UI Foundation feature flag 或选择兼容浅色 provider 回退；回退只影响外观。配置 schema 保留未知未来 theme ID，但运行时使用内置默认。删除旧 QSS/颜色常量必须在迁移清单与性能/视觉/功能门禁全部通过之后进行。

## 影响与风险

- 首期外观会从零散局部样式收敛到 Fusion/palette，一些控件可能发生可见变化；需要关键窗口截图与人工可读性检查，但截图不能替代行为和性能门禁。
- Palette-first 无法表达所有圆角和复杂状态，公共组件可能保留少量静态结构 QSS；必须通过审计区分“结构”与“主题颜色”。
- 主题切换是少见操作，但仍会触发 Qt 全局 palette change；250 ms/heartbeat 门禁限制其最坏影响。
- i18n 默认重启生效牺牲即时性，以换取一致性和低迁移成本；未来实时切换通过独立 adapter 增强，不改变 locale 资源合同。
- Provider 接口首期可测试但不开放动态发现，属于有意保留接口而非伪装已支持外部皮肤。

## 官方依据

- [Qt QStyleHints](https://doc.qt.io/qt-6/qstylehints.html)：`colorScheme`/`colorSchemeChanged` 自 Qt 6.5 可用；显式 setter 自 Qt 6.8 可用。
- [Qt QApplication](https://doc.qt.io/qt-6/qapplication.html)：应用 palette/style 生命周期，以及 palette 与 style sheet 混用警告。
- [Qt Style Sheet Syntax](https://doc.qt.io/qt-6/stylesheet-syntax.html)：style sheet 的 palette/font 传播是在应用样式时推送，与普通继承行为不同。
- [Qt QPixmapCache](https://doc.qt.io/qt-6/qpixmapcache.html)：pixmap cache 为应用级有界缓存，且只能在 GUI 主线程使用。
