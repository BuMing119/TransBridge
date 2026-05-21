# Story 16: Agent 死代码清理 + 注册样板消除

**Epic**: agent-tool-expansion
**优先级**: P0
**净减**: -194 行死代码 + -35 行样板
**风险**: 零
**依赖**: 无（可立即开始）
**状态**: 已方案

## 范围

删除从未在生产环境运行的 Agent 调度层死代码，消除 7 个工具模块中重复的注册样板代码。

## 验收标准

- [ ] 删除 `agents/orchestrator.py`（~120 行）和 `agents/agent_worker.py`（~72 行）
- [ ] 从 `agents/__init__.py` 移除 `Orchestrator` 和 `AgentWorker` 的 import 和 `__all__` 导出
- [ ] 7 个工具模块（tool_editor/translator/writer/parser/proofreader/paratranz/default）的 `_register_*_tools()` 改为调用 `ToolRegistry.register_tools(namespace, tools_list)` 批量注册
- [ ] `register_tools()` 接受 `list[dict]` 格式：`[{"name": "...", "display_name": "...", "description": "...", "execute": fn, "permission": "...", ...}, ...]`
- [ ] 所有现有工具注册不受影响，工具总数仍为 56
- [ ] 现有测试全部通过

## 实现步骤

1. 删除 `agents/orchestrator.py` 和 `agents/agent_worker.py`
2. 更新 `agents/__init__.py`：移除 `Orchestrator`/`AgentWorker` import 和导出，只保留 `AgentSpec`, `AgentInstance`, `AgentRegistry`
3. 在 `ToolRegistry` 中实现 `register_tools(namespace, tools: list[dict])` 类方法——内部遍历 tools 列表，为每个 dict 构造 `ToolSpec` 并调用已有的 `register()`
4. 逐个改造 7 个模块的 `_register_*_tools()`：将手动元组循环改为 `ToolRegistry.register_tools("namespace", [{"name": ..., ...}, ...])`
5. 运行现有测试验证（`pytest tests/ -k "tool"`）

## 涉及文件

- 删除: `agents/orchestrator.py`, `agents/agent_worker.py`
- 修改: `agents/__init__.py`, `tool_registry.py`, `tools/tool_editor.py`, `tools/tool_translator.py`, `tools/tool_writer.py`, `tools/tool_parser.py`, `tools/tool_proofreader.py`, `tools/tool_paratranz.py`, `tools/tool_default.py`

## 注册样板改造格式

**改造前** (tool_writer.py 示例):
```python
def _register_writer_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
    tools = [
        ("write_to_esp", "写回ESP", "...", _tool_write_to_esp, "admin"),
        ...
    ]
    for name, display_name, description, execute, permission in tools:
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters=_PARAM_SCHEMAS.get(name, {}), execute=execute, permission=permission,
            require_confirmation=True, is_long_running=True,
        ), namespace="writer")
```

**改造后**:
```python
def _register_writer_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    ToolRegistry.register_tools("writer", [
        {"name": "write_to_esp", "display_name": "写回ESP", "description": "...",
         "execute": _tool_write_to_esp, "permission": "admin",
         "parameters": _PARAM_SCHEMAS.get("write_to_esp", {}),
         "require_confirmation": True, "is_long_running": True},
        ...
    ])
```
