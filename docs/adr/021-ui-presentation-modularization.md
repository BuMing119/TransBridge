# ADR-021：UI 展示层模块化、功能切片与上帝窗口拆分

- **状态**：已接受并实施（2026-08-19）
- **日期**：2026-08-19
- **对应需求**：[FR25、NFR1.5](../requirements.md)
- **关联 ADR**：[ADR-008](008-smart-assistant-code-layering.md)、[ADR-016](016-modular-monolith-application-composition.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-020](020-high-performance-ui-foundation.md)
- **扩展关系**：扩展 ADR-008 的模块粒度规范，承接 FR10 明确排除的 UI 超重文件；不改写 FR10 的历史完成结论

## 背景与约束

当前 UI 展示层已出现多处超重模块：

| 模块 | 物理行数 | 类方法数 | 主要混合职责 |
|---|---:|---:|---|
| `ui/main_window.py` | 2140 | 80 | 壳、菜单、解析、迁移、操作、项目/版本/快照、工具窗口、关闭 |
| `ui/tools/ai_translator/ai_translator_window.py` | 1617 | 42 | 表单、配置、作用域、运行、润色、报告 |
| `ui/tools/smart_assistant/chat_widget.py` | 1250 | 68 | 消息、流式 UI、确认、Session/Task binding、兼容控制路径 |
| `ui/workbench/step2.py` | 1151 | 49 | 标签对话框、筛选、表格、编辑、菜单、projection |
| `ui/workbench/step1.py` | 931 | 25 | 数据源表单、批次解析、worker、迁移 |
| `ui/workbench/cards/upload_card.py` | 827 | 30 | 多种上传流程和设置面板 |
| `ui/workbench/cards/write_card.py` | 814 | 26 | 多格式写回流程和设置面板 |
| `ui/context.py` | 624 | 68 | GUI projection 与 legacy compatibility facade |

FR10/ADR-008 曾针对 Smart Assistant 后端建立拆分规则，但明确把 `chat_widget.py` 和 `main_window.py` 排除。后续 ChatWidget 虽提取了 `ConversationOrchestrator`、`ToolExecutionHandler` 和 `SessionController`，UI 仍保留大量 binding/compatibility 逻辑并再次增长。历史经验说明，仅把若干方法搬到新类，不能自动建立可持续边界。

本决策的约束：

1. 这是零行为、零视觉变化的结构重构；不能同时实施主题、交互或业务变更。
2. 复用 ADR-016 的 application use case、ADR-018 的 projection/aggregate owner 和 ADR-019 的 TaskRuntime，不在 UI 中建立第二套业务状态。
3. 性能优先。拆分不得增加高频 signal 链、反射、parent 查找、重复 projection 或常驻 timer。
4. 保留现有被测试和跨包使用的入口，如 `MainWindow`、`Step2PreviewWidget`、`AITranslatorWindow` 和 `ChatWidget`。
5. 先完成本 ADR 的生产迁移，再把 ADR-020 ThemeService 接入拆分后的组件边界。

## 决策

### 1. 采用功能切片内的 View / Presenter / Binding，而非全局 UI 框架

每个 UI 功能切片可包含以下角色，但不要求为空洞一致性创建所有文件：

| 角色 | 职责 | 允许依赖 | 禁止 |
|---|---|---|---|
| View | 构造 widget、采集输入、渲染 ViewState、发出用户意图 | PyQt、纯 ViewState、公共 UI primitive | 业务持久化、网络、Task command、跨窗口私有属性 |
| Presenter/Coordinator | 页面级交互编排、输入校验、调用 use case、将结果映射为 ViewState | ViewPort Protocol、application use cases、纯模型 | 构造具体 widget、直接 paint、复制领域规则 |
| Binding/Adapter | 连接 projection/Task/worker/Qt 事件，管理订阅和 generation | 公开 projection/ports、Presenter、Qt signal adapter | 拥有业务权威状态、无限生命周期订阅 |
| Composition | 创建 View/Presenter/Binding 并显式注入依赖 | 具体实现 | 业务逻辑和状态分支 |

