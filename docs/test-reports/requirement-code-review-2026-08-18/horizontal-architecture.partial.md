# 横向架构审查（阶段性结果）

- Agent：`architecture_iteration_review`
- 状态：为切换到“每需求一个 Agent”的纵向编排而中断；后续会独立续跑并产出正式文件。
- 本文件保存中断前已经形成的架构证据，最终综合前不向纵向 Agent 注入。

## 已确认的架构趋势

1. 同一业务能力在 GUI、Agent、MCP、FOMOD 中存在多套入口和不同调用契约，出现“内核存在但入口不可用”的系统性漂移。
2. 缺少正式 Application Service 层；UI、工具和流水线直接构造 Parser、Writer、Translator、PostProcessor 与 API client。
3. `MainWindow`、AI Translator Window、ChatWidget、Workbench Step、AutoTranslator、PostProcessor 仍是大型多职责对象，既包含 UI 又包含业务编排和状态修改。
4. 任务执行同时使用 QThread、threading.Thread、专用 Worker、TaskManager 与 MCP thread；取消、暂停、进度、错误和任务终态不统一。
5. 领域状态可被多个模块直接改写，Stage、标签、版本和来源 provenance 缺中央不变量。

## 初步目标架构

```text
GUI / Agent / MCP / CLI / FOMOD adapters
                |
        Application Use Cases
                |
  Domain Policies + Immutable Job/State Contracts
                |
 Parser/Writer/LLM/ParaTranz/TM Infrastructure Ports
```

建议采用渐进式 strangler：先用服务包装现实现，优先迁移已断裂的 Agent/MCP，再迁 FOMOD，最后逐步替换 GUI 直连；不做一次性大重写。

## 预期 ADR 方向（待横向正式复核）

- 新增 Application Service / Ports & Adapters / composition root ADR。
- 更新后台任务 ADR：纯 Python JobRuntime + Qt bridge。
- 更新 Smart Assistant ADR：工具不得拥有业务控制器，只能适配 Use Case。
- 更新 MCP 生命周期、安全与 SecretStore 决策。
- 更新 FOMOD/TM ADR：typed staged pipeline、事务式发布与明确冲突策略。
- 更新 Entry/Stage ADR：统一 mutation/provenance/variant overlay。
- 增加 package/import/release layout 决策。

