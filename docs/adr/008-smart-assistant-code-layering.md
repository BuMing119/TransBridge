# ADR-008: SmartAssistant 代码分层 — UI 与业务逻辑分离

- **状态**: 已接受
- **日期**: 2026-05-10
- **决策者**: BuMing
- **对应需求**: [FR7.12](../requirements.md)

## Context

智能助手 (SmartAssistant) 当前全部 13 个文件位于 `src/transbridge/ui/tools/smart_assistant/` 下，业务逻辑组件（ConversationManager、ChatWorker、ExecutionEngine、ToolRegistry、ContextBuilder、Prompts）与 UI 组件（Panel、ChatWidget、MessageBubble、QuickActions、ToolCard、PlanCard）混在一起。

项目已有成熟的分层惯例：`ai_translator/`、`converter/`、`parser/`、`writer/`、`persistence/` 作为独立业务逻辑包与 `ui/` 平级。smart_assistant 的后端组件应遵循相同惯例。

## Decision

### 1. 新建后端包 `src/transbridge/smart_assistant/`

与 `ai_translator/`、`converter/`、`parser/`、`writer/`、`persistence/` 平级。

```
src/transbridge/smart_assistant/        # NEW: 业务逻辑
├── __init__.py                         # 公开 API 导出
├── conversation_manager.py             # 多轮对话管理
├── chat_worker.py                      # LLM 流式调用 QThread
├── execution_engine.py                 # DAG 执行引擎 (含 StepResult)
├── tool_registry.py                    # ToolSpec + ToolRegistry + v1 工具
├── context_builder.py                  # 上下文构建器
└── prompts.py                          # System Prompt 模板

src/transbridge/ui/tools/smart_assistant/ # 保留: 仅 UI
├── __init__.py                         # 仍导出 SmartAssistantPanel
├── panel.py
├── chat_widget.py                      # 跨包引用 backend
├── message_bubble.py
├── quick_actions.py
├── tool_card.py
└── plan_card.py                        # 跨包引用 backend
```

### 2. Import 规范

- **后端包内**：使用相对导入（如 `prompts.py` 中 `from .tool_registry import ToolRegistry`）
- **UI → 后端**：使用绝对导入（如 `chat_widget.py` 中 `from src.transbridge.smart_assistant.execution_engine import ExecutionEngine, StepResult`）
- **外部 → UI**：`main_window.py` 的 `from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel` 保持不变

### 3. 后端 `__init__.py` 公开 API

导出以下类供外部使用：

```python
from .conversation_manager import ConversationManager
from .chat_worker import ChatWorker
from .execution_engine import ExecutionEngine, StepResult
from .tool_registry import ToolRegistry, ToolSpec
from .context_builder import ContextBuilder
from .prompts import build_system_prompt
```

### 4. 依赖方向

```
UI (smart_assistant/panel.py 等)
  ├──→ src.transbridge.smart_assistant (backend)
  │      ├── conversation_manager  →  (无内部依赖)
  │      ├── chat_worker           →  (无内部依赖)
  │      ├── execution_engine      →  (无内部依赖)
  │      ├── tool_registry         →  (无内部依赖)
  │      ├── context_builder       →  AppContext (外部)
  │      └── prompts               →  .tool_registry
  └──→ src.transbridge.ai_translator (已有)
  └──→ src.transbridge.paratranz (已有)
```

后端不依赖 UI，无循环导入风险。

### 5. 与已有 ADR 的关系

- **ADR-004** (QThread 异步模式)：ChatWorker 遵循 QThread 模式，搬迁不改变实现
- **ADR-005** (TOML Prompt 模板)：prompts.py 遵循 TOML 模板约定，搬迁不改变实现
- 两个 ADR 均无需更新

## Consequences

- **正面**：UI 与业务逻辑清晰分离；后端组件可被非 UI 场景（如 CLI、测试）复用；符合项目既有分层惯例
- **负面**：需要更新 5 个文件的 import 路径（chat_widget.py 4 处、plan_card.py 1 处、prompts.py 1 处）
- **风险**：搬迁后 import 错误 → 启动时 ImportError；通过逐文件验证规避

### 更新: 2026-05-10 - 提取共享基础设施 infra/ 包

**决策**: 新增项目级共享基础设施包 `src/transbridge/infra/`，与 `ai_translator/`、`smart_assistant/` 等业务包平级。

