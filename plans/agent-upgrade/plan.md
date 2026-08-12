# Agent 框架全面升级 (Phase 1)

**对应需求**: FR7.13
**技术模块**: smart_assistant + infra
**业务域**: AI 辅助翻译
**状态**: ✔️ 已实现（Phase 1 + Phase 2 全部完成）
**创建日期**: 2026-05-10
**更新日期**: 2026-05-10（追加 Phase 2）

## 功能边界

### 范围内 (Phase 1 — 已实现)
- infra/ 共享基础设施提取（LLMClient + EmbeddingClient + LLMConfig + VectorStore）
- Skill 系统（用户自定义 TOML Skill + 热加载 + 触发匹配）
- 文件上传与知识注入（Excel/CSV/Markdown/TXT/JSON/PDF/Word/ParaTranz）
- 长期记忆（FAISS 向量存储 + JSON 元数据 + 两阶段召回）
- Reflexion 自纠错（工具失败→LLM分析→重试 max 3 次）

### 范围内 (Phase 2 — 待实现，分三批)

**第一批（P0 — 核心能力）**:
- 多 Agent 协作（AgentSpec/AgentRegistry/Orchestrator/AgentWorker + ToolRegistry namespace + 多项目并行）
- 安全护栏（GuardMiddleware 中间件链 + 权限三级 + 输入输出校验 + 确认信号）

**第二批（P1 — 基础设施增强）**:
- 自研 Graph 编排引擎（GraphExecutor ABC + StatefulDAGExecutor + 4 种 Node 类型 + Checkpoint + HITL）
- 可观测性（ObservabilityCollector + 对话追踪 + 工具调用链 + Token 统计 + 观测面板）

**第三批（P2 — 可选扩展）**:
- MCP Server（stdio JSON-RPC + ToolSpec→MCP Tool 映射 + 安全约束）

### 范围外
- LangGraph / LangChain 及任何 asyncio 框架（ADR-005 已拒绝）
- 分布式 Agent（多机协作）
- MCP Client 端（仅做 Server 端）
- HTTP/SSE 传输（MCP 仅 stdio）
- OpenTelemetry 集成

## Story 清单

### Story-01: infra/ 共享基础设施提取与搬迁

**Phase**: 1 | **预估**: 3h | **状态**: 📝
**对应需求**: FR7.13 (基础依赖) | **架构引用**: ADR-008, ADR-010
**详细文档**: `plans/agent-upgrade/stories/story-01-infra-extraction.md`

**验收标准**:
- [ ] `src/transbridge/infra/__init__.py` 导出 5 个公开符号
- [ ] `llm_client.py` 从 ai_translator/ 搬迁到 infra/，内容不变
- [ ] `embedding_client.py` 从 ai_translator/ 搬迁到 infra/，内容不变
- [ ] `LLMConfig` 类从 paratranz/config_manager.py 提取到 infra/config.py
- [ ] `vector_store.py` 新建，包含 VectorStore 类（create_index/add/search/save/load/remove）
- [ ] 9 个受影响文件的 import 全部按 ADR-010 契约更新
- [ ] `python -c "from src.transbridge.infra import LLMClient, create_llm_client, EmbeddingClient, LLMConfig, VectorStore"` 无 ImportError

**实现步骤**:
1. 创建 `infra/` 包 + `__init__.py` → `src/transbridge/infra/__init__.py` (新建)
2. 搬迁 `llm_client.py` + `embedding_client.py` → `src/transbridge/infra/` (git mv)
3. 提取 `LLMConfig` 到 `config.py` → `src/transbridge/infra/config.py` (新建), `paratranz/config_manager.py` (改)
4. 新建 `vector_store.py` (VectorStore 类) → `src/transbridge/infra/vector_store.py` (新建)
5. 更新 ai_translator/ 内部 import (translator/term_database/prompt_builder/post_processor) → 4 文件 (改)
6. 更新 smart_assistant/ 内部 import (chat_widget/chat_worker/tool_registry) → 3 文件 (改)
7. 更新 paratranz/config_manager.py 内部引用 → 1 文件 (改)
8. 验证全链路 import

