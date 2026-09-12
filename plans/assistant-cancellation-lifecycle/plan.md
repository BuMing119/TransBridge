# 助手取消与生命周期快照闭环

- 实施状态：已完成；用户授权的本地实现与相关离线验证于 2026-09-12 完成。
- 依据：本次取消链路复现；ADR-008；unified-task-translation-runtime-v2 S02；project-session-persistence-v2。
- 范围：助手控制器、确认卡片、后台事件归属、取消工具、生命周期快照与 GUI 接线。
- 非目标：翻译项目版本回滚、恢复已取消的外部请求、通用多请求调度器。

## 修复前问题与约束

发送消息会重置控制器但后台任务可能继续；旧结束事件先写入当前历史才检查归属；旧卡片没有失效；等待确认快照缺少可执行确认载荷。TaskRuntime 已有取消信号与提交屏障，复用其权威终态。

## 实施顺序与验收

1. 控制器与恢复：区分轮次中断和会话解绑，迟到事件幂等处理；快照保存任务身份，恢复前核对实际任务，缺少可执行确认数据时降级为空闲并记录原因。
   - 文件：session_controller.py、application/sessions/models.py、recovery.py；控制器和恢复测试。
2. 确认失效：新输入、清空、切换、恢复和关闭时使旧卡片失效；排队中的人工决策不能作用于替换后的引擎。
   - 文件：confirmation_view.py 及卡片组件；离屏 Qt 回归。
3. 取消与事件：取消范围限定调用方会话；缺少目标且多任务时拒绝猜测；全部取消须明确选择；重复取消幂等；取消终态单独展示，旧事件不污染新轮次或重新启动推理。
   - 文件：task_binding.py、任务控制工具及其独立模块、task_manager.py；范围/终态/重复回调测试。
   - 计划执行注册父任务并关联子任务，纯同步计划也能查询、取消和保存生命周期；省略目标优先选择唯一顶层任务。
4. 接线与持久化：统一程序调用和输入框入口；使排队的新轮次随切换/关闭失效；捕获一致会话 scope；保存任务引用与控制器快照，恢复核对运行时；补集成测试。
   - 文件：chat_widget.py、chat_composition.py、session_binding.py、独立生命周期接线模块、会话保存 facade。
5. QA：聚焦回归后运行助手、Session 与 TaskRuntime 相关测试；Ruff check/format；核对现有用户改动未被覆盖。

## 关键语义

- 新消息中断旧模型轮次，不等同于后台任务已取消。
- 明确单任务停止由控制工具执行；先返回停止请求已接受，任务实际退出后才报告已取消。
- 取消前已提交数据保留，迟到候选由 TaskRuntime 提交屏障拒绝。
- 生命周期快照恢复不执行历史工具、不恢复失效确认、不把枚举恢复当作运行恢复。
- 旧格式字段向后兼容，无法核对的状态显式降级；没有数据迁移。
- stop_task 的旧“省略 ID 即全部操作”行为由明确 all_tasks 参数替代；根需求 FR9.3.8 同步更新。

## 阶段记录

- [x] 分析与复现
- [x] 实施边界与并行文件所有权
- [x] 开发与集成
- [x] 验证与差异审查

## 实现结果

三个子 Agent 分别完成控制器与恢复、确认与计划执行、任务控制与事件归属，主会话完成 GUI 接线、持久化与集成验证。

- 轮次中断保留后台任务关联，会话解绑清除当前控制器关联；新输入、会话切换与关闭使排队输入和旧确认失效。
- 计划注册带会话归属的父任务；纯同步计划和带后台子任务的计划均可查询和取消，父任务取消传递给执行器及子任务。
- 结束事件先验证会话、任务和运行身份，再更新历史和控制器；取消不会触发模型自动续跑，重复或迟到事件幂等收敛。
- 任务启动、取消请求和实际终态保存生命周期；恢复核对运行时任务，缺失任务、终态任务及无法重建的确认等待回到空闲并保留原因。
- 修复执行器入口清除取消信号的竞态，取消发生在工作线程启动前也不会重新执行步骤。

## QA 证据

2026-09-12 执行：

```text
uv run pytest tests/smart_assistant tests/ui/tools/smart_assistant tests/application/sessions tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/integration/bootstrap/test_task_runtime_wiring.py -q -m "not llm"
799 passed, 34 warnings in 22.56s

uv run ruff check src tests
All checks passed!

uv run ruff format --check src tests
1253 files already formatted

git diff --check
通过，无差异空白错误
```

回归包含真实面板的手工确认后立即持久化、同会话恢复、跨会话伪造引用拒绝、换题后取消同步计划、启动前取消、旧确认失效及重复终态事件。模型回复使用受控替身，Qt 使用离屏测试。警告为已有 SWIG 和旧领域接口弃用提示；未执行真实 LLM/API 联机验证或仓库全部测试。保留既有 AI 项目术语相关工作区变更；未提交或推送。

## 兼容性与执行边界

- 快照对象是助手生命周期状态及任务引用，不是翻译项目内容快照；不增加项目版本回滚。
- 旧快照字段兼容读取，缺少可核对运行身份时安全降级，不自动重放历史操作。
- stop_task 省略 ID 时只选择当前范围内唯一顶层任务；原先依赖隐式全部取消的调用须改为显式 all_tasks=True。
- 正在阻塞的同步步骤使用协作式取消，在步骤返回前保持 cancelling；不会声称远端请求已被强制终止。后续步骤禁止执行。
- 已取消的 GraphExecutor 保持取消状态；主动重新执行须创建新执行器。

## 职责规模复核

计划 UI 执行职责从 confirmation_view.py 抽出为 plan_execution_binding.py，运行时父任务另置 plan_task_runtime.py；任务控制从 tool_translator.py 抽出为 task_control.py。ChatWidget 超过 500 行/30 方法的复核阈值，本次将输入调度及运行时核对分别放入 SubmissionBinding、SessionRuntimeBinding，仅保留门面接线。既有 GraphExecutor 超过 700 行，本次只修正已有上下文传递和取消语义，没有加入新职责；继续保留其规模债务，后续扩展图调度能力前须拆分执行调度与步骤运行职责，本次以图取消回归约束风险。
