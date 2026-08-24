# UI 展示层模块化与上帝窗口拆分实施计划

- **Feature slug**：`ui-presentation-modularization`
- **状态**：已完成（2026-08-19）
- **日期**：2026-08-19
- **对应需求**：[FR25、NFR1.5](../../docs/requirements.md)
- **架构**：[ADR-021](../../docs/adr/021-ui-presentation-modularization.md)
- **后续 Epic**：[ui-foundation-framework](../ui-foundation-framework/plan.md)；除共享基线外，FR24 的生产接入由本 Epic 阻塞
- **目标平台**：Windows 10/11、Python 3.12、PyQt6 6.5+

## 目标

在不改变用户行为、视觉、业务状态所有权和公共 import 路径的前提下，把 MainWindow、Workbench、AI Translator、Smart Assistant 等超重 UI 模块拆为可独立测试和释放的功能切片，为后续高性能主题 Foundation 提供稳定接入面。

完成后应满足：

1. `MainWindow`、`Step1Widget`、`Step2PreviewWidget`、`AITranslatorWindow`、`ChatWidget` 等公开入口仍兼容，内部职责改为 View / Presenter-Coordinator / Binding / Composition。
2. UI 不再通过 parent 查找、跨组件私有属性或完整 `AppContext` 获得隐式依赖；application command、projection 和 TaskRuntime 仍是权威状态源。
3. 拆分不增加重复 signal、轮询、完整数据复制或无界订阅；关键窗口和交互满足 NFR1.5。
4. 手写 UI 模块目标不超过 500 物理行、primary class 不超过 30 方法；超过 hard gate 的例外有 owner、原因和退出条件。
5. 每个迁移切片都有行为刻画、生命周期、迟到事件、错误/取消、性能和回退证据。
6. ADR-020 可通过窄 View/adapter 边界接入，不再把 ThemeService 塞进上帝窗口。

## 非目标

- 不实现主题切换、语义令牌、皮肤、主题编辑器、i18n 或无障碍改造。
- 不改变布局、文案、快捷键、焦点顺序、业务规则、数据格式或网络协议。
- 不重写 domain/application/runtime，不建立 UI 私有的第二套任务、项目或会话状态。
- 不迁移到 QML，不引入第三方 MVVM/MVP 框架。
- 不以机械拆文件、mixin、继承链或 `_helpers.py` 杂物抽屉作为完成标准。

## 实施前事实

- `src/transbridge/ui/main_window.py` 约 2140 行、80 个方法，混合壳、菜单、解析、项目、操作、工具窗口和关闭协议。
- `ai_translator_window.py` 约 1617 行、42 个方法；`chat_widget.py` 约 1250 行、68 个方法；`workbench/step2.py` 约 1151 行、49 个方法。
- `step1.py`、`upload_card.py`、`write_card.py` 也接近或超过 800 行；`ui/context.py` 约 624 行、68 个方法。
- FR10/ADR-008 完成了 Smart Assistant 后端拆分，但明确排除了 `chat_widget.py` 和 `main_window.py`；历史 ChatWidget 拆分后又出现增长，说明必须建立依赖和生命周期边界，而非只搬方法。
- 现有测试直接 import `MainWindow`、`_AutoSaveManager`、`Step2PreviewWidget` 等符号，公共路径需要兼容 facade 或重导出。
- UI 内存在跨组件私有访问、`_find_main_window()`、parent 链查找，以及 AI Translator 对 Step2 私有 helper/常量的依赖。
- `tests/performance/benchmark_cases.py` 已提供性能证据框架；本 Epic 扩展它，不另建不可比的计时体系。

## 实施原则

1. **先冻结合同**：先记录 intent、command、可见状态、事件顺序、性能和资源数量，再移动代码。
2. **按功能切片**：Story 以可独立验收的交互能力为单位，不按文件或技术层横切。
3. **显式窄依赖**：ViewPort、不可变 ViewState、application use case 和 projection port 通过 composition 注入。
4. **同线程直调**：GUI 主线程中的 View/Presenter 使用普通窄接口；仅跨线程 Binding 使用 queued signal。
5. **单一状态所有权**：UI 只映射状态和提交 intent，不复制 Session/Task/Project/Translation 权威状态。
6. **兼容入口先保留**：旧公开类和 import 路径在调用方迁完、门禁通过前保持稳定。
7. **切片级回退**：每个 facade 可在迁移失败时切回旧 composition，不采用长期双写。
8. **规模只是诊断**：完成判定同时看行为、依赖方向、生命周期、性能和可测试性。