```
src/transbridge/infra/
├── __init__.py                  # 公开导出
├── llm_client.py                # LLMClient + create_llm_client（从 ai_translator/ 搬迁）
├── embedding_client.py          # EmbeddingClient（从 ai_translator/ 搬迁）
├── config.py                    # LLMConfig（从 paratranz/config_manager.py 提取）
└── vector_store.py              # FAISS 索引管理（新建）
```

**infra/ 导出的公开符号**:
- `LLMClient`, `create_llm_client` — LLM 客户端抽象 + 工厂方法
- `EmbeddingClient` — 文本嵌入生成
- `LLMConfig` — LLM API 配置（api_key, base_url, model, provider）
- `VectorStore` — FAISS 索引的创建/添加/搜索/保存/加载

**原因**: LLMClient、EmbeddingClient、LLMConfig 被 ai_translator 和 smart_assistant 双方使用，属于共享基础设施而非某个业务包的私有组件。提取到 infra/ 后消除 ai_translator ↔ smart_assistant 间的直接代码依赖，两者都仅依赖 infra/。

**影响**:
- ai_translator/ 失去 2 文件（llm_client.py, embedding_client.py），内部 import 更新
- paratranz/config_manager.py 失去 LLMConfig 类，保留 ParatranzConfig
- smart_assistant/ 的 LLM 相关引用改指 infra/
- 新建 vector_store.py 提供统一 FAISS 接口，ai_translator/term_database.py 和 smart_assistant/memory/memory_store.py 都通过它操作索引

### 更新: 2026-05-10 - Agent 框架升级新增 4 个子包

**决策**: 在 `src/transbridge/smart_assistant/` 下新增 4 个子包以支持 FR7.13 Phase 1 的四个能力：

```
src/transbridge/smart_assistant/
├── skills/              # FR7.13.1 Skill 系统
│   ├── __init__.py
│   ├── skill_loader.py       # 加载/解析 Skill 定义文件（TOML 格式，见 ADR-005）
│   ├── skill_registry.py     # 运行时 Skill 注册表，按触发条件匹配
│   └── skill_executor.py     # Skill 执行调度
├── file_parser/         # FR7.13.2 文件上传解析
│   ├── __init__.py
│   ├── base.py               # 统一接口 FileParser（ABC）
│   ├── text_parser.py        # Excel/CSV/Markdown/TXT/JSON
│   ├── binary_parser.py      # PDF/Word
│   └── paratranz_parser.py   # ParaTranz 导出格式
├── memory/              # FR7.13.3 长期记忆
│   ├── __init__.py
│   ├── memory_store.py       # FAISS 向量存储 + 精确索引
│   ├── embedding.py          # 嵌入生成（复用现有 LLMClient）
│   └── memory_retriever.py   # 语义+精确两阶段召回
├── reflexion/           # FR7.13.4 自纠错
│   ├── __init__.py
│   └── retry_handler.py      # 失败分析 + 参数调整 + 重试循环（默认 3 次）
├── (现有 7 文件保持不变)
```

**子包间依赖**: skills → file_parser (Skill 可引用解析后的文件) · skills → tool_registry (Skill 关联工具) · memory → (独立) · reflexion → execution_engine (注入点) · memory 和 reflexion 之间无依赖

**原因**: Phase 1 四项能力正交，各为独立子包。按能力拆分子包避免平铺文件过多（当前 7 个，Phase 1 后 18+），保持可维护性。遵循 ADR-008 既有的「业务逻辑与 UI 分离」原则——4 个子包均属业务逻辑层，不引入 UI 依赖。

### 更新: 2026-05-10 - Phase 2 多 Agent 协作架构：新增 agents/ 子包

**决策**: 在 `src/transbridge/smart_assistant/` 下新增 `agents/` 子包，实现 FR7.13 Phase 2 的多 Agent 协作能力，引入 Agent 定义模型、注册表、调度编排与并行执行四大组件。

**子包结构**:

```
src/transbridge/smart_assistant/agents/
├── __init__.py               # 公开导出
├── agent_spec.py             # AgentSpec + AgentInstance 数据类
├── agent_registry.py         # AgentRegistry 注册/查询/启禁
├── orchestrator.py           # 任务分解 + 调度映射 + 结果汇总
└── agent_worker.py           # AgentWorker(QThread) 单个 Agent 执行线程
```

**核心数据模型**:

(1) `AgentSpec` — Agent 定义模型（dataclass）:
- `agent_id: str` — 唯一标识，如 `"translator"`, `"proofreader"`, `"orchestrator"`
- `name: str` — 显示名称
- `role: str` — 角色描述（一段话，注入 system prompt）
- `namespace: str` — 工具命名空间，控制 ToolRegistry 可见范围
- `tools: list[str]` — 关联工具名称列表
- `skills: list[str]` — 关联 Skill ID 列表
- `system_prompt: str` — Agent 专属 system prompt
- `enabled: bool = True` — 启用/禁用开关

(2) `AgentRegistry` — Agent 运行时注册表:

```python
class AgentRegistry:
    def register(self, spec: AgentSpec) -> None
    def get(self, agent_id: str) -> AgentSpec | None
    def list_all(self) -> list[AgentSpec]
    def list_enabled(self) -> list[AgentSpec]
    def enable(self, agent_id: str) -> None
    def disable(self, agent_id: str) -> None
```

(3) `AgentInstance` — Agent 实例与项目绑定（dataclass）:
- `instance_id: str` — UUID，运行实例唯一标识
- `agent_spec: AgentSpec` — 引用注册表中的 Agent 定义
- `project_path: Path` — 绑定的项目目录
- `ctx: AppContext` — 执行上下文，创建时绑定，生命周期内不变

设计意图：同一 `AgentSpec` 可创建多个 `AgentInstance`，每个绑定到不同项目，支持多项目并行翻译。

**ToolRegistry namespace 扩展**:

扩展现有 `ToolRegistry`（`smart_assistant/tool_registry.py`），增加 namespace 参数，实现工具按 Agent 隔离:

```python
class ToolRegistry:
    def register(self, spec: ToolSpec, namespace: str = "default") -> None
    def get(self, name: str, namespace: str | None = None) -> ToolSpec | None
    def list_namespace(self, namespace: str) -> list[ToolSpec]
    def list_all_namespaces(self) -> dict[str, list[ToolSpec]]
```

权限模型: `namespace=None` 表示全部可见（编排 Agent 特权）；指定 namespace 时仅返回该空间内的工具。

**编排 Agent 调度模型**:

Orchestrator Agent 负责多 Agent 协作的全流程调度:

1. **任务分解**: 接收用户请求 → LLM 分解为子任务列表
2. **子任务格式**: `{task_id, agent_type, action, input_data, depends_on}`
3. **调度映射**: 将子任务映射为 `ExecutionEngine` 的 step dict（扩展 `agent` 字段，指向目标 `agent_type`）
4. **结果汇总**: 所有子任务完成后，汇总各 Agent 输出为最终结果

**预置 Agent 定义**:

| agent_id | namespace | 工具 | 可见范围 |
|---|---|---|---|
| `translator` | `"translator"` | translate_text, lookup_term, search_memory | 仅 translation 命名空间 |
| `proofreader` | `"proofreader"` | check_consistency, validate_format, search_memory | 仅 proofreading 命名空间 |
| `orchestrator` | `None`(全部) | decompose_task, summarize_results, search_memory | 全部命名空间 |

- `translator` 关联 skills: `["translate_with_terms"]`
- `proofreader` 和 `orchestrator` 的 skills 初始为空列表

**AgentWorker 接口契约**:

- 继承 `QThread`，遵循 ADR-004 异步模式
- 信号: `progress(str)` 进度报告、`finished(StepResult)` 执行完成、`error(str)` 错误通知
- 多个 `AgentWorker` 实例通过 `ExecutionEngine` 内部的 `ThreadPoolExecutor` 并行调度
- `AgentInstance.ctx` 在实例创建时绑定，整个生命周期不可变

**子包间依赖关系**:

```
agents/
├── agent_spec        →  (无内部依赖)
├── agent_registry    →  agent_spec
├── orchestrator      →  agent_registry, execution_engine (smart_assistant), tool_registry (smart_assistant)
└── agent_worker      →  agent_spec, agent_registry, tool_registry (smart_assistant), skills/skill_executor
```

agents/ 子包依赖 smart_assistant/ 层级的 execution_engine 和 tool_registry，以及 skills/ 子包的 skill_executor，但不依赖 UI 层。

**原因**: Phase 2 多 Agent 协作是 FR7.13 五项能力中唯一涉及"一组 Agent 并行/串行协作"的能力，天然需要独立的 Agent 定义与调度层。将 Agent 相关模型（AgentSpec/AgentInstance）、注册表（AgentRegistry）、编排器（Orchestrator）和工作者（AgentWorker）集中在 agents/ 子包中，与 Phase 1 的四个子包（skills/file_parser/memory/reflexion）平级，遵循单一职责原则。ToolRegistry 的 namespace 扩展以最小侵入方式实现工具隔离，无需新建独立的工具注册表。AgentInstance 的项目绑定机制复用现有 AppContext，避免引入新的上下文抽象。