### Story-02: Skill 系统

**Phase**: 2 | **预估**: 2.5h | **状态**: 📝
**详细文档**: `plans/agent-upgrade/stories/story-02-skill-system.md`
**对应需求**: FR7.13.1 | **架构引用**: ADR-005, ADR-008

**验收标准**:
- [ ] `SkillLoader` 可解析 TOML 格式 Skill 定义文件
- [ ] `SkillRegistry` 可按关键词/工具匹配 Skill
- [ ] `SkillExecutor` 可执行 Skill（注入 system prompt + 调度工具）
- [ ] `data/skills/` 目录创建，含 1 个预置示例 Skill
- [ ] 快捷指令面板新增「Skill」按钮，列出可用 Skill
- [ ] 用户点击 Skill → 填入输入框 → agent 按 Skill 流程执行

**实现步骤**:
1. 创建 `SkillSpec` 数据类 + `SkillLoader`（TOML 解析） → `smart_assistant/skills/skill_loader.py` (新建)
2. 创建 `SkillRegistry`（注册 + 关键词/工具匹配） → `smart_assistant/skills/skill_registry.py` (新建)
3. 创建 `SkillExecutor`（system prompt 注入 + 工具调度） → `smart_assistant/skills/skill_executor.py` (新建)
4. 创建子包 `__init__.py` → `smart_assistant/skills/__init__.py` (新建)
5. 创建预置 Skill 示例 → `data/skills/translate_with_terms.toml` (新建)
6. UI 集成：快捷指令面板新增 Skill 按钮 → `quick_actions.py` (改), `chat_widget.py` (改)

### Story-03: 文件上传与知识注入

**Phase**: 3 | **预估**: 3h | **状态**: 📝
**详细文档**: `plans/agent-upgrade/stories/story-03-file-upload.md`
**对应需求**: FR7.13.2 | **架构引用**: ADR-009

**验收标准**:
- [ ] `FileParser` ABC + `ParsedDocument` 数据类定义
- [ ] `TextFileParser` 支持 .xlsx/.csv/.md/.txt/.json
- [ ] `BinaryFileParser` 支持 .pdf/.docx
- [ ] `ParatranzParser` 支持 ParaTranz 导出格式
- [ ] 上传 UI：拖拽区 + 文件选择按钮，显示已上传文件列表
- [ ] 上传后文件解析为 ParsedDocument，注入 agent 上下文
- [ ] agent 翻译时自动引用已上传的纠错表/术语参考

**实现步骤**:
1. 创建 `FileParser` ABC + `ParsedDocument` → `smart_assistant/file_parser/base.py` (新建)
2. 创建 `TextFileParser` → `smart_assistant/file_parser/text_parser.py` (新建)
3. 创建 `BinaryFileParser` → `smart_assistant/file_parser/binary_parser.py` (新建)
4. 创建 `ParatranzParser` → `smart_assistant/file_parser/paratranz_parser.py` (新建)
5. 创建子包 `__init__.py` → `smart_assistant/file_parser/__init__.py` (新建)
6. UI：文件上传区域（拖拽 + 按钮 + 文件列表） → `chat_widget.py` (改) 或新建组件
7. ContextBuilder 扩展：注入已上传文件内容 → `context_builder.py` (改)

### Story-04: 长期记忆

**Phase**: 4 | **预估**: 3h | **状态**: 📝
**对应需求**: FR7.13.3 | **架构引用**: ADR-009, ADR-010
**详细文档**: `plans/agent-upgrade/stories/story-04-long-term-memory.md`

**验收标准**:
- [ ] `MemoryStore` 支持 add/search/get/delete/list_by_type
- [ ] `MemoryRetriever` 实现两阶段召回（精确匹配 → 语义检索）
- [ ] 对话结束时自动记录翻译上下文记忆
- [ ] 新对话开始时自动检索相关记忆并注入 system prompt
- [ ] 记忆存储在项目目录下（`data/projects/{project}/{variant}/memory/`）
- [ ] 项目切换时记忆自动隔离

