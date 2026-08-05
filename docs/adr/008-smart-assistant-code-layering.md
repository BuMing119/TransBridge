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
