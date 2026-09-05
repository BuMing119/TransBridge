# Smart Assistant 工具失败恢复补齐

- **状态**：已完成（2026-09-05，S01～S04 QA 通过）
- **日期**：2026-09-05
- **对应需求**：[FR7.13.4、FR7.13.6.6、FR7.17](../../docs/requirements.md)
- **对应架构**：[ADR-009](../../docs/adr/009-agent-file-memory-reflexion.md)、[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-031](../../docs/adr/031-native-llm-function-calling.md)

## 目标

让 Smart Assistant 的 ReAct 与 Graph/Plan 工具调用使用同一套安全 Reflexion 重试语义：失败时同时获得原调用参数和结构化错误，允许只读、无需确认的工具在最多三次重试内由 LLM 修复参数；失败耗尽后仍闭合原生工具调用并继续外层 ReAct。

## 非目标

- 不为现有写入或管理工具开放盲目自动重试。
- 不重构业务工具、TaskRuntime 或 Provider 原生 Function Calling 协议。
- 不把参数复制到用户可见错误文本，也不新增第三方依赖。
- 不改变 LLM 请求自身失败时现有的手动重试交互。

## 当前事实与约束

- `ToolExecutionHandler` 创建无 client 的 `RetryHandler()`，因此参数分析总是立即返回 `None`。
- `GraphExecutor` 自行加载 LLM 配置，仅对异常重试；普通 `ToolResult(success=False)` 不进入 Reflexion。
- 两条路径都已能在最终结果后继续 SessionController/ReAct，且原生 `tool_call_id` 结果必须恰好闭环一次。
- 一次性确认令牌不能用于第二次副作用调用；默认只读且无需确认的工具才具备自动重试资格。
- 现有 `ToolResult.error_category/error_code/recovery_action/execution_meta` 是结构化失败与观测的兼容载体。

## Story 01：共享重试协调器与分析器注入

### 验收标准

- 共享协调器同时处理异常和 `ToolResult.fail()`，并返回最终结果、最终参数和准确尝试次数。
- 结构化权限、认证、配置和取消错误不重试；安全的瞬态错误可原参数重试；其他可恢复错误由 LLM 生成同一工具的 `adjusted_args`。
- 分析器通过 client provider 获取当前会话客户端；无 client、响应非法或分析请求失败时安全停止，不进行盲目参数重试。
- 传给分析模型的参数经过递归脱敏，原 step 不被原地修改。

### 文件落点

- 新增 `src/transbridge/smart_assistant/reflexion/retry_executor.py`：共享执行循环、结果归一化和尝试元数据。
- 修改 `src/transbridge/smart_assistant/reflexion/retry_handler.py`：结构化错误判断、client provider、非原地参数调整。
- 修改 `src/transbridge/smart_assistant/reflexion/__init__.py`：导出共享契约。

## Story 02：统一 ReAct 与 Graph/Plan 路径

### 验收标准

- `ToolExecutionHandler` 和 `GraphExecutor` 均委托共享协调器，不再各自维护不同的重试循环。
- 自动重试仅适用于 `permission="read"`、无需确认且 step 未禁用 retry 的工具；write/admin 每个工具调用最多执行一次。
- 每次重试触发既有用户状态与观测回调；最终 `ToolResult.execution_meta` 包含 `attempt` 和 `retry_count`。
- ReAct 失败结果仍写入关联 `tool_call_id` 并只触发一次外层继续；Plan 仍按既有依赖隔离和汇总逻辑进入下一轮。
- GUI 组合根把当前 Orchestrator LLM client provider 同时传给 ReAct 和 Plan。
- ReAct 的只读重试调用在后台 worker 执行，结果回到 Qt 主线程再更新会话与界面。

### 文件落点

- 修改 `src/transbridge/smart_assistant/tool_execution_handler.py`。
- 修改 `src/transbridge/smart_assistant/graph_executor.py` 与 `execution_engine.py`。
- 修改 `src/transbridge/smart_assistant/conversation_orchestrator.py`，提供只读 client port。
- 修改 `src/transbridge/ui/tools/smart_assistant/chat_composition.py` 与 `confirmation_view.py` 完成依赖注入。
- 新增 `src/transbridge/ui/tools/smart_assistant/react_execution_binding.py`，隔离后台调用与主线程收尾。

## Story 03：回归与安全验证