**影响**:
- `smart_assistant/tool_registry.py` 的 `register`/`get` 方法签名变更，需新增 `namespace` 参数并增加 `list_namespace`/`list_all_namespaces` 方法。现有调用方（prompts.py、skills/skill_executor.py）需适配：无 namespace 需求的调用默认传 `"default"` 或保持 `namespace=None` 以获取全部工具
- `smart_assistant/execution_engine.py` 的 step dict 扩展 `agent` 可选字段，StepResult 新增 `agent_instance_id` 字段用于追溯
- `smart_assistant/__init__.py` 扩展导出列表，新增 `AgentSpec`, `AgentRegistry`, `AgentWorker`, `Orchestrator`
- 预置 Agent 定义在 `AgentRegistry` 初始化时加载，不依赖外部配置文件
- 与 Phase 1 子包的关系: `orchestrator` 依赖 `skills/skill_executor` 执行 skill；`agent_worker` 依赖 `reflexion/retry_handler` 实现失败重试；`agent_worker` 依赖 `memory/memory_retriever` 为 Agent 提供上下文召回

### 更新: 2026-05-22 - 超重文件拆分：模块与类粒度组织规范

**背景**: Smart Assistant 包经多轮功能迭代后，51 文件 ~9300 行中 8 个文件超过 300 行，其中 execution_engine.py（888行/37方法）、tool_translator.py（660行）、base.py（605行）已达临界质量。ADR-008 此前仅定义了包级和子包级分层，未涉及类/模块粒度的组织规范。

**决策**: 对 8 个超重文件进行职责拆分，建立以下模块粒度规范——

#### D1: ExecutionEngine 组合拆分（4 模块）

采用**组合模式**（非继承）拆分 ExecutionEngine 的 5 种职责：

```
smart_assistant/
├── execution_engine.py           # 保留: 委托门面 + StepResult + 重导出 (~85行)
├── graph_executor.py             # NEW: BFS图执行 + 护栏链/重试 + 生命周期 (~600行)
├── condition_evaluator.py        # NEW: AST条件表达式求值器 (~250行)
└── checkpoint_manager.py         # NEW: 检查点持久化管理器 (~80行)
```

- `ConditionEvaluator`: 承接全部 10 个 `_eval_ast_*` 方法，单一职责——对图节点的 condition 字段做布尔求值
- `CheckpointManager`: 承接 `_save_checkpoint/_load_checkpoint/_checkpoint_path/_safe_serialize`，管理图执行中断点的持久化与恢复
- `GraphExecutor`: 承接 BFS 图调度（`execute`/`execute_graph`/`_bfs_one_level`/`_dispatch_node`/`_run_single`）+ 护栏链 + 重试循环 + 生命周期 + 回调注册 + 决策注入
- `ExecutionEngine` 持有 `GraphExecutor` 实例（组合），作为委托门面暴露所有公开 API，同时通过 `graph_executor` 重导出 `ConditionEvaluator` 和 `CheckpointManager`
- `StepResult` 定义在 `graph_executor.py` 中（`execution_engine.py` 重导出），避免循环导入
- `_execute_tool_with_retry` 随 GraphExecutor 整体迁移（不迁入 reflexion 包）：retry 是执行层关注点，reflexion 是策略层关注点（ADR-009 边界不变）

**理由**: 37 方法的上帝类是最大的维护风险。AST 求值器（10 方法）天然独立——输入 condition 表达式 + 变量上下文，输出布尔值，无副作用；CheckpointManager 仅与文件系统交互。BFS 图调度逻辑（~400行）进一步提取到 GraphExecutor，ExecutionEngine 自身缩减为 84 行委托门面。四层组合：ExecutionEngine → GraphExecutor → ConditionEvaluator + CheckpointManager。

#### D2: base.py 类型定义独立

```
tools/
├── types.py           # NEW: ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse (~340行)
└── base.py            # 保留: 护栏构建+装饰器+作用域解析 (~280行)
                      #   顶部添加 from .types import * 重导出，保持所有现有 import 兼容
```

**理由**: `ToolResult`/`ExecutionContext` 是 39 个工具函数的返回值类型和执行上下文，被所有工具模块 import。将它们与执行逻辑（guard_chain/decorators）混在一起，导致 base.py 既是"类型定义仓库"又是"工具基础设施层"。分离后 import 关系更清晰：工具模块 `from tools.types import ToolResult`，`from tools.base import require_collection`。

