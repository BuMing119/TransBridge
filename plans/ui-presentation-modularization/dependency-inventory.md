# FR25 UI 依赖、行为与资源基线

- **状态**：S01 基线已冻结；S08 终验已完成
- **采集日期**：2026-08-19
- **对应 Plan**：[UI 展示层模块化与上帝窗口拆分](plan.md)
- **用途**：记录迁移前事实；数值变化必须由 Story 的迁移证据解释，不能把本清单当成目标架构。

## 模块规模基线

| 模块 | 物理行数 | 主要类方法数 | 迁移 Story |
|---|---:|---:|---|
| `ui/main_window.py` | 2140 | 80 | S03 |
| `ui/tools/ai_translator/ai_translator_window.py` | 1617 | 42 | S05 |
| `ui/tools/smart_assistant/chat_widget.py` | 1250 | 68 | S06 |
| `ui/workbench/step2.py` | 1151 | 49 | S04 |
| `ui/workbench/step1.py` | 931 | 25 | S04 |
| `ui/workbench/cards/upload_card.py` | 827 | 30 | S04 |
| `ui/workbench/cards/write_card.py` | 814 | 26 | S04 |
| `ui/context.py` | 624 | 68 | S07 |
| `ui/paratranz/string_detail_dialog.py` | 607 | 25 | S07 |
| `ui/workbench/cards/download_card.py` | 538 | 22 | S04/S07 复核 |
| `ui/tools/ai_translator/_translation_progress_window.py` | 517 | 22 | S05/S07 复核 |

物理行数使用 `Get-Content` 的行数；类方法数使用 AST 直接成员统计。生成代码和纯声明资源未计入。

## 稳定入口与事实兼容面

| 入口 | 已知消费者/证据 | 迁移规则 |
|---|---|---|
| `transbridge.ui.main_window.MainWindow` | `tests/ui/test_background_gui_operations.py`、`ui/app.py` | 原路径保留为 shell/facade |
| `transbridge.ui.main_window._AutoSaveManager` | background GUI tests | 原路径重导出；正式废弃另立 Story |
| `transbridge.ui.workbench.step2.Step2PreviewWidget` | incremental rendering tests、Workbench、AI Translator | 原路径保留为 composition facade |
| `AITranslatorWindow.open_for_translation` | Workbench tool entry | 参数和取消/失败返回语义保留 |
| `ChatWidget.shutdown/set_input/send_user_message` | Smart Assistant Panel/Session UI | facade 保留，内部委托新切片 |
| `AppContext` projection/legacy properties | MainWindow、Workbench、tools、integration tests | 遵守 ADR-018；先迁新消费者，再收窄兼容面 |

## 权威状态与资源 owner

| 状态/资源 | 当前权威 owner | UI 责任 |
|---|---|---|
| Project/Variant/entries/labels | application projection + commands；legacy 模式由 AppContext facade 兼容 | 渲染 projection、提交一次 command |
| 长任务/终态 | TaskRuntime、现有 worker owner | 显示进度、取消 intent、解绑迟到事件 |
| Smart Assistant conversation/tool flow | SessionController → ConversationOrchestrator → ToolExecutionHandler | message/confirmation/session/task presentation |
| Step2 大表 | collection/projection + render generation | 增量批次、stable entry ID、编辑安全 |
| QWidget | Qt parent | feature facade 在销毁时关闭 Presenter/Binding |
| subscription/timer | 当前创建者分散持有 | S02 后由 Binding/Subscription 显式聚合 |

## 已知隐式依赖与承接 Story

| 违规/风险 | 当前事实 | 承接 |
|---|---|---|
| MainWindow 跨私有访问 | Workbench `_step2`、`_project_bar`、worker 字段等 | S03 先建立 shell ports；S04 迁消费者 |
| AI → Step2 私有实现 | import `_ALL_CATEGORIES`、`_entry_category`、`_COL_KEY`，并持有具体 Step2 | S04 公布 selection/projection contract；S05 删除依赖 |
| AI parent lookup | `_find_main_window()` import/扫描 MainWindow | S05 用 navigate/locate/report ports |
| Chat → Panel lookup | `_find_panel()` 与 compatibility callbacks | S06 显式注入 SessionPort |
| Chat task polling | `_task_monitor_timer` 周期刷新 | S06 迁公开 Task event/projection；无事件前不得盲删 |
| Chat 新旧路径并存 | 注释和 callback 显示 SessionController compatibility 并行 | S06 用事件序列证明后删除旧路径 |
| View 直连业务/infra | Step1/cards/MainWindow/AI 窗口直接构建 worker/client/use case | S03～S05 迁 coordinator/adapter |
| AppContext 广域依赖 | QObject signals、projection、legacy slot、helper 混合 | S07 收窄新依赖，保留 ADR-018 facade |

