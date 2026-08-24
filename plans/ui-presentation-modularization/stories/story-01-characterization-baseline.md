# Story-01：冻结 UI 行为、性能、依赖与生命周期基线

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P0
- **前置依赖**：无
- **下游**：S02～S08；也是 FR24 可复用的共享基线

## 目标

在移动任何生产职责前，把上帝 UI 当前真实行为、调用顺序、资源所有权和性能冻结成可比较证据，避免重构时把隐式合同或回归一起搬走。

## 原始验收标准

- [x] 为 MainWindow、Step1/Step2、AI Translator、ChatWidget 和主要 operation cards 记录用户 intent → command/use case/worker → 可见状态的序列。
- [x] 覆盖成功、校验失败、业务失败、取消、关闭、窗口销毁后迟到回调，以及重复点击/重复终态幂等。
- [x] 建立固定数据集与固定窗口的冷启动、窗口打开、关键交互、GUI heartbeat、RSS、订阅/timer/worker 数量基线。
- [x] 生成依赖清单，标出跨组件私有访问、parent lookup、View 直连 infra/persistence、并行控制路径和公共 import 消费者。
- [x] 基线测试不锁定计划删除的私有方法，不以整屏像素截图作为唯一合同。

## 当前入口与观察面

- Main：`MainWindow.closeEvent()`、解析/迁移 `_run_*`、`_op_run_worker()`、项目/Variant/Snapshot 命令和工具窗口入口。
- Workbench：`Step1Widget._start_parse()`；`Step2PreviewWidget.refresh()`、`_append_table_batch()`、`_apply_all_filters()`、编辑/标签/定位；`UploadCard`/`WriteCard`/`DownloadCard`。
- AI：`AITranslatorWindow.open_for_translation()`、`_on_start()`、`_on_mixed_start()`、`_on_polish_start()` 与结果提交/报告。
- Chat：`send_user_message()`、`_on_send()`、stream flush、plan/confirm/task callbacks、session save/load 与 `shutdown()`。
- 公共兼容：测试和调用方对 `MainWindow`、`_AutoSaveManager`、`Step2PreviewWidget` 等直接 import。

## 证据模型

每个场景记录 `ScenarioTrace`（计划新增测试 DTO，而非生产合同）：`intent`、`commands[]`、`visible_state`、`projection_revision`、`render_generation/run_id`、`terminal_state`、`errors[]`、`resource_counts`。测试替身只挂在公开 use-case/worker/projection 边界，不断言私有 helper 被调用。

事件顺序：

```text
user intent -> validation -> exactly one command/worker start
            -> projection/task event -> GUI-thread binding
            -> visible state/terminal message
close/destroy -> detach UI subscriptions -> late event ignored
```

## 实施步骤

1. 在 `dependency-inventory.md` 建立模块尺寸、公开消费者、跨私有访问、parent lookup、状态 owner、signal/timer/worker 表。
2. 为五组核心 UI 建 fixture；使用小数据集验证语义、10k+ entries/高频 token 验证负载。
3. 冻结正常、失败、取消、重复 intent/终态、close/destroy 后迟到事件；记录 command 次数和公开控件状态。
4. 扩展 `benchmark_cases.py`，固定机器信息、Qt/style、数据集、warm-up、样本数和 P95/RSS 采集。
5. 采集冷启动、首次/热窗口打开、筛选/批次渲染、进度、streaming、heartbeat 与 100 次生命周期循环。
6. 将无法稳定测量或依赖外部服务的场景标为模拟层/Windows 权威层，不伪造通过结果。

## 边界与错误

- 网络、LLM 和磁盘写入使用边界替身；仍要验证调用参数、次数和错误分类。
- 截图允许记录关键窗口结构，但不得因抗锯齿/平台差异取代语义断言。
- 当前已有泄漏/重复调用应作为 baseline defect 记录并阻塞对应 Story，不能把它写成目标行为。
- timer/worker 计数要区分 application runtime 所有与当前 View 所有；关闭 UI 不应要求全局任务消失。

## 文件变更

- 新增 `plans/ui-presentation-modularization/dependency-inventory.md`
- 新增 `tests/ui/characterization/test_{main_window,workbench,ai_translator,chat_widget}_contract.py`
- 修改 `tests/performance/benchmark_cases.py`
- 新增 `tests/performance/test_ui_modularization_performance.py`

## 测试与建议命令

- `pytest tests/ui/characterization -q`
- `pytest tests/performance/test_ui_modularization_performance.py -q`
- Windows 权威性能命令沿用仓库现有 performance marker/registry 约定，实施时从 `tests/performance` 配置读取，不在文档硬编码新协议。

## 风险与回退

本 Story 仅新增测试和清单；若 fixture 影响全局 Qt 状态，按 scenario 隔离 QApplication/QSettings/temp config，并撤回不稳定断言。性能阈值只能由可复现实测确认。

## 未决问题

- 具体 Windows 权威机型和 RSS warm tolerance 由实施时现有 registry 与 S01 实测确定。
- 若 `_AutoSaveManager` 还有仓外消费者，只能通过兼容 facade 保护，不能凭仓内搜索直接删除。