## Story 总览与依赖

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---:|---|
| S01 | UI 行为、性能、依赖和生命周期基线 | P0 | 无 |
| S02 | 展示层合同、composition 约定与静态审计门禁 | P0 | S01 |
| S03 | MainWindow 壳化与顶层 coordinator 拆分 | P0 | S02 |
| S04 | Workbench 输入、筛选、表格、标签和操作卡片切片 | P0 | S02；S03 的公开壳端口 |
| S05 | AI Translator 配置、作用域、运行和结果切片 | P1 | S02；S04 的公开 selection/projection port |
| S06 | ChatWidget View/stream/session/task binding 收敛 | P1 | S02；S03 的工具窗口 composition |
| S07 | 其余超重组件与 AppContext compatibility facade 收敛 | P1 | S03～S06 |
| S08 | 全量等价、性能、生命周期和依赖终验；交接 FR24 | P0 | S01～S07 |

## Story-01：冻结 UI 行为、性能、依赖与生命周期基线

**详细文档**：[story-01-characterization-baseline.md](stories/story-01-characterization-baseline.md)

**验收标准**：

- [x] 为 MainWindow、Step1/Step2、AI Translator、ChatWidget 和主要 operation cards 记录用户 intent → command/use case/worker → 可见状态的序列。
- [x] 覆盖成功、校验失败、业务失败、取消、关闭、窗口销毁后迟到回调，以及重复点击/重复终态幂等。
- [x] 建立固定数据集与固定窗口的冷启动、窗口打开、关键交互、GUI heartbeat、RSS、订阅/timer/worker 数量基线。
- [x] 生成依赖清单，标出跨组件私有访问、parent lookup、View 直连 infra/persistence、并行控制路径和公共 import 消费者。
- [x] 基线测试不锁定计划删除的私有方法，不以整屏像素截图作为唯一合同。

**文件落点**：

- 新增 `plans/ui-presentation-modularization/dependency-inventory.md`
- 新增 `tests/ui/characterization/test_main_window_contract.py`
- 新增 `tests/ui/characterization/test_workbench_contract.py`
- 新增 `tests/ui/characterization/test_ai_translator_contract.py`
- 新增 `tests/ui/characterization/test_chat_widget_contract.py`
- 修改 `tests/performance/benchmark_cases.py`
- 新增 `tests/performance/test_ui_modularization_performance.py`

**实施步骤**：盘点公开入口与事件源；为每个切片画出 owner/调用序列；抽取可观察断言；固定性能场景和 warm-up；记录现状例外，不修改生产结构。

**验证**：characterization tests 在当前实现通过；Windows 权威性能样本可复现；inventory 中每个违规点都有迁移 Story。

## Story-02：建立展示层合同、composition 与静态审计

**详细文档**：[story-02-presentation-contracts.md](stories/story-02-presentation-contracts.md)

**验收标准**：

- [x] 提供 Qt-free 的 ViewPort/不可变 ViewState/UiMessage/BusyState 基础合同，以及幂等 Subscription/Binding 生命周期合同。
- [x] 形成 composition 约定：具体 Qt View 只在 feature facade 或 shell 组装，Presenter 不 import 具体窗口。
- [x] 静态审计能识别新增跨组件私有属性、parent lookup、View 直连 repository/client、无 owner 订阅和模块级可写 UI singleton。
- [x] 规模审计执行目标与 hard gate，并支持带 owner/reason/expires_when 的临时豁免。
- [x] 合同不强制复制大型 table rows，也不为同线程交互增加 queued signal 层。

**文件落点**：

- 新增 `src/transbridge/ui/presentation/__init__.py`
- 新增 `src/transbridge/ui/presentation/contracts.py`
- 新增 `src/transbridge/ui/presentation/messages.py`
- 新增 `src/transbridge/ui/presentation/subscriptions.py`
- 新增 `scripts/audit_ui_modularity.py`
- 新增 `tests/contracts/ui/test_presentation_contracts.py`
- 新增 `tests/contracts/ui/test_ui_modularity_audit.py`

