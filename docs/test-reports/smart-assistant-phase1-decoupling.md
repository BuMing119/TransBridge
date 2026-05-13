# Smart Assistant Phase 1 QObject 解耦 — 测试报告

**日期**: 2026-05-13
**对应方案**: `plans/smart-assistant-qa-fix/plan.md` (Story-02) + `docs/council-review-stack-overflow-decoupling.md`
**审查方式**: 4 维度并行 (功能 / 安全 / 代码质量 / 回归风险)

## 测试覆盖

| 维度 | 检查项 | 通过 | 问题数 |
|------|--------|------|--------|
| 功能测试 | 15 | 15/15 | 1 Minor |
| 安全审查 | 9 | 5/9 | 1 Blocker → 已修复, 3 Major, 2 Minor |
| 代码质量 | 10 | 6/10 | 4 Minor |
| 回归风险 | 9 | 6/9 | 1 Blocker → 已修复, 1 Major, 1 Minor |

## 发现的问题

### Blocker (2 → 已全部修复)

- [x] **B-001: 跨线程 Qt GUI 操作** (安全 + 回归 同时发现)
  - **描述**: `tool_translator.py` worker 线程调用 `TaskManager.notify_completed()` 时，回调 `_on_task_completed` 在 worker 线程同步执行，直接操作 QWidget/QLayout → UB/crash
  - **根因**: 去除 `pyqtSignal` 后丧失了 Qt 自动的跨线程信号槽排队机制
  - **修复**: `TaskManager.notify_completed/notify_failed` 使用 `QMetaObject.invokeMethod(app, lambda, Qt.QueuedConnection)` 将回调投递到主线程。新增 `_safe_callback` 静态方法统一异常隔离。改动: `task_manager.py` (+15 行)

### Major (4 — 预存问题，建议后续修复)

- [ ] **M-001: panel.py closeEvent 中 4 处静默异常** (安全) — `except:pass` 仍存在于观测收集器清理、信号断开、回调注销、TaskManager 重置路径
- [ ] **M-002: Micro-stage 消息覆盖竞态** (安全) — `_round_messages` 可被快速连续发送覆盖，导致 Stage C 读取错误消息
- [ ] **M-003: 护栏降级缺少 UI 通知** (安全) — `_ensure_middlewares` 降级到默认链时仅 logger.warning，用户无感知
- [ ] **M-004: tool_proofreader.py 缺少任务完成通知** (回归) — 后处理任务完成后无 `notify_completed` 调用，UI 无反馈

### Minor (8)

- [ ] **m-001**: TaskManager 回调方法未持锁 (功能)
- [ ] **m-002**: 日志可能包含用户对话内容 (安全)
- [ ] **m-003**: FAISS 索引写入无原子保护 (安全)
- [ ] **m-004**: `on_completed` 命名歧义 (代码质量)
- [ ] **m-005**: 回调参数缺少 Callable 类型注解 (代码质量)
- [ ] **m-006**: Worker 清理逻辑重复 (代码质量)
- [ ] **m-007**: `del` 删除实例属性不常规 (代码质量)
- [ ] **m-008**: `stop()` 返回值语义丢失 (回归)

## 审查结论

- **方案一致性**: ✅ Phase 1 改动按委员会纪要执行，4 项目标全部完成
- **代码质量**: ✅ 风格一致，无死代码，API 向后兼容
- **安全性**: ⚠️ Blocker 已修复 (跨线程投递)，4 Major 为预存问题
- **回归风险**: ⚠️ Blocker 已修复，预存 proofreader 通知缺失

## 签名

**QA 审查**: ⚠️ 条件通过 — Blocker 已修复，4 Major + 8 Minor 为已知限制，不阻塞合入。
建议在后续 Story 中处理 Major 问题，Minor 可在日常维护中逐步修复。