Presenter 默认是普通 Python 对象。仅当对象必须拥有 Qt signal、timer、thread affinity 或 parent 生命周期时才继承 `QObject`。View 与 Presenter 在 GUI 主线程可直接调用窄接口；不得为了“解耦”把每次点击和每个字段都转换为多层 queued signal。

### 2. 公共合同使用 ViewPort 与不可变 ViewState

计划新增的合同形态：

```text
FeatureViewPort (Protocol)
  render(state: FeatureViewState) -> None
  show_error(message: UiMessage) -> None
  set_busy(busy: BusyState) -> None

FeaturePresenter
  initialize() -> None
  handle_<intent>(...) -> None
  close() -> None

FeatureBinding
  start() -> None
  close() -> None
```

`ViewState` 使用冻结 dataclass/tuple，表达呈现所需状态，不暴露 domain aggregate 或可写 AppContext。高容量表格可保留现有 model/projection 与增量渲染，不强制把全部行复制进一个巨大 ViewState；此时 ViewState 只持有 revision、筛选摘要、行引用/批次和统计。

View 对外暴露命名 signal 或注册回调；Presenter 只调用 ViewPort 公共方法。禁止：

- `getattr(parent, "_step2")`、`_find_main_window()`、遍历 parent 链寻找服务；
- 一个 feature 直接访问另一个 feature 的 `_worker/_controller/_project_bar/_table`；
- Presenter import 具体 `MainWindow/QDialog`；
- View import persistence repository、具体 HTTP client 或 translation workload。

### 3. MainWindow 保留公开类，收敛为应用壳

`transbridge.ui.main_window.MainWindow` 路径和类名保留，目标只负责：

- 创建顶层 tabs/docks/status/menu 容器；
- 装配 feature facade/coordinator；
- 映射顶层导航和公共 UiMessage；
- 实现单一关闭协议并委托各 feature close。

计划新增内聚模块：

```text
ui/shell/
  menu_builder.py          # QAction/menu 构造，输出 MainWindowActions
  status_presenter.py      # HTTP/操作状态呈现
  window_lifecycle.py      # geometry、关闭协调、资源释放
  tool_windows.py          # 工具窗口创建/复用，不含工具业务

ui/coordinators/
  parse_coordinator.py     # 解析/迁移 UI 编排 -> application contracts
  operation_coordinator.py # 上传/下载/写回 UI 编排
  project_coordinator.py   # 项目/版本/快照/导入导出 UI 编排
```

不创建一个接收全部依赖并拥有全部方法的 `MainWindowController`。每个 coordinator 只管理一个业务交互切片，通过 `WorkbenchViewPort`、`ProjectBarViewPort` 等窄接口更新 UI。

### 4. Workbench 按输入、筛选、表格与操作切片

`Step1Widget`/`Step2PreviewWidget` 公开入口保留为 facade/composition root：

```text
workbench/
  source_input_view.py / parse_presenter.py
  filters_view.py / filters_presenter.py
  translation_table.py / table_presenter.py
  labels_view.py / labels_presenter.py
  entry_menu.py
  progress_view.py
  cards/...               # 各 operation 的 form/view + presenter
```

`translation_table` 保持现有 row identity、render generation、queued batch 和编辑安全规则。Filter revision、projection revision、render generation 与未来 theme revision 分离；Presenter 不能用一次 `refresh()` 隐式同时推进四种状态。

用户自定义标签数据、Stage 和 TranslationEntry 仍由现有 projection/application command 管理。标签对话框和菜单只产生 intent，不直接写入 `_entry_labels` 或业务对象。

### 5. AI Translator 分成配置、作用域、运行和结果四个切片

`AITranslatorWindow` 保留兼容入口并组合：

```text
ai_translator/
  config_view.py / config_presenter.py
  scope_view.py / scope_presenter.py
  run_controller.py
  result_presenter.py
```

- Config presenter 负责读取/验证/防抖保存统一配置，不创建 worker/progress window。
- Scope presenter 只处理 Stage/标签/分类选择和 entry ID projection，不构建 workload。
- Run controller 把已验证配置与 scope 转成 application request/Task 引用，协调现有 translation/polish/mixed 入口。
- Result presenter 把 canonical report/candidate 映射到 preview/report UI，不修改正式集合；正式提交继续走既有唯一提交点。