#### D3: 工具模块函数封装为 Controller 类

消除 `tool_translator.py`（15 函数）、`tool_proofreader.py`（11 函数）、`tool_editor.py`（9 函数）的模块级函数反模式——

```
tools/
├── tool_translator.py       # TranslationController 类 (~350行)
│                            #   构造函数注入 AppContext + TaskManager
│                            #   每个工具作为一个实例方法
├── tool_proofreader.py      # ProofreaderController 类 (~280行)
├── tool_editor.py           # EditorController 类 (~350行)
└── _common.py               # NEW: 共享工具函数 (~80行)
                             #   _load_llm_config(), _build_postprocessor()
```

**变更前**:
```python
# 模块级函数通过闭包/外部全局获取 ctx
def _tool_start_translation(args: dict, ctx: ExecutionContext) -> ToolResult:
    config = _load_llm_config()  # 模块内重复定义
    ...
```

**变更后**:
```python
class TranslationController:
    def __init__(self, ctx: AppContext, task_manager: TaskManager):
        self._ctx = ctx
        self._task_mgr = task_manager

    def start_translation(self, args: dict, ctx: ExecutionContext) -> ToolResult:
        config = load_llm_config()  # 来自 _common.py
        ...
```

`_register_*_tools()` 注册函数保留在模块末尾，创建 Controller 实例后将其实例方法传入 `ToolSpec.execute`。

**理由**: 模块级函数通过闭包引用外部状态（AppContext 全局单例、TaskManager 全局单例），导致函数间隐式耦合、测试困难、可读性差。封装为 Controller 类后：依赖显式注入（构造器）、方法间通过 self 共享状态、mock 测试更简单。

#### D4: _common.py 消除 LLM 配置加载重复

`tool_translator.py` 和 `tool_proofreader.py` 中存在 `_load_llm_config()` 重复逻辑——从 `LLMConfig.load_from_file()` 加载，两文件各自实现了相同的 10 行代码。

提取到 `tools/_common.py`（仅 `load_llm_config()` 函数，~30行），两个 Controller 统一 import。未来新增工具模块复用同一入口。

`_build_postprocessor()` **不提取**：两处签名差异较大（参数数量不同、阶段配置不同），强行统一会引入回归风险，各自保留在对应 Controller 中。

#### D5: MemoryWriterThread 外提

`memory/memory_store.py`（335行）中内嵌的 `MemoryWriterThread` 类（~42行）提取到 `memory/memory_writer.py`，原位置 `from .memory_writer import MemoryWriterThread` 重导出。

#### D6: ConversationOrchestrator 精简

`conversation_orchestrator.py` 不改文件数量，仅内部重构——
- 提取 `_create_llm_client()` 模块级函数（~60行）：封装缓存键计算、配置 mtime 检测、客户端创建
- 去除 `_get_prompt_builder()` 中与 `prompts.build_system_prompt()` 重复的内联逻辑

> **注**: 经代码验证，`react_depth` 和 `auto_mode` 无重复定义问题（各有一次属性赋值 + 一组 property getter/setter），无需修复。

#### D7: TaskManager 精简

不拆新文件，内部精简——
- 合并 `on_completed`+`on_failed` → `on_finished`；`notify_completed`+`notify_failed` → `notify_finished`（旧方法保留为 deprecated wrapper，兼容 `chat_widget.py` 等外部调用方）
- 所有公开 API 签名保持不变

> **注**: `set_main_thread_dispatcher`/`reset_dispatcher`/`get_handle` **保留不动**——经全局搜索确认 `chat_widget.py:712` 和 `tool_proofreader.py:129` 有活跃调用方，移除会破坏现有功能。

#### 模块粒度规范（本次建立，后续遵循）

| 指标 | 上限 | 说明 |
|------|------|------|
| 文件行数 | ≤450 | 超过时评估拆分 |
| 类方法数 | ≤22 | 超过时评估职责分离（回调注册/事件处理方法不计入） |
| 模块级函数数 | ≤3 | 超过时考虑类封装 |

> **注**: 类方法数上限不包含基础设施方法——回调注册（`on_*`）、事件发射（`_emit`）等纯转发/簿记方法不计入职责计数。这些方法不包含业务逻辑，提取到独立模块会破坏内聚性。ExecutionEngine 拆分后保留 21 方法（含 7 个回调注册 + 1 个静态 `_emit`），实际业务方法 13 个，符合规范。

