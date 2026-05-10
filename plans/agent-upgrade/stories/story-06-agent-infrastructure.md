# Story 06: 多 Agent 基础设施

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/agents
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01（同 plan）：已完成 → infra/ 包就绪（LLMClient/EmbeddingClient/LLMConfig/VectorStore）
- Story-02（同 plan）：已完成 → skills/ 子包就绪，Skill TOML 格式可用
- Story-05（同 plan）：已完成 → ExecutionEngine 已有 RetryHandler 注入

### 引用的架构决策
- ADR-008 更新（2026-05-10）: agents/ 子包结构 + ToolRegistry namespace 扩展 + AgentSpec/AgentRegistry/AgentInstance 数据模型

## 验收标准

（从 plan 原样复制）

- [ ] `AgentSpec` 数据类（agent_id/name/role/namespace/tools/skills/system_prompt/enabled）
- [ ] `AgentInstance` 数据类（instance_id/agent_spec/project_path/ctx），支持同类型多实例
- [ ] `AgentRegistry` 类（register/get/list_all/list_enabled/enable/disable）
- [ ] `ToolRegistry` namespace 扩展：`register(name, spec, namespace)` / `get(name, namespace)` / `list_namespace(ns)` / `list_all_namespaces()`
- [ ] `namespace=None` 时返回全部工具（编排 Agent 特权）
- [ ] `agents/__init__.py` 导出 5 个公开符号
- [ ] 3 个预置 Agent 在启动时自动注册：translator（namespace="translator"）、proofreader（namespace="proofreader"）、orchestrator（namespace=None，全工具可见）
- [ ] 现有 ToolRegistry.register() 调用方保持兼容（namespace 默认 "default"）

## 数据流

```
应用启动（AppContext 初始化后）
  │
  ├─→ AgentRegistry.init_presets()
  │     ├─ AgentSpec("translator", namespace="translator", tools=[...], skills=["translate_with_terms"])
  │     ├─ AgentSpec("proofreader", namespace="proofreader", tools=[...])
  │     └─ AgentSpec("orchestrator", namespace=None, tools=[...])
  │
  ├─→ _register_v1_tools()（现有，需扩展 namespace 参数）
  │     ├─ lookup_terms        → namespace="translator"
  │     ├─ translate_entries   → namespace="translator"
  │     ├─ check_quality       → namespace="proofreader"
  │     ├─ get_collection_summary → namespace="default"（通用）
  │     ├─ export_json         → namespace="default"
  │     └─ write_back          → namespace="default"
  │
  ▼
运行时（S07 实现 Or后使用）
  │
  ├─ agent = AgentRegistry.get("translator") → AgentSpec
  ├─ instance = AgentInstance(uuid4(), agent, project_path, ctx)
  ├─ tools = ToolRegistry.list_namespace(instance.agent_spec.namespace)
  │     → [ToolSpec(lookup_terms), ToolSpec(translate_entries)]
  │
  └─ 编排 Agent 查全工具:
       ToolRegistry.list_all_namespaces() → {"translator": [...], "proofreader": [...], "default": [...]}
       ToolRegistry.get("write_back", namespace=None) → ToolSpec(...)  # None=全命名空间搜索
```

## 关键接口

### agent_spec.py（新建）

```python
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4


@dataclass
class AgentSpec:
    """Agent 定义模型——描述一个 Agent 的身份、能力和工具范围。"""
    agent_id: str
    name: str
    role: str
    namespace: str | None      # None = 编排Agent，可查全部工具
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    system_prompt: str = ""
    enabled: bool = True


@dataclass
class AgentInstance:
    """Agent 运行时实例——将 AgentSpec 绑定到具体项目和上下文。"""
    instance_id: str = field(default_factory=lambda: uuid4().hex)
    agent_spec: AgentSpec | None = None
    project_path: Path | None = None
    ctx: object | None = None      # AppContext，创建时绑定，生命周期内不变
```

### agent_registry.py（新建）