**实施步骤**：从 S01 的最小共同语义提炼合同；定义 ownership/close；实现 AST/文本审计及基线豁免；用测试替身证明 presenter 无 Qt 依赖和订阅可释放。

**验证**：合同单测、审计自测、对当前仓库只报告已登记基线；新增人工违规 fixture 会稳定失败。

## Story-03：MainWindow 壳化与顶层协调器

**详细文档**：[story-03-main-window-shell.md](stories/story-03-main-window-shell.md)

**验收标准**：

- [x] `transbridge.ui.main_window.MainWindow` 与 `_AutoSaveManager` 等已用公共符号路径保持兼容。
- [x] MainWindow 只负责顶层布局/导航、feature composition、公共消息和单一关闭协议。
- [x] 菜单/Action、状态呈现、工具窗口复用、geometry/close 分别有明确 owner。
- [x] 解析/迁移、上传下载写回、项目/版本/快照分别进入窄 coordinator；每个 intent 最多提交一次 command。
- [x] coordinator 通过公开 ViewPort/use-case port 工作，不读 `_step2._table`、`_worker` 等私有状态。
- [x] 创建/关闭 100 次无残留订阅、timer、worker 或 deleted QObject 回调。

**文件落点**：

- 修改 `src/transbridge/ui/main_window.py`
- 新增 `src/transbridge/ui/shell/menu_builder.py`
- 新增 `src/transbridge/ui/shell/status_presenter.py`
- 新增 `src/transbridge/ui/shell/window_lifecycle.py`
- 新增 `src/transbridge/ui/shell/tool_windows.py`
- 新增 `src/transbridge/ui/coordinators/parse_coordinator.py`
- 新增 `src/transbridge/ui/coordinators/operation_coordinator.py`
- 新增 `src/transbridge/ui/coordinators/project_coordinator.py`
- 新增/修改 `tests/ui/test_main_window_*.py`

**实施步骤**：先加公开 ports；抽出无状态 action 构造；迁移状态/工具/关闭；按 coordinator 逐切片替换；每步对照 S01 序列并删除对应旧分支。

**验证**：现有 MainWindow tests、characterization、菜单/工具/关闭 GUI tests、生命周期循环和性能对比。

## Story-04：Workbench 功能切片与增量渲染保真

**详细文档**：[story-04-workbench-slices.md](stories/story-04-workbench-slices.md)

**验收标准**：

- [x] `Step1Widget`、`Step2PreviewWidget` 和既有 cards import 路径保持兼容 facade。
- [x] 数据源/解析、筛选、翻译表格、标签、entry menu、进度和 upload/download/write cards 各有内聚 View/Presenter 或 controller。
- [x] table row identity、selection、edit safety、projection revision、render generation 和 queued batch 行为与基线一致。
- [x] filter revision、projection revision、render generation 明确分离，不用全量 `refresh()` 隐式推进多个状态。
- [x] 标签/Stage/TranslationEntry 修改只通过既有 application command/projection，不由对话框直接改业务对象。
- [x] 大数据交互不复制完整 collection 到 ViewState，窗口打开/筛选/滚动/编辑回归满足 NFR1.5。

**文件落点**：

- 修改 `src/transbridge/ui/workbench/step1.py`
- 修改 `src/transbridge/ui/workbench/step2.py`
- 新增 `src/transbridge/ui/workbench/source_input_view.py`
- 新增 `src/transbridge/ui/workbench/parse_presenter.py`
- 新增 `src/transbridge/ui/workbench/filters_view.py`
- 新增 `src/transbridge/ui/workbench/filters_presenter.py`
- 新增 `src/transbridge/ui/workbench/translation_table.py`
- 新增 `src/transbridge/ui/workbench/table_presenter.py`
- 新增 `src/transbridge/ui/workbench/labels_view.py`
- 新增 `src/transbridge/ui/workbench/labels_presenter.py`
- 新增 `src/transbridge/ui/workbench/entry_menu.py`
- 新增 `src/transbridge/ui/workbench/progress_view.py`
- 重构 `src/transbridge/ui/workbench/cards/*.py` 为 form/view + presenter
- 新增/修改 `tests/ui/test_step1_*.py`、`tests/ui/test_step2_*.py`、`tests/ui/test_*_card*.py`

**实施步骤**：先导出公共 selection/projection ports；按 Step1、Step2 filter、table、labels/menu、cards 顺序迁移；保留增量 model；逐切片移除旧实现和私有跨访。