**实现步骤**:
1. 创建 `MemoryEntry` 数据类 + `MemoryStore` → `smart_assistant/memory/memory_store.py` (新建)
2. 创建 `Embedding` 嵌入生成（复用 infra/EmbeddingClient） → `smart_assistant/memory/embedding.py` (新建)
3. 创建 `MemoryRetriever` → `smart_assistant/memory/memory_retriever.py` (新建)
4. 创建子包 `__init__.py` → `smart_assistant/memory/__init__.py` (新建)
5. ChatWidget 集成：`_on_send` 前检索 + `_on_llm_finished` 后记录 → `chat_widget.py` (改)
6. ConversationManager 扩展：注入记忆上下文 → `conversation_manager.py` (改)

### Story-05: Reflexion 自纠错

**Phase**: 5 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.13.4 | **架构引用**: ADR-009
**详细文档**: `plans/agent-upgrade/stories/story-05-reflexion-retry.md`
**对应需求**: FR7.13.4 | **架构引用**: ADR-009

**验收标准**:
- [ ] `RetryHandler` 类实现（LLM 分析失败原因 + 参数调整 + 重试，max 3 次）
- [ ] `ExecutionEngine._run_single()` 注入重试包裹
- [ ] 重试过程对用户可见：ToolCard 显示 "重试中 (n/3)…"
- [ ] 3 次全失败后优雅降级，不阻塞后续步骤
- [ ] 非工具错误（如网络超时）不触发 Reflexion（直接报错）

**实现步骤**:
1. 创建 `RetryHandler` → `smart_assistant/reflexion/retry_handler.py` (新建)
2. 创建子包 `__init__.py` → `smart_assistant/reflexion/__init__.py` (新建)
3. ExecutionEngine 注入：`_run_single` 包裹 → `execution_engine.py` (改)
4. UI 状态更新：ToolCard 显示重试进度 → `chat_widget.py` (改), `tool_card.py` (改)

---

## Phase 2 Story 清单

### Story-06: 多 Agent 基础设施

**Phase**: P0 第一批 | **预估**: 2.5h | **状态**: 📝
**对应需求**: FR7.13.6 | **架构引用**: ADR-008（agents/ 子包更新）
**详细文档**: `plans/agent-upgrade/stories/story-06-agent-infrastructure.md`

**验收标准**:
- [ ] `AgentSpec` 数据类（agent_id/name/role/namespace/tools/skills/system_prompt/enabled）
- [ ] `AgentInstance` 数据类（instance_id/agent_spec/project_path/ctx），支持同类型多实例
- [ ] `AgentRegistry` 类（register/get/list_all/list_enabled/enable/disable）
- [ ] `ToolRegistry` namespace 扩展：`register(name, spec, namespace)` / `get(name, namespace)` / `list_namespace(ns)` / `list_all_namespaces()`
- [ ] `namespace=None` 时返回全部工具（编排 Agent 特权）
- [ ] `agents/__init__.py` 导出 5 个公开符号
- [ ] 3 个预置 Agent 在启动时自动注册：translator（namespace="translator"）、proofreader（namespace="proofreader"）、orchestrator（namespace=None，全工具可见）
- [ ] 现有 ToolRegistry.register() 调用方保持兼容（namespace 默认 "default"）

**实现步骤**:
1. 创建 `AgentSpec` + `AgentInstance` 数据类 → `smart_assistant/agents/agent_spec.py` (新建)
2. 创建 `AgentRegistry` → `smart_assistant/agents/agent_registry.py` (新建)
3. 创建子包 `__init__.py` → `smart_assistant/agents/__init__.py` (新建)
4. ToolRegistry 添加 namespace 参数 → `smart_assistant/tool_registry.py` (改)
5. 启动时注册 3 个预置 Agent → 应用启动入口或 ChatWidget 初始化 (改)
6. 更新 smart_assistant/__init__.py 导出 → `smart_assistant/__init__.py` (改)

### Story-07: Agent 编排与并行执行

**Phase**: P0 第一批 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.13.6.2~FR7.13.6.6 | **架构引用**: ADR-008（Orchestrator + AgentWorker）
**详细文档**: `plans/agent-upgrade/stories/story-07-agent-orchestration.md`