```python
class AgentRegistry:
    """Agent 运行时注册表——管理所有 Agent 定义的生命周期。"""

    _agents: dict[str, AgentSpec] = {}

    @classmethod
    def register(cls, spec: AgentSpec) -> None:
        """注册或覆盖 Agent 定义。重复 agent_id → 覆盖（update 语义）。"""
        cls._agents[spec.agent_id] = spec

    @classmethod
    def get(cls, agent_id: str) -> AgentSpec | None:
        """按 ID 获取 Agent。不存在返回 None。"""
        return cls._agents.get(agent_id)

    @classmethod
    def list_all(cls) -> list[AgentSpec]:
        return list(cls._agents.values())

    @classmethod
    def list_enabled(cls) -> list[AgentSpec]:
        return [a for a in cls._agents.values() if a.enabled]

    @classmethod
    def enable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            spec.enabled = True

    @classmethod
    def disable(cls, agent_id: str) -> None:
        spec = cls._agents.get(agent_id)
        if spec:
            spec.enabled = False

    @classmethod
    def init_presets(cls) -> None:
        """启动时注册 3 个预置 Agent。"""
        cls.register(AgentSpec(
            agent_id="translator",
            name="翻译 Agent",
            role="你是一个专业的游戏 Mod 翻译 Agent。负责将英文文本翻译为中文，严格遵循术语库的标准译名。遇到不确定的术语时主动查询术语库。",
            namespace="translator",
            tools=["lookup_terms", "translate_entries", "get_collection_summary"],
            skills=["translate_with_terms"],
            system_prompt="你是 TransBridge 翻译引擎。请严格按照术语库的标准译名翻译，保持原文格式标签不变。",
        ))
        cls.register(AgentSpec(
            agent_id="proofreader",
            name="校对 Agent",
            role="你是一个专业的翻译校对 Agent。负责检查译文的一致性和格式正确性，但不直接修改译文。",
            namespace="proofreader",
            tools=["check_quality", "lookup_terms", "get_collection_summary"],
            skills=[],
            system_prompt="你是 TransBridge 校对引擎。请检查译文质量，发现术语不一致、格式错误时报告具体位置和建议。",
        ))
        cls.register(AgentSpec(
            agent_id="orchestrator",
            name="编排 Agent",
            role="你是一个翻译任务编排 Agent。负责分析用户请求，将复杂任务分解为子任务，分配给翻译或校对 Agent 执行，并汇总结果。",
            namespace=None,
            tools=["get_collection_summary", "lookup_terms", "check_quality", "translate_entries",
                   "export_json", "write_back"],
            skills=[],
            system_prompt="你是 TransBridge 编排引擎。分析用户意图，制定执行计划，调度合适的 Agent 完成任务，最后汇总呈现结果。",
        ))
```

### tool_registry.py（修改）

```python
# 内部存储变更：_tools: dict[str, ToolSpec] → _namespaced_tools: dict[str, dict[str, ToolSpec]]
# {"translator": {"lookup_terms": ToolSpec, ...}, "default": {...}}

class _ToolRegistry:
    _namespaced_tools: dict[str, dict[str, ToolSpec]] = {"default": {}}

    @classmethod
    def register(cls, spec: ToolSpec, namespace: str = "default") -> None:
        if namespace not in cls._namespaced_tools:
            cls._namespaced_tools[namespace] = {}
        cls._namespaced_tools[namespace][spec.name] = spec

    @classmethod
    def get(cls, name: str, namespace: str | None = None) -> ToolSpec | None:
        if namespace is not None:
            return cls._namespaced_tools.get(namespace, {}).get(name)
        # namespace=None：编排 Agent 特权，搜索全部 namespace
        for ns_tools in cls._namespaced_tools.values():
            if name in ns_tools:
                return ns_tools[name]
        return None

    @classmethod
    def list_all(cls) -> list[ToolSpec]:
        """保持向后兼容：返回所有 namespace 的工具去重列表。"""
        seen: set[str] = set()
        result = []
        for ns_tools in cls._namespaced_tools.values():
            for name, spec in ns_tools.items():
                if name not in seen:
                    seen.add(name)
                    result.append(spec)
        return result

    @classmethod
    def list_namespace(cls, namespace: str) -> list[ToolSpec]:
        return list(cls._namespaced_tools.get(namespace, {}).values())

    @classmethod
    def list_all_namespaces(cls) -> dict[str, list[ToolSpec]]:
        return {ns: list(tools.values()) for ns, tools in cls._namespaced_tools.items()}

    @classmethod
    def build_tool_schema_for_prompt(cls, namespace: str | None = None) -> str:
        """namespace=None 时列出全部工具（编排Agent）；否则只列该namespace的工具。"""
        if namespace is not None:
            tools = cls._namespaced_tools.get(namespace, {})
        else:
            tools = {}
            for ns_tools in cls._namespaced_tools.values():
                tools.update(ns_tools)
        lines = ["可用工具列表："]
        for tool in tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
            lines.append(f"  参数: {tool.parameters}")
        return "\n".join(lines)
```