消除从 AI 子窗口 import `MainWindow`、`Step2` 私有常量/方法和 `_find_main_window()`；所需导航、定位、进度通过显式 ports 注入。

### 6. ChatWidget 只拥有聊天 View 组合

后端权威保持不变：

```text
SessionController -> ConversationOrchestrator -> ToolExecutionHandler
SessionAggregate / TaskRuntime -> projections/events
```

UI 计划拆为：

```text
smart_assistant/
  chat_widget.py          # facade + layout composition
  message_list_view.py    # bubble/card/list/scroll/limit
  streaming_presenter.py  # buffer、flush、Markdown呈现 generation
  session_binding.py      # Session projection/save/load/name events
  task_binding.py         # Task projection/completion events
  confirmation_view.py    # plan/tool/HITL presentation intents
```

`ChatWidget` 不再转发后端私有方法或维护兼容状态机。ADR-008 D12 的新旧并行路径必须通过等价日志/测试完成删除，不能无限期保留“双写但只读一边”。

### 7. AppContext 只做既有 Projection/Compatibility Facade

本 ADR 不重开 ADR-018 的状态所有权决策。`ui/context.py` 的处理顺序：

1. 识别公开 projection facade、legacy compatibility 和纯类型/DTO；
2. 将纯类型/映射 helper 移到内聚模块；
3. 新 Presenter 只依赖窄 projection/use-case ports，不依赖完整 AppContext；
4. 保留薄 AppContext facade 给未迁移窗口；只有全量调用方迁移且 V2 门禁通过后才删除 legacy API。

不得仅为降低行数把 AppContext 方法平均分到多个仍共享可写状态的 mixin。

### 8. 生命周期与事件顺序是强制合同

每个 feature composition 明确 owner：

- Qt parent 拥有 widget；feature facade 拥有 Presenter/Binding；
- `Binding.start()` 返回或持有可释放订阅句柄；`close()` 幂等并在 widget 销毁前/同时执行；
- worker/Task 仍由既有 runtime owner 管理，UI close 只取消自己的 subscription，不伪称关闭全局任务；
- 迟到结果按 run_id、owner、projection revision 或 render generation 丢弃；
- 一次用户 intent 最多提交一次 application command；新增委托层不得重复 emit、重复错误提示或吞异常。

跨线程事件只在 Binding 边界排队到 GUI thread。Presenter 和 View 的常规同线程交互直接调用，避免额外 event-loop 排队。

### 9. Characterization 与性能门禁先于搬迁

拆分前为每个切片记录：

- 用户 intent → application/use-case/worker 调用序列；
- View 的 enabled/visible/text/selection/progress 等公开状态；
- projection revision、render generation、Task run_id/终态；
- 错误/取消/关闭和迟到回调；
- 窗口打开、主要交互、heartbeat、RSS 和订阅/worker 数量。

测试比较可观察合同，不锁定私有方法或完整像素截图。截图只辅助视觉等价，不能替代 command、状态和性能证据。

### 10. 规模门禁是诊断，不是拆分算法

沿用并扩展 ADR-008：

- 目标：手写 UI module ≤500 行、primary class ≤30 方法；
- hard gate：>700 行或 >40 方法必须有临时豁免；
- audit 同时检查 import cycle、跨组件私有属性、parent lookup、View 直接 infra/persistence、无释放订阅和 module-level mutable singleton；
- 豁免必须有 owner、reason、expires_when，并在最终 Story 复核；
- 禁止用 mixin、继承链、同名 `_helpers.py` 杂物抽屉或几十个单方法文件规避。

完成判定以职责、依赖、行为、性能和生命周期为主；行数只负责触发审查和阻止明显回生。

## 关键兼容与错误语义