### 验收标准

- 覆盖参数修复后成功、`ToolResult.fail()` 重试、异常重试、无 client 停止、非重试错误停止和最大次数。
- 覆盖写入/确认工具不会重复副作用、失败后原生结果关联与 ReAct 继续回调。
- 覆盖 Graph/Plan 与 ReAct 使用相同协调器语义。
- 相关 pytest、Ruff check、Ruff format check 通过；若环境阻断，记录精确原因和替代验证。

### 文件落点

- 新增 `tests/smart_assistant/test_retry_executor.py`。
- 修改 `tests/smart_assistant/test_tool_execution_security.py`、`test_execution_engine.py` 和必要的组合/提示测试。

## Story 04：字段级、多项参数校验诊断

### 验收标准

- 参数校验结果为每个失败约束提供稳定的 `code`、JSON Pointer、schema keyword/schema pointer、期望值与实际
  JSON 类型；不复制实际参数值，避免错误载荷泄漏凭据或大段用户文本。
- 一次参数校验返回全部确定性排序的问题，而不是只保留首项；必填字段错误的 pointer 直接指向缺失字段。
- Guard 与 `validate_params` 装饰器生成一致的 `validation_issues[]`，并保留既有 `json_pointer`、错误码和自然语言
  `message`，兼容现有 UI、日志和调用方。
- Reflexion 继续通过结构化失败载荷获得上述问题，修正参数后仍由同一 schema 和安全护栏重新校验。

### 文件落点与实施步骤

- 修改 `src/transbridge/application/tools/schema.py`：扩展 `ArgumentValidationError` 契约、稳定约束代码与 JSON 类型，
  生成可序列化诊断；对 required pointer 做字段级定位。
- 修改 `src/transbridge/smart_assistant/guardrails/base.py` 与 `input_validator.py`：让护栏携带全部校验问题。
- 修改 `src/transbridge/smart_assistant/tools/base.py`：将问题投影到 `ToolResult.data.validation_issues`，统一护栏和装饰器路径。
- 更新 schema 契约、输入护栏、工具基类和 retry executor 测试，覆盖多字段错误、稳定字段、兼容投影与提示透传。

### 验证策略

- 先运行 schema、输入护栏、工具基类与重试协调器的聚焦测试。
- 再运行 `tests/smart_assistant` 回归，以及 Ruff check/format check。

## 依赖顺序与回退

Story 01 → Story 02 → Story 03 → Story 04。公共 `ToolSpec`、`ToolResult` 和 `ExecutionEngine.execute()` 保持兼容。
`ArgumentValidationError` 仅新增有默认兼容语义的只读字段；`ToolResult.data` 保留既有 `phase/json_pointer`。若共享协调器
出现回归，可让单个调用方暂时回退为单次执行，但不得回退失败结果闭环和 ReAct 续跑。

## 明确假设

- “默认最多 3 次重试”解释为首次调用之外最多再调用三次，总尝试数最多四次。
- 当前版本不为 write/admin 声明新的幂等或 reconcile 能力；后续工具需按 ADR-009/019 单独证明安全后再开放。

## 完成证据

- Story 01：共享协调器统一异常与 `ToolResult.fail()`，分析上下文包含当前参数、工具 schema 与结构化错误；敏感值
  递归脱敏并在调整结果中恢复为原进程内值。
- Story 02：ReAct 与 Graph/Plan 使用同一协调器；只读 ReAct 调用后台执行，write/admin 保持单次执行与既有确认链；
  重试耗尽仍闭合原生工具结果并继续外层 ReAct。
- Story 03：`tests/smart_assistant` 606 项通过；`ruff check src tests` 与 `ruff format --check src tests` 通过。
- Story 04：schema 校验现返回完整、确定性排序且不复制实际值的 `validation_issues[]`；Guard 与装饰器路径保持一致，
  Reflexion 提示透传稳定字段。QA 发现并修复未知敏感字段被无条件恢复、导致 `UNKNOWN_FIELD` 无法自愈的问题；
  schema 允许的既有凭据仍保持原值，schema 拒绝的字段可删除，模型新增的凭据会被丢弃。最终聚焦测试
  `21 passed`；Smart Assistant 与受影响契约测试 `635 passed, 1 skipped, 34 warnings`；全仓 Ruff check 与
  `1162 files` 格式检查通过。