**验证**：既有 incremental rendering/background GUI tests、S01 characterization、10k+ entry 场景、取消/迟到批次和 100 次创建销毁。

## Story-05：AI Translator 配置、作用域、运行与结果切片

**详细文档**：[story-05-ai-translator-slices.md](stories/story-05-ai-translator-slices.md)

**验收标准**：

- [x] `AITranslatorWindow` 公开入口、现有翻译/润色/混合入口和报告行为兼容。
- [x] Config、Scope、Run、Result 四个切片职责独立；配置保存、workload 构建、worker/task、结果映射没有循环依赖。
- [x] 不再 import MainWindow 或 Step2 私有 helper/常量，不再使用 `_find_main_window()`；定位/选择/进度使用显式 ports。
- [x] 正式 TranslationEntry 更新仍走既有唯一提交点，预览/报告不能隐式写回。
- [x] run_id/owner/generation 防止窗口关闭、重跑或切换 scope 后的迟到结果污染当前 UI。
- [x] 高频进度和结果呈现满足 NFR1.5，无额外全量 projection 复制。

**文件落点**：

- 修改 `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
- 新增 `src/transbridge/ui/tools/ai_translator/config_view.py`
- 新增 `src/transbridge/ui/tools/ai_translator/config_presenter.py`
- 新增 `src/transbridge/ui/tools/ai_translator/scope_view.py`
- 新增 `src/transbridge/ui/tools/ai_translator/scope_presenter.py`
- 新增 `src/transbridge/ui/tools/ai_translator/run_controller.py`
- 新增 `src/transbridge/ui/tools/ai_translator/result_presenter.py`
- 修改相关 progress/report dialogs 的公开 adapter
- 新增/修改 `tests/ui/tools/test_ai_translator_*.py`

**实施步骤**：先替换 Step2 私有依赖为 port；迁移配置与 scope；建立统一 run request；接入现有 worker/Task；迁移结果/提交边界；删除旧 parent lookup 和重复分支。

**验证**：三种运行入口、空/非法 scope、取消/重跑/关闭、失败恢复、报告/正式提交和性能/lifecycle tests。

## Story-06：ChatWidget View 与 Binding 收敛

**详细文档**：[story-06-chat-widget-convergence.md](stories/story-06-chat-widget-convergence.md)

**验收标准**：

- [x] `ChatWidget` 公开路径和 Panel/SessionList 现有装配保持兼容，主体只保留 layout 与 composition。
- [x] message list、streaming、session binding、task binding、confirmation view 各自有明确 owner 和幂等 close。
- [x] SessionController → ConversationOrchestrator → ToolExecutionHandler 继续作为后端权威路径，UI 不维护第二状态机。
- [x] 用事件计数/序列证明新旧并行控制路径等价后删除旧路径；没有双写、重复 bubble、重复终态或重复保存。
- [x] streaming buffer/flush/Markdown generation 在高频 token 下有界，并能丢弃关闭/切会话后的迟到事件。
- [x] 100 次打开/关闭/切会话后订阅、timer、task binding 和 RSS 回到预热容差。

**文件落点**：

- 修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`
- 新增 `src/transbridge/ui/tools/smart_assistant/message_list_view.py`
- 新增 `src/transbridge/ui/tools/smart_assistant/streaming_presenter.py`
- 新增 `src/transbridge/ui/tools/smart_assistant/session_binding.py`
- 新增 `src/transbridge/ui/tools/smart_assistant/task_binding.py`
- 新增 `src/transbridge/ui/tools/smart_assistant/confirmation_view.py`
- 修改 `src/transbridge/ui/tools/smart_assistant/panel.py`
- 新增/修改 `tests/ui/tools/smart_assistant/test_*.py`

**实施步骤**：记录双路径事件；先抽纯消息列表；迁 streaming；迁 session/task bindings；迁 confirmation intents；切换权威路径并删除 compatibility forwarding。

**验证**：既有 llm-chat/session/task tests、流式压力、切会话/关闭迟到事件、确认/取消、事件唯一性和资源循环。

## Story-07：其余超重组件与 AppContext 兼容面收敛

**详细文档**：[story-07-remaining-ui-and-context.md](stories/story-07-remaining-ui-and-context.md)

**验收标准**：

