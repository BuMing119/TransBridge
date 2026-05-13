# 评审委员会讨论纪要 — ExecutionEngine QObject 保留评估

**日期**: 2026-05-13
**评审对象**: `src/transbridge/smart_assistant/execution_engine.py`
**参与角色**: 架构师 / 开发者
**触发**: 用户询问 ExecutionEngine 为何保留 QObject，Phase 1-2 后是否仍正常工作

## 各角色独立意见

### 架构师
- **总体评价**: 保留 QObject 是合理的工程权衡。ExecutionEngine 是统一执行引擎——DAG 拓扑排序 + BFS 层级并行 + 护栏中间件 + Reflexion 自纠错 + HITL 确认 + Checkpoint 断点续跑。6 个信号中 5 个可替代，`step_requires_confirmation` 的跨线程 RPC 模式是保留 QObject 的核心原因。
- **发现**: `step_requires_confirmation.emit()` → `Condition.wait()` 模式本质是跨线程同步等待，emit 必须投递到主线程弹窗。去 QObject 需自建消息泵（~150-200 行），引入异步时序问题和回调生命周期管理风险。
- **结论**: 不改，收益为负。

### 开发者
- **总体评价**: Phase 1-2 后完全正常工作。`step_requires_confirmation` 的跨线程 RPC 是保留 QObject 的最强理由——如果 emit 改成直接回调，`QMessageBox.question` 会在 worker 线程崩溃。
- **发现**: 5 路信号连接到 ChatWidget/ObsCollector，消费者均未被 Phase 1-2 破坏。`progress` 信号无消费者（死代码）。`_safe_serialize` 的 QObject 检查是防御性保留，无开销。
- **建议**: 删除 `progress` 信号或补上 UI 连接。

## 共识汇总

- [x] **ExecutionEngine 保留 QObject 是正确的** — 架构师 + 开发者一致
- [x] **Phase 1-2 无兼容性问题** — TaskManager/ExecutionContext/护栏链均为纯 Python，在 ExecutionEngine 中正常工作
- [x] **`progress` 信号是死代码** — emit 了但无人 connect，可删除或补 UI

## 综合建议

### 低优先级
- [ ] 删除 `progress` pyqtSignal 声明（L28）及 emit 调用（L329），或为 Step2 进度条补上连接
- [ ] `_run_single` L180 的 `TaskManager()` 冗余调用可缓存为局部变量

## 纪要不构成决议

本文件仅为各角色独立意见的客观汇总，不强制要求采纳任何建议。