#### 导入兼容性策略

所有新模块在原位置重导出，外部调用无需修改：
```python
# base.py 顶部
from .types import ToolResult, ExecutionContext, HITLType, HITLRequest, HITLResponse
# 外部仍可 import: from ...tools.base import ToolResult

# execution_engine.py 顶部
from .condition_evaluator import ConditionEvaluator
from .checkpoint_manager import CheckpointManager
# 外部仍可 import: from ...smart_assistant.execution_engine import ExecutionEngine
```

**影响**:
- 新增 6 文件：`condition_evaluator.py`、`checkpoint_manager.py`、`graph_executor.py`、`tools/types.py`、`tools/_common.py`、`memory/memory_writer.py`
- 修改 8 文件：`execution_engine.py`、`tools/base.py`、`tools/tool_translator.py`、`tools/tool_proofreader.py`、`tools/tool_editor.py`、`conversation_orchestrator.py`、`memory/memory_store.py`、`tools/task_manager.py`
- `__init__.py` 懒加载映射更新：`StepResult` → `.graph_executor`
- 测试 import 路径适配：内部辅助函数迁移到 Controller 类后，3 个测试文件更新 import
- 零新依赖，零外部 import 断裂
- 与 ADR-009（Reflexion 边界）无冲突：`_execute_tool_with_retry` 保留在 GraphExecutor
- 与 ADR-012（护栏体系）无冲突：guard_chain 函数保留在 base.py

### 更新: 2026-08-05 - SessionController 会话控制流提取

**背景**: FR12 要求将分散在 ChatWidget、ConversationOrchestrator、ToolExecutionHandler 中的会话主循环控制流提取为显式状态机。当前控制流通过回调链隐式串联——ChatWidget 的 8 个方法末尾各自判断 `_check_react_depth() + _run_llm_round()`，Orchestrator._on_finished() 内部做 Plan/Tool/Reply 模式分发，ToolHandler._handle_result() 末尾触发 ReAct 继续。没有统一的"当前处于什么状态"的显式表达。ADR-008 此前仅定义了包级和文件级分层，未涉及**会话控制流**的归属。

**决策 D8: 新增 SessionController 作为会话级顶层调度者**

在 `smart_assistant/` 后端包中新建 `session_controller.py`，位于 ConversationOrchestrator 和 ToolExecutionHandler 之上：

```
smart_assistant/
├── session_controller.py        # NEW: 会话状态机 (~250-300行)
├── conversation_orchestrator.py  # 保留: LLM轮次生命周期（移除分发逻辑）
├── tool_execution_handler.py     # 保留: 工具调度（移除ReAct触发）
├── execution_engine.py           # 不变
├── graph_executor.py             # 不变
└── ...
```

**层级关系**:
```
SessionController          ← 会话级：管理 IDLE→THINKING→AWAITING→EXECUTING
  ├── ConversationOrchestrator  ← 轮次级：LLM请求/流式/响应解析
  ├── ToolExecutionHandler      ← 步次级：工具查找/权限/执行/结果
  └── ExecutionEngine           ← 图次级：DAG调度/Checkpoint/HITL
       └── GraphExecutor
```

SessionController 持有 Orchestrator/ToolHandler/Engine 引用，通过回调接收下层事件，通过方法调用下达指令。

**决策 D9: enum + 显式分发表实现状态机**

不使用外部状态机库。采用 `enum.Enum` + 每个 `handle_*` 方法内部断言当前状态 + 显式 `_transition_to()` 方法：

```python
class SessionController:
    class State(Enum):
        IDLE = "idle"
        THINKING = "thinking"
        AWAITING_CONFIRM = "awaiting"
        EXECUTING = "executing"
        AWAITING_TASK = "awaiting_task"

    def handle_user_message(self, text: str) -> None:
        assert self._state == State.IDLE
        self._transition_to(State.THINKING)
        self._orchestrator.start_round()

    def handle_execution_complete(self, results: list) -> None:
        assert self._state == State.EXECUTING
        if self._react_depth >= self._MAX_REACT_DEPTH:
            self._transition_to(State.IDLE)
        else:
            self._react_depth += 1
            self._transition_to(State.THINKING)
            self._orchestrator.start_round()
```

**理由**: 5 状态 × 7 转换的规模不需要框架。`enum + assert + _transition_to()` 模式与项目现有的 `ConditionEvaluator._AST_DISPATCH` 分发表风格一致。每个转换点显式可读、可调试、可测试。