- [x] 复核并处理仍越过 hard gate 的 UI 模块，例如 ParaTranz detail、translation progress、download card 等；没有无 owner 的豁免。
- [x] `ui/context.py` 只保留 ADR-018 定义的 projection/compatibility facade；纯 DTO/helper 迁到内聚模块，新 Presenter 不依赖完整 AppContext。
- [x] 不用共享可写状态 mixin 降行数；每个新模块有单一职责和单向依赖。
- [x] 全仓跨组件私有访问、parent lookup、View 直连 infra/persistence 清单归零，或有时限豁免与承接 Story。
- [x] 旧 compatibility API 只在调用方迁完且 V2 门禁通过后删除；未迁消费者不被静默破坏。

**文件落点**：

- 修改 `src/transbridge/ui/context.py`
- 按清单重构 `src/transbridge/ui/paratranz/string_detail_dialog.py`
- 按清单重构 `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py`
- 按清单重构 `src/transbridge/ui/workbench/cards/download_card.py`
- 新增内聚 projection DTO/mapping 模块（具体路径由 S01 inventory 定稿）
- 修改 `scripts/audit_ui_modularity.py`
- 新增/修改相应 UI、contract 和 compatibility tests

**实施步骤**：重跑规模/依赖审计；按风险和依赖排序；先抽公开 port/纯映射，再迁调用方；最后收窄 facade 和清理到期豁免。

**验证**：全量 UI contract、旧 import/API compatibility、静态审计、生命周期和相关业务回归。

## Story-08：全量终验与 UI Foundation 交接

**详细文档**：[story-08-final-gates-and-handoff.md](stories/story-08-final-gates-and-handoff.md)

**验收标准**：

- [x] S01 的全部可观察行为序列在新 composition 下等价；视觉抽检无非预期布局/文案/焦点变化。
- [x] 冷启动、窗口打开和关键交互 P95/RSS 回归不超过 5% 或 10 ms 中较宽者，GUI heartbeat ≤200 ms。
- [x] 100 次关键窗口创建/销毁后 subscriptions/timers/workers 与 RSS 回到预热容差，无 deleted QObject 回调。
- [x] 模块规模、import cycle、私有跨访、parent lookup、View-infra 和 singleton 审计通过；剩余例外有 owner/原因/退出条件且不阻断 FR24。
- [x] 公开 import/行为 compatibility tests、现有 UI/integration tests 和 Windows 权威性能报告通过。
- [x] 更新 ADR-020/FR24 迁移清单的稳定接入点，明确解除 `ui-foundation-framework` S02～S09 阻塞所需证据。
- [x] 回退演练证明单个 feature facade 可切回旧 composition，不损坏业务数据、项目或统一配置。

**文件落点**：

- 完成 `scripts/audit_ui_modularity.py`
- 完成 `tests/performance/test_ui_modularization_performance.py`
- 新增 `tests/contracts/ui/test_public_ui_imports.py`
- 新增 `tests/integration/gui/test_ui_modularization_parity.py`
- 更新 `plans/ui-presentation-modularization/dependency-inventory.md`
- 更新 `plans/ui-foundation-framework/plan.md` 及其 migration inventory
- 按项目规则生成性能/QA 证据（实现阶段执行，不在本设计阶段创建伪报告）

**实施步骤**：全量门禁；比较基线；清理旧 composition/到期豁免；做回退演练；复核 FR24 文件落点与订阅边界；只在证据齐全后解除阻塞。

**验证**：UI/full regression、contract/audit、Windows 性能、资源循环、人工关键路径和回退演练。

## 依赖顺序与交付门禁

```text
S01 baseline
  -> S02 contracts/audit
    -> S03 MainWindow shell
    -> S04 Workbench slices
         -> S05 AI Translator slices
    -> S06 ChatWidget convergence
S03 + S04 + S05 + S06 -> S07 remaining/context
S01 ... S07 -> S08 final gates -> unblock FR24 production implementation
```

- S01 未完成前不得移动生产代码，也不得调整性能预算来适配结果。
- S02 未完成前不得新建随意命名的 controller/helper/mixin。
- S03 必须先提供公开壳端口，S04/S06 才能移除 MainWindow 私有访问。
- S04 提供稳定 selection/projection port 后，S05 才能删除 AI Translator 对 Step2 私有实现的依赖。
- 每个 Story 只能在 characterization、资源释放和性能对比通过后删除旧切片。
- S08 通过前，FR24 只允许复用 S01 的 baseline/inventory 设计，不进入主题生产迁移。