**验收标准**:
- [ ] `Orchestrator` 类：decompose_task（LLM 分解用户请求→子任务列表）→ map_to_steps（子任务→ExecutionEngine step dict，含 agent 字段）→ summarize_results（汇总 StepResult 列表→用户可读摘要）
- [ ] `AgentWorker(QThread)` 类：接收 step + AgentInstance，在独立线程中执行工具调用，信号 `progress(str)`, `finished(StepResult)`, `error(str)`
- [ ] 同一类型 Agent 可创建多个 AgentWorker 实例（每个绑定不同项目）
- [ ] 多个 AgentWorker 通过 ExecutionEngine 的 ThreadPoolExecutor 并行调度
- [ ] 单个 Agent 失败不阻断其他 Agent（错误隔离）
- [ ] ChatWidget UI：Agent 状态指示器（空闲/执行中/完成/失败）

**实现步骤**:
1. 创建 `Orchestrator` → `smart_assistant/agents/orchestrator.py` (新建)
2. 创建 `AgentWorker(QThread)` → `smart_assistant/agents/agent_worker.py` (新建)
3. ChatWidget 集成：Agent 选择 + 状态显示 → `chat_widget.py` (改)
4. ExecutionEngine 适配：step dict 支持 `agent` 字段，按 agent 路由到对应 AgentWorker → `execution_engine.py` (改)

### Story-08: 安全护栏中间件

**Phase**: P1 第二批 | **预估**: 2.5h | **状态**: 📝
**对应需求**: FR7.13.8 | **架构引用**: ADR-012（中间件链注入模式）
**详细文档**: `plans/agent-upgrade/stories/story-08-safety-guardrails.md`

**验收标准**:
- [ ] `GuardMiddleware` ABC（before_execute/after_execute）+ `GuardResult` 数据类（allowed/reason/modified_args/modified_result）
- [ ] `PermissionGuard`：从 ToolSpec.permission 读取权限级别（read/write/admin），admin 拒绝执行并触发确认，write 可配置需确认
- [ ] `InputValidationGuard`：类型检查 + 字符串长度限制（100KB）+ 注入模式检测（SQL/XSS/命令注入特征）
- [ ] `OutputValidationGuard`：类型检查 + 大小截断 + API key 等敏感信息脱敏
- [ ] `ToolSpec` 扩展：新增 `permission: str = "read"`、`require_confirmation: bool = False`、`max_output_size: int = 102400` 字段
- [ ] ExecutionEngine 注入 `_middlewares: list[GuardMiddleware]`，在 `_run_single()` 中构建 before→retry→after 中间件链
- [ ] `step_requires_confirmation` 信号：node_id + prompt + choices，UI 弹窗确认后通过 `provide_decision(node_id, choice)` 返回结果
- [ ] 护栏配置：`[guardrails]` INI section（enable_admin_confirm/enable_input_validation/enable_output_validation/max_input_size）
- [ ] 护栏日志：所有拒绝和校验失败记录到日志（时间/工具/规则/摘要），在可观测性面板中展示
- [ ] 所有现有工具 ToolSpec 标注 permission 字段（向后兼容：未标注默认 "read"）

**实现步骤**:
1. 创建 `GuardMiddleware` ABC + `GuardResult` → `smart_assistant/guardrails/base.py` (新建)
2. 创建 `PermissionGuard` → `smart_assistant/guardrails/permission.py` (新建)
3. 创建 `InputValidationGuard` → `smart_assistant/guardrails/input_validator.py` (新建)
4. 创建 `OutputValidationGuard` → `smart_assistant/guardrails/output_validator.py` (新建)
5. 创建子包 `__init__.py` → `smart_assistant/guardrails/__init__.py` (新建)
6. ToolSpec 添加 permission 字段 → `smart_assistant/tool_registry.py` (改)
7. ExecutionEngine 注入中间件链 + 确认信号 → `execution_engine.py` (改)
8. ChatWidget 连接确认信号 + UI 弹窗 → `chat_widget.py` (改)
9. 现有工具标注 permission → `smart_assistant/tool_registry.py` (改，同步骤6)