### agents/__init__.py（新建）

```python
from .agent_spec import AgentSpec, AgentInstance
from .agent_registry import AgentRegistry

__all__ = ["AgentSpec", "AgentInstance", "AgentRegistry"]
```

### smart_assistant/__init__.py（修改）

在现有导出中追加 agents 子包导出。

## 实现步骤

### 步骤 1: 创建 AgentSpec + AgentInstance 数据类

**涉及文件**: `src/transbridge/smart_assistant/agents/agent_spec.py`（新建）

**实现要点**:
- 两个 dataclass: AgentSpec（Agent 定义）和 AgentInstance（运行时绑定）
- AgentInstance.instance_id 默认 uuid4().hex（短格式，无需 import uuid）
- AgentInstance.ctx 类型标注为 object（避免循环导入 AppContext）
- 职责边界：数据容器，无业务逻辑

**边界条件**:
- AgentSpec 创建时缺少必填字段 → dataclass 自身报错
- AgentInstance 未绑定时 agent_spec/project_path/ctx 均为 None → getter 调用方负责判空

**测试策略**:
- 创建 AgentSpec，验证字段默认值
- 创建 AgentInstance，验证 instance_id 自动生成且不重复

### 步骤 2: 创建 AgentRegistry

**涉及文件**: `src/transbridge/smart_assistant/agents/agent_registry.py`（新建）

**实现要点**:
- 类级别单例模式（与 ToolRegistry 风格一致：类成员变量 + @classmethod）
- 核心方法：register/get/list_all/list_enabled/enable/disable/init_presets
- init_presets() 注册 translator/proofreader/orchestrator 三个预置 Agent
- 职责边界：只管 Agent 定义的生命周期（注册/查询/启禁），不涉及执行

**边界条件**:
- 重复 agent_id register → 覆盖（update 语义），不报错
- get(不存在的ID) → 返回 None
- enable/disable 不存在的ID → 静默忽略
- init_presets() 重复调用 → 覆盖已有定义（幂等）

**伪代码**:
```python
class AgentRegistry:
    _agents: dict[str, AgentSpec] = {}

    @classmethod
    def register(cls, spec):
        cls._agents[spec.agent_id] = spec
```

**测试策略**:
- register + get 往返验证
- list_all/list_enabled 计数验证
- enable/disable 状态切换验证
- init_presets() 后确认 3 个 Agent 已注册

### 步骤 3: 创建 agents/__init__.py

**涉及文件**: `src/transbridge/smart_assistant/agents/__init__.py`（新建）

**实现要点**:
- 导出 AgentSpec, AgentInstance, AgentRegistry
- 不导出 orchestrator 和 agent_worker（属于 S07）

**边界条件**:
- ImportError：AgentSpec/AgentRegistry 不存在 → S02 完成后正常

**测试策略**:
- `python -c "from src.transbridge.smart_assistant.agents import AgentSpec, AgentInstance, AgentRegistry"` 无 ImportError

### 步骤 4: ToolRegistry namespace 扩展

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改）

**实现要点**:
- 内部存储迁移：`_tools: dict[str, ToolSpec]` → `_namespaced_tools: dict[str, dict[str, ToolSpec]]`
- 新增 `namespace` 参数到 register/get
- 新增 `list_namespace(ns)` / `list_all_namespaces()` 方法
- `build_tool_schema_for_prompt(namespace=None)` 接受可选 namespace 参数
- 启动时自动迁移：`_register_v1_tools()` 已存在 → 每个 register 调用改为指定 namespace
- 职责边界：只扩展存储层和查询接口，不修改 ToolSpec 数据结构

