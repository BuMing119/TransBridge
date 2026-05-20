# Story 13: Agent 注册更新 + ExecutionEngine 适配 + orchestrator 优化

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: -ExecutionContext定义(移至Story01) +MCP护栏接入(B6联动) +map_to_steps修复(E4) +namespace通配符(O3)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult` + `ExecutionContext`
- Story 04-11 → 各 namespace 工具已注册
- Story 12 → parser/writer 工具已注册

### 引用的架构决策
- ADR-008 Phase 2: AgentRegistry 预置定义 + ToolRegistry namespace
- ADR-011: ExecutionEngine 适配新 ExecutionContext
- ADR-012: MCP 适配器（`_is_exposed` 利用 permission 字段）

## 验收标准

- [ ] AgentRegistry 新增 4 个 Agent（parser/editor/paratranz/writer），扩展 3 个现有 Agent 的 tools 列表
- [ ] ExecutionEngine 工具执行上下文升级：组装 `ExecutionContext(app_context, task_manager)`
- [ ] orchestrator Agent 不直接暴露 50+ 工具 schema，通过元工具描述层间接路由
- [ ] 现有 ReAct 循环 / PlanCard / Skill / MCP 调用不受影响

## 数据流

```
用户输入 → ChatWidget → ConversationManager → ExecutionEngine
    │
    ├─→ 当前为 orchestrator Agent
    │   → system_prompt 含 7 个元工具描述（非 50+ 完整 schema）
    │   → LLM 返回分解后的子任务: [{agent: "editor", action: "filter_by_stage", ...}]
    │
    ├─→ ExecutionEngine 将子任务分发到对应 Agent
    │   → 创建 ExecutionContext(app_context=ctx, task_manager=TaskManager())
    │   → 通过 ToolRegistry.get(tool_name, namespace=agent.namespace) 获取工具
    │   → spec.execute(args, execution_context)
    │
    └─→ 结果汇总 → 返回用户
```

## 关键接口

```python
# agents/agent_registry.py 更新 init_presets()

def init_presets(cls):
    # 现有 3 个 Agent 不变...
    
    cls.register(AgentSpec(
        agent_id="parser", name="解析 Agent",
        role="文件解析专家...",
        namespace="parser",
        tools=["parse_esp", "parse_eet", "parse_xt", "parse_sst", "import_json", "import_strings"],
        system_prompt="你是 TransBridge 文件解析引擎...",
    ))
    cls.register(AgentSpec(
        agent_id="editor", name="编辑 Agent",
        role="词条编辑与标签管理专家...",
        namespace="editor",
        tools=["filter_by_stage", "filter_by_category", "filter_by_label",
               "search_entries", "clear_all_filters", "get_visible_entries",
               "select_entries", "edit_translation",
               "list_labels", "create_label", "assign_label", "remove_label", "batch_assign_label"],
        system_prompt="你是 TransBridge 词条编辑引擎...",
    ))
    cls.register(AgentSpec(
        agent_id="paratranz", name="ParaTranz Agent",
        role="ParaTranz 平台同步专家...",
        namespace="paratranz",
        tools=["list_projects", "get_project_info", "compare_with_remote",
               "upload_entries", "download_entries",
               "export_artifact", "get_upload_history"],
        system_prompt="你是 TransBridge ParaTranz 同步引擎...",
    ))
    cls.register(AgentSpec(
        agent_id="writer", name="写回 Agent",
        role="文件写回专家...",
        namespace="writer",
        tools=["write_to_esp", "write_to_eet", "write_to_xt", "write_to_strings"],
        system_prompt="你是 TransBridge 文件写回引擎。所有写回操作需用户确认。",
    ))
    
    # 扩展现有 3 个 Agent 的 tools 列表
    translator.tools.extend(["start_translation", "start_polish", "pause_task", "stop_task", "get_task_status",
                              "get_translation_config", "set_translation_config", "set_scope", "get_scope_preview"])
    proofreader.tools.extend(["run_consistency_check", "run_format_validation",
                               "run_llm_refinement", "run_llm_polish", "run_llm_arbitration", "get_quality_report"])
    orchestrator.tools.extend(["get_app_state", "list_collections", "switch_collection",
                                "get_current_filters", "get_statistics",
                                "list_local_projects", "get_current_project"])

# tools/base.py 追加

@dataclass
class ExecutionContext:
    """工具执行上下文，替代裸 ctx 参数。"""
    app_context: Any  # AppContext 实例
    task_manager: Any = None  # TaskManager 实例

# execution_engine.py 适配

def _run_single(self, step: dict) -> StepResult:
    tool_name = step["tool"]
    args = step.get("args", {})
    namespace = step.get("namespace")
    spec = ToolRegistry.get(tool_name, namespace=namespace)
    exec_ctx = ExecutionContext(app_context=self.app_context, task_manager=TaskManager())
    result: ToolResult = spec.execute(args, exec_ctx)
    return StepResult(success=result.success, message=result.message, data=result.to_dict())
```

## 实现步骤

### 步骤 1: 实现 `ExecutionContext` 数据类

**涉及文件**: `tools/base.py`（追加）

**实现要点**:
- 简单 dataclass，2 个字段
- `app_context` 绑定到当前 AppContext
- `task_manager` 可延迟初始化（`TaskManager()` 单例）

---

### 步骤 2: 更新 AgentRegistry 预置定义

**涉及文件**: `agents/agent_registry.py`（修改）

**实现要点**:
- 在 `init_presets()` 中新增 4 个 AgentSpec
- 扩展现有 3 个 Agent 的 tools/skills 列表
- 确保新增 Agent 的 namespace 与 ToolRegistry 注册一致

---

### 步骤 3: ExecutionEngine 适配

**涉及文件**: `execution_engine.py`（修改）

**实现要点**:
- `_run_single()` 中组装 `ExecutionContext` 替代裸 `ctx`
- 向后兼容：如果 spec.execute 签名仍为 `(args, ctx)`（v1 工具），则传裸 ctx
- `StepResult` 封装 ToolResult

**边界条件**:
- `namespace` 为 None → ToolRegistry.get 查找所有 namespace（orchestrator 特权）

---

### 步骤 4: Orchestrator 元工具描述

**涉及文件**: `agents/orchestrator.py` 或 `prompts.py`（修改）

**实现要点**:
- 在 orchestrator 的 system_prompt 中，用 7 个元工具描述替代 50+ 完整 schema
- 元工具格式: `{name: "manage_entries", description: "筛选/搜索/编辑/选择词条和标签（editor Agent）", delegate_to: "editor"}`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/base.py` | 修改 | 新增 ExecutionContext |
| `smart_assistant/agents/agent_registry.py` | 修改 | 新增 4 + 扩展 3 Agent 定义 |
| `smart_assistant/execution_engine.py` | 修改 | 适配 ExecutionContext + ToolResult |
| `smart_assistant/agents/orchestrator.py` | 可能修改 | 元工具描述 |

## 风险与注意事项

- **注意**: 向后兼容是关键——v1 工具的 `execute(args, ctx)` 接收裸 `AppContext`，新工具的 `execute(args, exec_ctx)` 接收 `ExecutionContext`。建议在 ExecutionEngine 中通过检测参数数量或使用 try/except 兼容两种签名
- **注意**: 本 Story 是全局收尾，修改后需执行全链路烟雾测试：对话 → LLM → 工具选择 → 工具执行 → 结果返回