### Story-09: Graph 引擎核心

**Phase**: P1 第二批 | **预估**: 3h | **状态**: 📝
**对应需求**: FR7.13.7.1~FR7.13.7.3 | **架构引用**: ADR-011（自研 Graph 引擎）
**详细文档**: `plans/agent-upgrade/stories/story-09-graph-engine-core.md`

**验收标准**:
- [ ] `GraphExecutor` ABC：`execute_graph(graph: GraphSpec) → list[StepResult]` / `cancel()` / `pause()` / `resume()`
- [ ] `GraphSpec` 数据类（graph_id/nodes/edges/entry_node）
- [ ] `NodeSpec` 基类 + 4 种子类：`ActionNode`（tool+args+agent+retry）、`ConditionNode`（condition+true_node+false_node）、`LoopNode`（sub_nodes+max_iterations+exit_condition）、`HumanConfirmNode`（prompt+choices+timeout+default_choice）
- [ ] `EdgeSpec`：from/to/type（"always"/"conditional"/"loop_back"）
- [ ] `StatefulDAGExecutor(GraphExecutor)`：BFS 遍历 + 同层 ThreadPoolExecutor 并行 + 条件路由 + 循环控制
- [ ] 循环支持嵌套条件分支；不支持嵌套循环（Phase 2 限制）
- [ ] 条件表达式引擎：基于 `StepResult` 字段（success/message/data）评估，如 `result.data['score'] < 0.7`
- [ ] `execute()` 接口向后兼容：内部将 steps 转为线性 GraphSpec 委托给 `execute_graph()`
- [ ] ExecutionEngine 保留为 StatefulDAGExecutor 的别名（`ExecutionEngine = StatefulDAGExecutor`）
- [ ] PyQt6 信号保留现有 5 个，新增 `node_paused(node_id, prompt, choices)` / `node_resumed(node_id)`

**实现步骤**:
1. 创建 `GraphExecutor` ABC → `smart_assistant/graph_executor.py` (新建)
2. 创建 Graph 类型体系（GraphSpec/NodeSpec/ActionNode/ConditionNode/LoopNode/HumanConfirmNode/EdgeSpec）→ `smart_assistant/graph_types.py` (新建)
3. StatefulDAGExecutor 实现（BFS+并行+条件+循环+信号）→ `execution_engine.py` (重写核心循环，保留现有 _topological_levels/_run_single)
4. 条件表达式评估引擎 → `execution_engine.py` (改，同文件)
5. `execute()` 兼容适配（steps→GraphSpec 转换）→ `execution_engine.py` (改，同文件)

### Story-10: Checkpoint 与人机协同

**Phase**: P1 第二批 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.13.7.4~FR7.13.7.6 | **架构引用**: ADR-011（Checkpoint + HITL）
**详细文档**: `plans/agent-upgrade/stories/story-10-checkpoint-hitl.md`

**验收标准**:
- [ ] `Checkpoint` 数据类（graph_id/current_node_id/completed_results/graph_state/timestamp）
- [ ] `save_checkpoint() → Checkpoint`：每层执行完成后自动保存到 `data/projects/{project}/{variant}/checkpoints/{graph_id}_{timestamp}.json`
- [ ] `load_checkpoint(ckpt) → None` / `resume_from_checkpoint(ckpt) → list[StepResult]`：从 checkpoint 恢复，跳过已完成节点
- [ ] `StepResult.data` 序列化前校验：仅允许 dict/list/str/int/float/bool/None，不可序列化对象跳过 + 写警告日志
- [ ] `HumanConfirmNode` 执行流程：暂停 → 发 `node_paused` 信号 → 后台线程 QEventLoop local loop 等待 → UI 用户确认 → `provide_decision()` → 退出 QEventLoop → 继续
- [ ] 确认超时兜底：配置的 timeout_seconds 超时后自动采用 default_choice
- [ ] LoopNode 循环控制：每轮迭代后评估 exit_condition → 满足则跳出 → 不满足且未达 max_iterations 则继续
- [ ] 异常中断（取消/崩溃）→ 保留最近 checkpoint，下次调用 `execute_graph()` 时询问用户是否从断点恢复

