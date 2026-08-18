# 后台任务监控面板

**对应需求**: FR14 — 后台任务监控面板
**技术模块**: frontend
**业务域**: smart-assistant
**状态**: 已实现
**创建日期**: 2026-08-05

## 功能边界

### 范围内
- 新建 `TaskMonitorWidget`：可折叠任务列表 + 任务卡片
- 任务卡片：类型名称 + 彩色状态标签 + 进度条 + 详细信息 + 运行时长 + 操作按钮
- Panel 集成：ChatWidget 下方追加 TaskMonitorWidget，垂直分割
- 实时刷新：TaskManager.on_finished 回调 + 1s QTimer 定时轮询
- 任务清理：单任务清除 + 一键清除已完成
- 无任务时显示"无后台任务"
- TaskManager 未初始化时降级提示

### 范围外
- 任务历史持久化（仅内存中保留当前会话的任务）
- 任务详情展开/执行日志查看
- 桌面通知
- 任务优先级排序
- 跨会话任务保留

## Story 清单

### Story 01: TaskMonitorWidget 核心组件
**验收标准**:
- [ ] `TaskMonitorWidget(QWidget)` 新建，位于 `ui/tools/smart_assistant/task_monitor.py`
- [ ] 可折叠结构：标题栏（显示任务计数）+ 折叠/展开按钮 + 任务卡片列表
- [ ] 任务卡片（`_TaskCard(QFrame)`）：圆形状态指示灯 + 任务名称 + 状态标签（彩色）+ 进度条（QProgressBar）+ 详细信息文本 + 运行时长 + 操作按钮
- [ ] 状态标签颜色：running=#4CAF50, completed=#2196F3, failed=#D32F2F, cancelled=#9E9E9E, paused=#FF9800
- [ ] 进度条：仅 running/paused 状态显示，从 progress dict 读取 current/total
- [ ] 运行时长：从 created_at 计算，格式化为"X分Y秒"或"X秒"
- [ ] 操作按钮：running→暂停+取消, paused→恢复+取消, 其他→无操作按钮
- [ ] 清除按钮：completed/failed/cancelled 任务显示清除按钮
- [ ] "清除已完成"按钮：一键移除所有非活跃任务
- [ ] 空状态占位："无后台任务"灰色文本
- [ ] `refresh(tasks: list[dict])` 方法：全量刷新任务列表
- [ ] `update_task(task_id, status/progress)` 方法：单任务增量更新
- [ ] 零 PyQt6 新依赖，使用现有 QWidget/QFrame/QProgressBar/QPushButton/QLabel

> 详细实现指南见 `plans/task-monitor/stories/story-01-task-monitor-widget.md`（由 `/bm-story` 展开后生成）

### Story 02: Panel 集成与刷新机制
**验收标准**:
- [ ] `SmartAssistantPanel` 布局改为垂直 QSplitter：上半 ChatWidget + 下半 TaskMonitorWidget（默认比例 7:3）
- [ ] Panel 中创建 TaskMonitorWidget 实例，传入 TaskManager 引用
- [ ] QTimer 定时器（1s 间隔）调用 `TaskManager.list_all()` → `refresh()`
- [ ] ChatWidget._ensure_task_manager() 中注册 `on_finished` 回调 → 立即刷新 TaskMonitorWidget
- [ ] 任务操作按钮点击 → 调用 `TaskManager.cancel/pause/resume(task_id)` → 立即刷新
- [ ] 关闭面板时停止定时器
- [ ] 会话切换时清除任务显示（`reset()` 方法）
- [ ] 426+ 现有测试零回归

> 详细实现指南见 `plans/task-monitor/stories/story-02-panel-integration.md`（由 `/bm-story` 展开后生成）

## 架构依赖
- **ADR-008**: 代码分层 — TaskMonitorWidget 在 UI 层，通过 TaskManager 回调 + 定时器与后端通信
- **TaskManager API**: `list_all()`, `get_status(task_id)`, `cancel(task_id)`, `pause(task_id)`, `resume(task_id)`, `on_finished(callback)`, `cleanup(task_id)`
- **现有组件**: `SmartAssistantPanel`（panel.py）, `ChatWidget._ensure_task_manager()`（chat_widget.py）

## 风险与回退方案
- **TaskManager 未初始化**: 降级显示"任务监控不可用"，不崩溃
- **大量任务导致 UI 卡顿**: 限制最多显示 20 个任务卡片，超出时滚动
- **定时器内存泄漏**: Panel.closeEvent 中停止定时器，会话切换时重置
- **回退方案**: 删除 TaskMonitorWidget，移除 panel.py 中的 QSplitter 和集成代码，恢复原布局

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 2 Story UI 交付；当前 Monitor/TaskManager 状态并非统一 JobSnapshot 的只读投影。
- `blocked_by`：`unified-task-translation-runtime-v2` S01/S02/S07、`release-hardening-v2` S02/S03。
- `superseded_by`：可写 TaskManager 状态、全局 reset 和按钮推断由 TaskRuntime capability/JobSnapshot 取代；现有 Widget 作为 adapter 保留。