## 行为场景基线

| ID | 用户 intent | 必须保持的可观察合同 |
|---|---|---|
| MW-01 | 启动可见磁盘任务 | 调用立即返回；Workbench disabled/progress visible；结果后恢复；一次 completion |
| MW-02 | 自动/手动保存 | 自动保存静默且不禁用 Workbench；手动保存显示进度；dirty/完成语义不变 |
| MW-03 | 关闭运行中的窗口 | GUI 线程不 `wait/join`；先异步收尾，迟到回调不访问 deleted QObject |
| WB-01 | refresh 1200+ entries | 首批立即可见，后续自动批次完成；filtered count 保持总数 |
| WB-02 | locate 未渲染 entry | 渲染到目标后选择正确 stable ID，不以旧 row index 污染新 generation |
| WB-03 | 编辑触发同步 projection rebuild | 正式 collection 与可见 cell 一致，不访问已删除 item，不重启全量 render |
| WB-04 | filter state round-trip | category/stage/label/search/focus 的公开状态可保存恢复，不重复业务 mutation |
| AI-01 | 无 collection 打开翻译 | 显示一次警告并返回 `None`，不创建 worker/window |
| AI-02 | translate/mixed/polish | 每次 start 只建立一个 run；preview/report 与正式提交分离；旧 run 迟到结果忽略 |
| CHAT-01 | send/stream/terminal | 单个用户 bubble、单个 assistant stream target、单个终态/保存 |
| CHAT-02 | confirm/plan/task | intent 最多提交一次；重复终态不重复 card/message/save |
| CHAT-03 | switch/clear/close | 旧 session/turn 事件不进入新 View；shutdown 幂等并停止 UI-owned timer/subscription |

## 性能测量协议

- 固定 `QT_QPA_PLATFORM=offscreen` 的开发边界探针；最终以 Windows 可见 GUI 固定硬件证据为准。
- 与 `tests/performance/benchmark_cases.py` 共用 `HardwareTier`、warm-up、repetitions、P95 和 threshold registry。
- FR25 相对门禁：冷启动、代表性窗口打开和主要交互回归不超过 `max(基线×5%, 10 ms)`；heartbeat ≤200 ms。
- 生命周期：代表性 facade/Binding 连续创建销毁 100 次，UI-owned subscriptions/timers/workers 回到 warm baseline；RSS 容差由 Windows 权威样本确定。
- 不使用单次计时作为通过证据；开发机测试只验证采集链和明显边界违规。

## 临时豁免基线

S02 审计上线时，上述未迁模块允许以本清单作为基线存在，但禁止新增同类违规。每个 hard-gate 模块必须由表中 Story 关闭；S08 不接受无 `owner/reason/expires_when` 的残留豁免。

## S08 终验快照

| 模块 | 迁移前（行/方法） | 终验（行/方法） | 结果 |
|---|---:|---:|---|
| `ui/main_window.py` | 2140/80 | 473/13 | shell + coordinators；公共 composition ports |
| `ai_translator_window.py` | 1617/42 | 449/30 | View/Presenter/Run/Result；worker owner 与 run guard |
| `smart_assistant/chat_widget.py` | 1250/68 | 451/30 | composition + session/task/stream bindings |
| `workbench/step2.py` | 1151/49 | 601/40 | filter/table/label/progress slices；登记 review 例外 |
| `string_detail_dialog.py` | 607/25 | 497/22 | navigation + lifecycle owner |
| `ui/context.py` | 624/68 | 608/68 | ADR-018 compatibility facade；登记 hard-gate 例外 |

清理结果：AI→Step2 私有 import、Main/Panel parent lookup、Main→Workbench 私有字段、Chat 常驻任务轮询和无 owner 的批次 singleShot 均已移除。全 UI 审计零未登记 finding；`--include-exempt` 只报告带 owner/reason/expires_when 的 Workbench review 项与 `AppContext`。

原生 Windows Qt 性能样本（相同机器、相同 runner、拆分前 `HEAD` 对当前工作树）：冷启动 P95 814.1→826.4 ms，窗口打开 44.95→45.24 ms，10k 完整交互 601.1→619.6 ms，heartbeat 最大 45.2 ms；100 次生命周期无存活 Python wrapper，RSS 增量 39.0→40.7 MB。比较器按耗时 `max(5%, 10 ms)`、heartbeat 200 ms 和 RSS 独立容差判定，结果无失败。