**决策 D10: 双层状态管理 — SessionController (会话级) vs GraphExecutor (执行级)**

两个状态机运行在不同抽象层级，互不穿透：

| 层级 | 管理者 | 状态粒度 | 生命周期 |
|------|--------|---------|---------|
| 会话级 | SessionController | IDLE/THINKING/AWAITING/EXECUTING/TASK | 一次用户对话 |
| 执行级 | GraphExecutor | BFS层级/Condition路由/Loop迭代/HITL暂停 | 一次 plan/tool 执行 |

SessionController 不关心 GraphExecutor 内部的 BFS/Condition/Loop/HITL 细节——它只知道"提交了一个执行计划，等待完成通知"。GraphExecutor 的 HITL 暂停/恢复在 SessionController 看来是透明的（EXECUTING 状态持续直到 `all_finished` 回调）。这与 ADR-011 的设计一致：GraphExecutor 管理图级状态，SessionController 管理会话级状态。

**决策 D11: 回调契约与接口变更**

SessionController 定义清晰的输入接口（外部调用）和输出接口（回调注入）：

**输入接口**（供 UI/外部调用）:
- `handle_user_message(text: str)` — IDLE → THINKING
- `handle_user_confirmed(steps, mode)` — AWAITING → EXECUTING
- `handle_user_cancelled()` — AWAITING → IDLE
- `handle_execution_complete(results)` — EXECUTING → THINKING | IDLE
- `handle_task_completed(task_id, result)` — AWAITING_TASK → THINKING | IDLE
- `handle_abort()` — 任意状态 → IDLE

**输出接口**（回调注入，供 UI 响应）:
- `on_state_changed(old, new, context)` — 状态变更通知
- `on_present_plan_card(steps)` / `on_present_tool_card(step)` / `on_present_batch_tool_card(steps)`
- `on_system_message(text)` / `on_conversation_end()`

**现有组件接口变更**（最小侵入）:
- `ConversationOrchestrator`: 新增 `on_response_parsed(parsed)` 回调，替代内部分发逻辑。`_on_finished()` 不再做 Plan/Tool/Reply 分发，改为调用回调通知 Controller
- `ToolExecutionHandler`: 新增 `on_step_completed(step, result)` 回调。`_handle_result()` 不再末尾触发 ReAct 继续，改为调用回调通知 Controller

**决策 D12: 两 Story 分步迁移**

| Story | 内容 | 风险 |
|-------|------|------|
| **S01: 核心引入** | 新建 `session_controller.py`，ChatWidget 创建 Controller 实例并注入回调。新路径与旧路径并行运行——Controller 的状态转换不删除 ChatWidget 中旧逻辑，通过比对验证等价性后再切换 | 低 |
| **S02: 旧逻辑清理** | 删除 ChatWidget 中 `_run_llm_round()`/`_check_react_depth()`/`_check_react_continue()`/`_auto_execute_steps()` 等方法；Orchestrator 移除模式分发逻辑；ToolHandler 移除 ReAct 触发逻辑 | 中 |

**S01 新旧并行策略**: ChatWidget 同时持有 SessionController 引用和旧控制方法。`send_user_message()` 同时触发 Controller.handle_user_message() 和旧路径 `_run_llm_round()`。通过日志比对两者输出，验证一致后（通常 20-30 轮对话），在 S02 中删除旧路径。不引入 feature flag——用代码分支 + import 控制即可。

**影响**:
- 新增 1 文件：`smart_assistant/session_controller.py`
- 修改 4 文件：`chat_widget.py`（S01 新增 Controller 初始化+回调注入，S02 删 ~150行）、`conversation_orchestrator.py`（新增 `on_response_parsed` 回调，移除分发 ~40行）、`tool_execution_handler.py`（新增 `on_step_completed` 回调，移除 ReAct 触发 ~15行）、`__init__.py`（新增 `SessionController` 懒加载映射）
- 新增测试 1 文件：`test_session_controller.py`（状态转换覆盖 + 集成测试）
- 零新依赖
- 161 现有测试零回归
- 外部行为不变（用户感知到的对话流程完全一致）
- 与 ADR-011 无冲突：SessionController 和 GraphExecutor 是不同抽象层级
- 与 ADR-012 无冲突：护栏链仍在 ToolExecutionHandler 中，SessionController 不介入

### 更新: 2026-08-05 - SessionManager 会话持久化与多会话管理

**背景**: FR13 要求添加多会话管理能力——用户可创建、切换、删除多个命名会话，每个会话有独立的对话历史，数据以 JSON 文件全局持久化，启动时自动恢复。