**实现步骤**:
1. 创建 Checkpoint 数据类 + 序列化/反序列化 → `smart_assistant/graph_types.py` (改，追加到 S09 的类型文件)
2. StatefulDAGExecutor 添加 save/load/resume checkpoint 逻辑 → `execution_engine.py` (改)
3. HumanConfirmNode + QEventLoop 暂停/恢复实现 → `execution_engine.py` (改)
4. ChatWidget 连接 node_paused/node_resumed 信号 + 确认弹窗 UI → `chat_widget.py` (改)

### Story-11: 可观测性系统

**Phase**: P2 第三批 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.13.9 | **架构引用**: ADR-012（pyqtSignal 遥测管道）
**详细文档**: `plans/agent-upgrade/stories/story-11-observability.md`

**验收标准**:
- [ ] `ObservabilityCollector` 类：监听 pyqtSignal（step_started/step_finished/step_retrying），聚合数据
- [ ] `ConversationTrace` 数据类：conv_id/rounds/tools_called/token_stats
- [ ] `ReActRound` 数据类：round_num/llm_input_tokens/llm_output_summary/tools/duration_ms
- [ ] `ToolCallRecord` 数据类：timestamp/tool_name/input_summary(截断500字符)/output_summary(截断500字符)/duration_ms/success/retry_count
- [ ] `TokenStats` 数据类：input_tokens/output_tokens/by_model
- [ ] 对话结束时自动保存观测数据 JSON 到 `data/projects/{project}/{variant}/observability/{conv_id}.json`
- [ ] 每轮 ReAct 结束后在消息底部显示 token 消耗摘要
- [ ] 会话级别 token 统计在智能助手状态栏持久显示
- [ ] 观测面板：「观测」Tab → Token 仪表盘（今日/本周/本月）+ 工具调用列表（可展开）+ 对话轮次时间线
- [ ] 历史数据保留 30 天，过期自动清理

**实现步骤**:
1. 创建观测数据模型（ConversationTrace/ReActRound/ToolCallRecord/TokenStats）→ `smart_assistant/observability/models.py` (新建)
2. 创建 `ObservabilityCollector` → `smart_assistant/observability/collector.py` (新建)
3. 创建子包 `__init__.py` → `smart_assistant/observability/__init__.py` (新建)
4. ExecutionEngine 集成：初始化时创建 Collector，连接信号 → `execution_engine.py` (改)
5. ChatWidget 集成：观测面板 Tab + Token 摘要 + 状态栏 → `chat_widget.py` (改)

### Story-12: MCP Server

**Phase**: P2 第三批 | **预估**: 2h | **状态**: 📝
**对应需求**: FR7.13.5 | **架构引用**: ADR-012（MCP stdio + ToolSpec 映射）
**详细文档**: `plans/agent-upgrade/stories/story-12-mcp-server.md`

**验收标准**:
- [ ] `MCPServer` 类：`run_stdio()` 从 stdin 读取 JSON-RPC 请求，处理后写入 stdout
- [ ] `MCPAdapter` 类：ToolSpec → MCP Tool 定义 JSON Schema 转换（name/description/inputSchema）
- [ ] `tools/list` 方法：返回 ToolRegistry 中所有非 admin 工具的列表
- [ ] `tools/call` 方法：接收 tool_name + arguments → ToolSpec.execute() → 包装返回 MCP 格式
- [ ] admin 级工具默认不暴露；通过 `[mcp]` INI 白名单 (`admin_tool_whitelist`) 控制
- [ ] write 级工具在 MCP 通道中可配置策略：allow/deny（默认 deny，因 MCP 无 UI 确认机制）
- [ ] `[mcp]` INI section：enabled/transport/admin_tool_whitelist/write_tool_policy
- [ ] MCP Server 启用时在应用启动日志中输出监听信息