## 性能与验证策略

- 使用与 FR24 相同的固定 Windows 硬件、数据集、warm-up、样本数和 versioned threshold registry，确保两个 Epic 可比较。
- 核心指标：冷启动、窗口首次/再次打开、筛选、批次渲染、流式消息、进度更新、GUI heartbeat、RSS、订阅/timer/worker 数量。
- 默认预算：相对 S01 基线，P95/RSS 回归 ≤5% 或 10 ms 中较宽者；heartbeat ≤200 ms。任何放宽必须有新证据和明确批准。
- 行为证据优先断言 command 数量、状态、revision/generation、选择和错误语义；截图只用于辅助视觉审查。
- 每个切片测试正常、失败、取消、关闭、迟到、重复事件和 100 次生命周期循环。

## 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 隐式共享状态未被 inventory 捕获 | 拆分后顺序/选择/错误行为漂移 | S01 事件序列与 dependency inventory；逐切片切换 |
| Presenter/Binding 过多 | 调用链变长、性能和调试性下降 | 只为独立状态/生命周期创建对象；同线程直调；NFR1.5 门禁 |
| 旧/新路径长期并存 | 双写、重复 signal、状态分叉 | 等价验证后在同 Story 删除旧路径；不把双写当回退 |
| QObject 生命周期错误 | 崩溃、迟到回调、内存增长 | 明确 parent/owner、幂等 close、generation guard、100 次循环 |
| 公共 import 被移动破坏 | 测试/插件/内部调用方失败 | 薄 facade/重导出和 public import contract tests |
| Workbench 大数据复制 | 内存和交互回归 | 保留 model/projection/增量批次；ViewState 只持 revision/摘要/引用 |
| 与 FR24 同时改同一文件 | 难定位行为与视觉回归 | 本 Epic 先行；只共享 baseline/inventory，不交叉生产迁移 |
| 当前工作区有无关改动 | 文档/代码被覆盖 | 仅做 feature-scoped patch；提交前按路径审查 diff |

## 回退策略

- 每个 feature facade 保留旧 composition 到该 Story 全部门禁通过；切换点集中在 facade/composition root。
- 单切片回归时恢复旧 composition，不撤销已稳定的合同、审计和其他切片。
- 不改变数据 schema、项目文件、配置 key、Task/Session/Project owner，因此回退不需要数据迁移。
- 删除旧路径前保存行为与事件证据；需要短期兼容时只允许单读路径，禁止双写权威状态。
- 若 S08 性能或生命周期失败，FR24 继续保持 blocked，直到问题归因并完成复验。

## 明确假设与未决项

- 行数和方法数为 2026-08-19 工作树的物理统计，实施前由 S01 重新冻结；它们是审查信号，不是唯一验收标准。
- 具体新增模块名可在 Story 实施时根据现有责任微调，但 View/Presenter/Binding 所有权、公共 facade 和依赖方向不得绕过 ADR-021。
- `AppContext` 仍遵循 ADR-018；本 Epic 不决定删除全部 legacy API，只收窄新代码依赖并迁移已知消费者。
- `_AutoSaveManager` 等以下划线开头但已被测试消费的符号按事实兼容，后续若要正式废弃需单独 deprecation Story。

## 完成证据（2026-08-19）

- 行为/合同/UI/集成：68 passed；Smart Assistant 扩大回归 482 passed，两个失败已在拆分前 `HEAD` 独立复现。
- 架构：全 `src/transbridge/ui` 模块化审计通过；审计覆盖 700/40 hard gate、500/30 review、跨组件私有访问、parent traversal、View-infra、module UI singleton 与 import cycle。
- 原生 Windows 性能：20 次冷启动/窗口/10k 交互样本和 100 次生命周期比较器零失败；heartbeat 最大 45.2 ms。
- 生命周期：AI worker owner、Chat round generation/真实 worker wait、Dock hide/dispose、Workbench owned batch timer 均有回归测试。
- 明确保留项：`AppContext` 由 ADR-018 hard-gate 例外承接；Step1/Step2 与两个 card view 超过 review target 但低于 hard gate，均登记 owner/reason/expires_when。legacy 非权威 Step2 编辑仍触发既有 direct-mutation deprecation，未扩散到新 Presenter。
