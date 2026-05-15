import logging

from ..tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPAdapter:
    def __init__(self, registry: type[ToolRegistry] | None = None, config: dict | None = None,
                 ctx=None):
        self._registry = registry or ToolRegistry
        cfg = config or {}
        self._admin_whitelist: list[str] = [
            t.strip() for t in cfg.get("admin_tool_whitelist", "").split(",") if t.strip()
        ]
        self._write_policy: str = cfg.get("write_tool_policy", "deny")
        self._ctx = ctx
        # M46: 持有 TaskManager 单例引用，避免每次 call_tool 都重新获取 TaskManager()
        from ..tools.task_manager import TaskManager
        self._tm = TaskManager()

    def set_context(self, ctx) -> None:
        self._ctx = ctx

    def list_tools(self) -> list[dict]:
        tools = []
        for spec in self._registry.list_all():
            if not self._is_exposed(spec):
                continue
            desc = spec.description
            # 标注 admin 工具：MCP 通道不支持 HITL 确认，admin 工具虽可见
            # 但执行时会被 PermissionGuard 拒绝
            if getattr(spec, 'permission', 'read') == "admin":
                desc = f"[仅列表可见，MCP 不支持执行] {desc}"
            tools.append({
                "name": spec.name,
                "description": desc,
                "inputSchema": self._build_json_schema(spec.parameters),
            })
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        # M47: app_context 为空时提前报错，防止工具收到 None 后触发 AttributeError
        if self._ctx is None:
            return {
                "content": [{"type": "text",
                             "text": "MCP 适配器未初始化: app_context 为 None，请先调用 set_context()"}],
                "isError": True,
            }
        spec = self._registry.get(name, namespace=None)
        if spec is None:
            return {"content": [{"type": "text", "text": f"工具不存在: {name}"}], "isError": True}
        if not self._is_exposed(spec):
            return {"content": [{"type": "text", "text": f"工具未暴露: {name}"}], "isError": True}
        try:
            # B6: MCP 通道接入 GuardChain，与 GUI 共享同一条中间件链
            from ..tools.base import execute_with_guardrails, ExecutionContext
            exec_ctx = ExecutionContext(app_context=self._ctx, task_manager=self._tm)  # M46: 缓存单例，避免每次重新获取
            result = execute_with_guardrails(spec, arguments, exec_ctx)
            if isinstance(result, dict):
                return {"content": [{"type": "text", "text": result.get("message", "")}]}
            return {"content": [{"type": "text", "text": result.message}]}
        except Exception as exc:
            logger.warning("MCP 工具执行失败: %s", exc)
            return {"content": [{"type": "text", "text": f"执行异常: {exc}"}], "isError": True}

    def _is_exposed(self, spec) -> bool:
        """检查工具是否对 MCP 通道暴露。

        M11 TODO: MCP 通道不支持 HITL 确认。即使 admin 工具在
        admin_tool_whitelist 中且通过 _is_exposed 可见性检查，
        PermissionGuard 仍会返回 requires_confirmation="admin"
        的 GuardResult，execute_with_guardrails 会将其视为硬阻断
        （因为 MCP 无法弹出 UI 确认框）。
        需要架构层面解决：MCP 通道中白名单内工具应跳过 PermissionGuard
        的 admin 阻断，或明确文档化 MCP 不支持 admin 工具。
        当前 _is_exposed 仅控制工具列表可见性，不绕过权限检查。
        """
        perm = getattr(spec, 'permission', 'read')
        if perm == "admin":
            return spec.name in self._admin_whitelist
        if perm == "write" and self._write_policy == "deny":
            return False
        return True

    def _build_json_schema(self, parameters: dict) -> dict:
        properties = {}
        for key, info in parameters.items():
            properties[key] = {
                "type": info.get("type", "string"),
                "description": info.get("description", ""),
            }
        # m2: 仅将 required=True 的参数加入 required 列表
        # 默认 required=False，避免强制可选参数变为必填
        required = [k for k, info in parameters.items() if info.get("required", False)]
        return {"type": "object", "properties": properties, "required": required}