**向后兼容策略**:
- `register(spec)` 不传 namespace → 默认 "default"（现有调用方无需修改）
- `get(name)` 不传 namespace → 搜索全部 namespace（保持与当前 `_tools.get(name)` 等价）
- `list_all()` → 返回全部 namespace 去重工具列表（保持返回类型不变）

**边界条件**:
- `list_namespace(不存在的namespace)` → 返回空列表 `[]`
- `get(name, namespace=None)` 搜索全部 namespace → 找到第一个匹配返回
- `register(spec, namespace)` namespace 为空字符串 → 视为普通 namespace（与 "default" 独立）
- 同一 name 注册到不同 namespace → 各自独立存储

**伪代码**（内部结构变更）:
```python
class _ToolRegistry:
    _namespaced_tools: dict[str, dict[str, ToolSpec]] = {"default": {}}

    @classmethod
    def register(cls, spec, namespace="default"):
        if namespace not in cls._namespaced_tools:
            cls._namespaced_tools[namespace] = {}
        cls._namespaced_tools[namespace][spec.name] = spec

    @classmethod
    def get(cls, name, namespace=None):
        if namespace is not None:
            return cls._namespaced_tools.get(namespace, {}).get(name)
        for ns_tools in cls._namespaced_tools.values():
            if name in ns_tools:
                return ns_tools[name]
        return None
```

**测试策略**:
- 现有 v1 工具 register → get 往返验证（向后兼容）
- 同 name 注册到两个不同 namespace → 各自独立 get
- list_namespace("translator") → 仅返回 translator namespace 的工具
- list_all_namespaces() → 返回完整 namespace 字典
- get(name, namespace=None) → 编排 Agent 跨 namespace 搜索

### 步骤 5: v1 工具 namespace 分配

**涉及文件**: `src/transbridge/smart_assistant/tool_registry.py`（修改，`_register_v1_tools()` 函数）

**实现要点**:
- 为每个 v1 工具的 `register()` 调用添加 namespace 参数
- namespace 分配：
  - "translator": lookup_terms, translate_entries
  - "proofreader": check_quality
  - "default": get_collection_summary, export_json, write_back

**边界条件**:
- 迁移后现有 ChatWidget 通过 `list_all()` 仍可见所有工具 → 行为不变

### 步骤 6: smart_assistant/__init__.py 导出更新 + 启动集成

**涉及文件**: `src/transbridge/smart_assistant/__init__.py`（修改）

**实现要点**:
- 新增导入 AgentSpec, AgentInstance, AgentRegistry
- 更新 __all__ 列表

**启动集成点**（在 ChatWidget 或 AppContext 初始化时）:
```python
from src.transbridge.smart_assistant.agents import AgentRegistry
AgentRegistry.init_presets()
```

**边界条件**:
- init_presets() 重复调用安全（幂等）
- ToolRegistry 的 v1 工具必须在 AgentRegistry.init_presets() 之前注册（否则预置 Agent 引用的 tools 列表不可验证）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/agents/__init__.py` | 新建 | 子包入口，导出 AgentSpec/AgentInstance/AgentRegistry |
| `src/transbridge/smart_assistant/agents/agent_spec.py` | 新建 | AgentSpec + AgentInstance 数据类 |
| `src/transbridge/smart_assistant/agents/agent_registry.py` | 新建 | AgentRegistry（注册/查询/启禁/预置） |
| `src/transbridge/smart_assistant/tool_registry.py` | 修改 | 内部存储改为 namespace 字典；register/get 签名扩展；新增 list_namespace/list_all_namespaces；v1 工具分配 namespace |
| `src/transbridge/smart_assistant/__init__.py` | 修改 | 新增 agents 子包导出 |

## 风险与注意事项

- **风险**: ToolRegistry 内部存储格式变更可能遗漏调用方 → 缓解：`list_all()` 和 `get(name)` 保持向后兼容签名
- **注意**: AgentRegistry 使用 `uuid4().hex` 生成 instance_id 而非 `str(uuid4())`，避免 UUID 中的连字符
- **注意**: `AppContext` 类型在 agent_spec.py 中使用 `object` 标注避免循环导入
- **注意**: `init_presets()` 调用时机必须在 ToolRegistry v1 工具注册之后，否则预置 Agent 引用的 tools 列表中的工具尚未注册（当前未做运行时校验，仅文档提醒）