**决策 D13: SessionManager 作为独立后端组件**

在 `smart_assistant/` 下新建 `session_manager.py`，纯 Python 实现（ADR-008 兼容）。位于 ConversationManager 的下层——SessionManager 负责会话数据的持久化与多会话索引，ConversationManager 负责单会话内的消息列表操作。

```
smart_assistant/
├── session_controller.py         # FR12: 会话内控制流状态机
├── session_manager.py            # NEW: FR13 多会话持久化管理
├── conversation_manager.py       # 保留: 单会话消息列表（+to_dict/from_dict）
├── ...
```

层级关系：
```
SmartAssistantPanel (UI 协调)
  ├── SessionListWidget (UI)     ← NEW: 左侧会话列表栏
  ├── ChatWidget (UI)            ← 修改: 支持会话切换
  └── SessionManager (后端)      ← NEW: 会话 CRUD + JSON 持久化
        └── ConversationManager  ← 保留: 消息列表操作
```

**决策 D14: JSON 文件存储 + 目录扫描 + 懒加载**

- 存储位置：全局 `data/sessions/` 目录（不绑定项目）
- 存储格式：每个会话一个 `{session_id}.json` 文件
- 索引方式：启动时扫描目录加载全部会话元数据到内存缓存；消息列表懒加载（仅切换会话时读取完整文件）
- 自动保存：每轮 LLM 对话结束后自动保存当前活跃会话

**JSON Schema**:
```json
{
    "session_id": "abc123",
    "name": "翻译 Dragonborn",
    "created_at": "2026-08-05T10:00:00",
    "last_active_at": "2026-08-05T14:30:00",
    "project_name": "Dragonborn",
    "message_count": 12,
    "messages": [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "..."}
    ]
}
```

**理由**: 零新依赖（JSON 序列化使用标准库 json）。目录扫描在会话数 <100 时性能无感知。懒加载避免启动时一次读取全部消息（每个会话可能含数万字对话）。与项目 JSON 惯例一致（ADR-006 current.json、memory_metadata.json）。

**决策 D15: ConversationManager 序列化接口**

`ConversationManager` 新增两个方法：
- `to_dict() → dict`: 返回 `{"messages": [...]}`，消息列表中每个 dict 包含 `role` 和 `content`
- `from_dict(data: dict) → None`: 替换内部 `_messages` 列表并重置轮次索引

**理由**: 保持 ConversationManager 职责单一（消息列表管理），序列化逻辑不内嵌到 SessionManager。SessionManager 调用 `conv.to_dict()` 获取消息数据后写入 JSON，加载时读取 JSON 后调用 `conv.from_dict()`。

**决策 D16: Panel 协调的会话切换流程**

```
用户点击会话B
  → SessionListWidget 发出 on_switch_session("session_B")
  → Panel._on_switch_session("session_B")
    → 1. chat_widget.save_current_session()
         → conversation.to_dict() → session_manager.save_session("A", data)
    → 2. chat_widget.load_session("session_B")
         → session_manager.get_session("B") → 返回 data
         → conversation.clear()
         → conversation.from_dict(data["messages"])
         → 重建所有 MessageBubble 渲染历史消息
         → 更新 session_manager 中 last_active_at
         → controller.handle_abort()  # 重置状态到 IDLE
```

**Decision D17: SessionListWidget UI 契约**

新建 `ui/tools/smart_assistant/session_list_widget.py`：
- 位于 Panel 左侧，可折叠（toggle 按钮）
- 每个会话行显示：名称（粗体）+ 消息数 + 时间（灰色小字）
- 当前活跃会话高亮（背景色 `#E3F2FD`）
- 顶部"+"按钮 → 弹出 QInputDialog 命名 → `on_create_session(name)` 回调
- 每行悬停显示"×"删除按钮 → QMessageBox 确认 → `on_delete_session(session_id)` 回调
- 点击行 → `on_switch_session(session_id)` 回调

**影响**:
- 新增 2 文件：`smart_assistant/session_manager.py` + `ui/tools/smart_assistant/session_list_widget.py`
- 修改 4 文件：`conversation_manager.py` (+to_dict/from_dict) + `chat_widget.py` (+save/load/switch) + `panel.py` (+协调) + `__init__.py` (+懒加载)
- 新增目录：`data/sessions/`
- 零新依赖
- 与 FR12 (SessionController) 无冲突：切换会话时 `handle_abort()` 重置状态
