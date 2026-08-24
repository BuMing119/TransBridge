# Story-06：ChatWidget View 与 Binding 收敛

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P1
- **前置依赖**：S01、S02；S03 的 ToolWindows composition
- **下游**：S07、S08

## 目标

让 `ChatWidget` 回归聊天 View composition，把消息/流式呈现、Session/Task 订阅和确认交互拆开，并在证据充分后删除历史新旧并行控制路径。

## 原始验收标准

- [x] `ChatWidget` 公开路径和 Panel/SessionList 现有装配保持兼容，主体只保留 layout 与 composition。
- [x] message list、streaming、session binding、task binding、confirmation view 各自有明确 owner 和幂等 close。
- [x] SessionController → ConversationOrchestrator → ToolExecutionHandler 继续作为后端权威路径，UI 不维护第二状态机。
- [x] 用事件计数/序列证明新旧并行控制路径等价后删除旧路径；没有双写、重复 bubble、重复终态或重复保存。
- [x] streaming buffer/flush/Markdown generation 在高频 token 下有界，并能丢弃关闭/切会话后的迟到事件。
- [x] 100 次打开/关闭/切会话后订阅、timer、task binding 和 RSS 回到预热容差。

## 当前职责与目标归属

- bubble/card/list/scroll/widget limit/clear/load history → `MessageListView`。
- `_do_streaming_flush()`、thinking/observation/Markdown update、retry presentation → `StreamingPresenter`。
- session manager、save/load/auto-save/auto-name、panel lookup → `SessionBinding`，Panel 通过显式 port 注入。
- task manager/monitor callbacks、completion/failure/token stats → `TaskBinding`；移除 UI polling，优先消费既有事件/projection。
- plan/tool/batch/HITL dialogs 与 intents → `ConfirmationView`；执行仍交给 `ToolExecutionHandler`/controller。
- `ChatWidget`：输入区域、layout、公开 facade 方法和以上对象的 composition/close。

## 事件身份与顺序

- `ConversationEpoch(session_id, turn_id, generation)`：所有 stream/parsed/tool/result 事件带或映射到当前 epoch。
- `StreamingPresenter` 持有单个有界 buffer/flush timer；切会话/close 递增 generation 并清空 UI-owned buffer。
- `SessionBinding`、`TaskBinding` 各自聚合 Subscription；`close()` 幂等，不关闭 application owner。

```text
send intent -> SessionController -> Orchestrator -> ToolExecutionHandler
events(epoch) -> bindings/presenter -> MessageListView
confirm intent -> controller/tool handler (once)
session switch/close -> detach + generation advance -> late events ignored
```

## 实施步骤

1. 在 S01 trace 中同时记录现有 controller path、compat callbacks、bubble/terminal/save 次数，识别真实权威读路径。
2. 抽 MessageListView；保持 widget limit、scroll-to-bottom 和 history order，ChatWidget facade 方法先委托它。
3. 抽 StreamingPresenter，统一 flush timer、bubble target 与 generation；高频 token 不逐 token 重排完整 Markdown。
4. 抽 SessionBinding；由 Panel 注入 SessionPort，删除 `_find_panel()` 和 ChatWidget 对 session ownership 的猜测。
5. 抽 TaskBinding；用事件/projection 替换 `_start_task_monitor_polling()`，确认没有功能缺口后删除常驻 polling。
6. 抽 ConfirmationView；plan/tool/HITL 只发 intent，后端决定执行与终态。
7. 切到唯一 controller chain；通过 trace 证明事件等价后删除 forwarding/parallel state，收敛 ChatWidget 为 composition。

## 边界与错误

- 快速切 session、clear、retry、close 时，旧 epoch 不能添加 bubble、改状态、自动保存或自动命名新 session。
- tool/task 终态重复到达必须幂等，不能重复 card/notification/save。
- Session 保存失败保持可重试诊断，不把内存消息伪装成已持久化。
- Streaming Markdown 异常回退为安全纯文本/既有错误表现，不丢后端终态。
- QSettings auto_mode 的配置统一属于 FR24/S07 兼容面，本 Story先保持 key 与语义，避免混入偏好迁移。

## 文件变更

- 修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`、`panel.py`
- 新增 `message_list_view.py`、`streaming_presenter.py`、`session_binding.py`、`task_binding.py`、`confirmation_view.py`
- 新增/修改 `tests/ui/tools/smart_assistant/test_*.py`

## 测试与建议命令

- `pytest tests -k "chat_widget or smart_assistant or session_controller or task_monitor" -q`
- 高频 token、切会话/clear/retry/close、重复终态、plan/tool/HITL confirm/cancel、save/load/auto-name。
- 100 次打开关闭切换，断言 timer/subscription/polling/RSS 与 deleted QObject 回调。

## 风险与回退

历史双路径是首要风险。先旁路记录、后单点切换、最后删除旧路径；不得长期双写。MessageList/Streaming/Session/Task 可分别通过 ChatWidget facade 回退，但任一时刻只允许一组 owner 连接事件。

## 未决问题

- 若 TaskMonitor 当前唯一信息源确实是 polling，需先在既有 TaskRuntime 增加公开事件/projection adapter；这属于本 Story 必要适配，不授权重写 TaskRuntime。