**实现步骤**:
1. 创建 `MCPAdapter`（ToolSpec→MCP Tool 映射 + JSON Schema 生成）→ `smart_assistant/mcp/adapter.py` (新建)
2. 创建 `MCPServer`（stdio JSON-RPC 主循环）→ `smart_assistant/mcp/server.py` (新建)
3. 创建子包 `__init__.py` → `smart_assistant/mcp/__init__.py` (新建)
4. INI 配置扩展：`[mcp]` section → `paratranz/config_manager.py` (改)
5. 启动入口集成：读取配置，enabled=true 时启动 MCPServer 线程 → 应用入口 (改)

---

## Phase 1 新建文件清单

```
src/transbridge/infra/                      # Story-01
├── __init__.py
├── llm_client.py                           (搬迁自 ai_translator/)
├── embedding_client.py                     (搬迁自 ai_translator/)
├── config.py                               (LLMConfig 从 paratranz/ 提取)
└── vector_store.py                         (新建)

src/transbridge/smart_assistant/skills/      # Story-02
├── __init__.py
├── skill_loader.py
├── skill_registry.py
└── skill_executor.py

src/transbridge/smart_assistant/file_parser/ # Story-03
├── __init__.py
├── base.py
├── text_parser.py
├── binary_parser.py
└── paratranz_parser.py

src/transbridge/smart_assistant/memory/      # Story-04
├── __init__.py
├── memory_store.py
├── embedding.py
└── memory_retriever.py

src/transbridge/smart_assistant/reflexion/   # Story-05
├── __init__.py
└── retry_handler.py

data/skills/                                 # Story-02
└── translate_with_terms.toml               (预置示例)
```

## Phase 2 新建文件清单

```
src/transbridge/smart_assistant/agents/      # Story-06/07
├── __init__.py
├── agent_spec.py                            (AgentSpec + AgentInstance)
├── agent_registry.py                        (AgentRegistry)
├── orchestrator.py                          (Orchestrator)
└── agent_worker.py                          (AgentWorker QThread)

src/transbridge/smart_assistant/guardrails/  # Story-08
├── __init__.py
├── base.py                                  (GuardMiddleware ABC + GuardResult)
├── permission.py                            (PermissionGuard)
├── input_validator.py                       (InputValidationGuard)
└── output_validator.py                      (OutputValidationGuard)

src/transbridge/smart_assistant/             # Story-09/10
├── graph_executor.py                        (GraphExecutor ABC)
└── graph_types.py                           (GraphSpec/NodeSpec/Checkpoint + 4子类 + EdgeSpec)

src/transbridge/smart_assistant/observability/ # Story-11
├── __init__.py
├── models.py                                (ConversationTrace/ToolCallRecord/TokenStats/ReActRound)
└── collector.py                             (ObservabilityCollector)

src/transbridge/smart_assistant/mcp/         # Story-12
├── __init__.py
├── server.py                                (MCPServer stdio)
└── adapter.py                               (MCPAdapter ToolSpec→MCP)
```

## Phase 1 需修改的现有文件

| 文件 | Story | 修改内容 |
|------|-------|---------|
| `ai_translator/translator.py` | S01 | llm_client import → infra |
| `ai_translator/term_database.py` | S01 | embedding_client import → infra |
| `ai_translator/prompt_builder.py` | S01 | llm_client import → infra |
| `ai_translator/post_processor/post_processor.py` | S01 | llm_client import → infra |
| `paratranz/config_manager.py` | S01 | 提取 LLMConfig 到 infra/，import infra |
| `smart_assistant/chat_widget.py` | S01/S02/S03/S04/S05 | S01: create_llm_client→infra / S02: Skill按钮 / S03: 文件上传UI / S04: 记忆集成 / S05: 重试状态 |
| `smart_assistant/chat_worker.py` | S01 | LLMClient 类型引用 → infra |
| `smart_assistant/tool_registry.py` | S01 | LLMConfig → infra |
| `smart_assistant/context_builder.py` | S03 | 注入已上传文件内容 |
| `smart_assistant/conversation_manager.py` | S04 | 注入记忆上下文 |
| `smart_assistant/execution_engine.py` | S05 | _run_single 注入重试包裹 |
| `smart_assistant/quick_actions.py` | S02 | 新增 Skill 按钮 |
| `smart_assistant/tool_card.py` | S05 | 重试进度显示 |

## Phase 2 需修改的现有文件