| 场景 | 合同 |
|---|---|
| 旧 import 路径 | 薄 facade/重导出继续工作，并有删除门禁 |
| Presenter/View 初始化失败 | feature 不进入半连接状态；释放已建立订阅并显示原等价错误 |
| application command 失败 | 保持既有错误分类、消息与可重试性，不由委托层改写成成功 |
| View 已销毁后的迟到事件 | owner/generation guard 忽略，不访问 deleted QObject |
| close 中存在运行任务 | 仅解除 UI 订阅；按现有 TaskRuntime/窗口关闭策略处理，不清空权威任务 |
| 单切片迁移回归 | 回退该 facade 到旧实现，不回退已稳定的独立切片 |

## 备选方案

### A. 只把方法移动到多个 helper/mixin

不采用。它能降低单文件行数，但共享 `self` 私有状态和依赖方向不变，测试与生命周期问题仍存在，且容易形成隐式多重继承。

### B. 引入通用 MVVM/MVP 第三方框架

不采用。当前 PyQt Widgets 项目已有 application use cases、projection 和显式 runtime，引入新框架会增加概念、依赖和事件层。采用轻量 Protocol/ViewState/组合即可获得边界，且性能更可控。

### C. 全量重写 UI 或迁移 Qt Quick/QML

不采用。风险、验证和视觉变化远超零行为重构目标，也会阻塞主题 Foundation。未来若产品需要 QML，应独立立项。

### D. 先实施主题框架，再顺手拆文件

不采用。结构与视觉变更混合后无法可靠定位性能和功能回归，且 ThemeService 会继续耦合现有上帝类。仅共享 baseline/inventory 可以先行。

### E. 只设置行数 lint，不设计边界

不采用。会诱导机械拆分和 helper 杂物抽屉；行数必须与依赖、职责、生命周期和行为合同共同验收。

## 迁移与回退

1. 冻结 UI 行为/性能/依赖与生命周期基线。
2. 建立 ViewPort/ViewState/UiMessage、订阅句柄和静态审计工具，不搬业务。
3. MainWindow 壳化并抽出 feature coordinator；保留旧公开类。
4. Workbench 按输入、筛选、表格、标签和卡片拆分。
5. AI Translator 拆为配置、作用域、运行与结果切片。
6. ChatWidget 拆 View/binding 并删除已验证的旧并行控制路径。
7. 处理 AppContext facade 和其余超重组件，清除跨模块私有访问。
8. 运行全量行为、性能、生命周期和静态门禁；形成 ADR-020 UI Foundation 的稳定接入面。

每步按 feature slice 独立提交和验证。旧 facade 在对应切片验证通过前保留；回退切换 composition 到旧实现，不改变业务数据格式或 application contracts。不得用长期双写作为回退机制。

## 影响与风险

- 主要风险不是移动代码本身，而是隐式共享状态、callback 顺序和 QObject 生命周期。Story 必须先建立依赖/事件表。
- Presenter 数量过多可能增加间接层和调试成本；只为有独立状态/生命周期/验收的切片建立 Presenter。
- MainWindow 与 Workbench 当前大量访问私有属性，需要先补公开 ViewPort，再迁移调用方，不能一次删除。
- ChatWidget 的历史双路径可能造成事件重复；先用计数/序列对比定位权威路径，再删除 compatibility。
- UI Foundation 文档仍有效，但其生产实现需在本迁移后重新核对文件落点；FR24 S06/S07 明确被本 Epic 阻塞。

## 实施结果（2026-08-19）

- `MainWindow`、`AITranslatorWindow`、`ChatWidget` 分别收敛至 473/13、449/30、451/30（物理行/primary class 方法）；Workbench 与其余窗口按功能切片完成。
- Main/Workbench/AI/Chat 使用公开 composition ports；parent lookup、跨组件私有访问、View-infra、模块 UI singleton 和 UI import cycle 审计归零。
- Chat 的任务进度改为事件触发并以 100 ms single-shot 合并刷新；stream、run 与 render 都有 generation/run-id 和关闭保护。
- 原生 Windows Qt 对比门禁使用 10,000 条数据、20 次样本和 100 次生命周期：冷启动、窗口打开、交互 P95、heartbeat 与 RSS 均在 NFR1.5 预算内。
- 剩余规模 review 例外仅为 Workbench compatibility facade 与 ADR-018 `AppContext`，均在审计表中登记 owner、reason 和退出条件。