| 文件 | Story | 修改内容 |
|------|-------|---------|
| `smart_assistant/tool_registry.py` | S06/S08 | S06: namespace 参数扩展（register/get/list_namespace）+ S08: ToolSpec 添加 permission/require_confirmation/max_output_size |
| `smart_assistant/__init__.py` | S06 | 新增 agents 子包导出 |
| `smart_assistant/execution_engine.py` | S07/S08/S09/S10/S11 | S07: step 支持 agent 字段 + AgentWorker 调度 / S08: 中间件链注入 + 确认信号 / S09: StatefulDAGExecutor 实现 / S10: Checkpoint + HITL / S11: ObservabilityCollector 集成 |
| `smart_assistant/chat_widget.py` | S07/S08/S10/S11 | S07: Agent 选择+状态 / S08: 确认弹窗 / S10: HITL 交互 / S11: 观测面板 |
| `paratranz/config_manager.py` | S08/S12 | S08: [guardrails] INI section / S12: [mcp] INI section |
| 应用启动入口 | S06/S12 | S06: 预置 Agent 注册 / S12: MCPServer 启动 |
| `smart_assistant/graph_types.py` | S10 | S10: Checkpoint 数据类追加 |

## 架构依赖

### Phase 1
- [ADR-005: TOML Prompt 模板 + Skill 定义格式](../../docs/adr/005-toml-prompt-no-langchain.md)
- [ADR-008: SmartAssistant 代码分层 + infra/ + 4子包](../../docs/adr/008-smart-assistant-code-layering.md)
- [ADR-009: 文件解析、长期记忆与 Reflexion](../../docs/adr/009-agent-file-memory-reflexion.md)
- [ADR-010: 共享基础设施提取 — infra/ 包](../../docs/adr/010-infra-extraction.md)

### Phase 2
- [ADR-008 更新: agents/ 子包 + ToolRegistry namespace](../../docs/adr/008-smart-assistant-code-layering.md)（2026-05-10 更新节）
- [ADR-011: 自研有状态图编排引擎](../../docs/adr/011-graph-orchestration-engine.md)
- [ADR-012: 安全护栏、可观测性与 MCP Server](../../docs/adr/012-safety-observability-mcp.md)

## 风险与回退方案

### Phase 1 风险
| 风险 | 影响 | 回退方案 |
|------|------|---------|
| infra/ 搬迁后 import 遗漏导致 ImportError | 应用无法启动 | 全项目 Grep 验证 + import 测试链 |
| PDF/Word 解析质量不稳定 | 文件上传功能部分不可用 | 提供文本格式 fallback，提示用户转换 |
| FAISS 索引随记忆增长性能下降 | 检索变慢 | 定期重建索引 + 记忆数量上限（默认 1000 条） |
| Reflexion 重试增加 token 消耗 | 成本上升 | 最大 3 次重试 + 仅工具错误触发 |
| Skill TOML 格式错误 | Skill 加载失败 | 跳过失败 Skill + toast 提示，不阻断其他 Skill |

### Phase 2 风险
| 风险 | 影响 | 回退方案 |
|------|------|---------|
| ToolRegistry namespace 扩展破坏现有调用方 | 工具调用全部失败 | namespace 默认 "default" 向后兼容 + 全量回归测试 |
| 中间件链增加工具调用延迟 | 响应变慢 | 中间件链可配置启用/禁用 + 校验逻辑轻量化 |
| Graph 循环控制死循环 | 无限占用 LLM token | max_iterations 硬上限兜底 + 执行超时 kill |
| Checkpoint 序列化含不可序列化 Qt 对象 | 运行时崩溃 | data 字段协议约束 + 序列化前类型校验 + 跳过不可序列化对象 |
| HITL 确认弹窗阻塞 | 用户离开无法操作 | 可配置超时 + 默认策略兜底（continue/skip/abort） |
| 观测数据 JSON 积累过大 | 磁盘占用 | 30 天自动清理 + 单个文件上限 10MB |
| MCP Server stdio 读写阻塞 | 应用卡死 | 独立线程运行，不阻塞主线程 + 缓冲区大小限制 |
