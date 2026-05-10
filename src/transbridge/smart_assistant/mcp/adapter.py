import logging

from ..tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPAdapter:
    def __init__(self, registry: type[ToolRegistry] | None = None, config: dict | None = None):
        self._registry = registry or ToolRegistry
        cfg = config or {}
        self._admin_whitelist: list[str] = [
            t.strip() for t in cfg.get("admin_tool_whitelist", "").split(",") if t.strip()
        ]
        self._write_policy: str = cfg.get("write_tool_policy", "deny")
        self._ctx = None

    def set_context(self, ctx) -> None:
        self._ctx = ctx

    def list_tools(self) -> list[dict]:
        tools = []
        for spec in self._registry.list_all():
            if not self._is_exposed(spec):
                continue
            tools.append({
                "name": spec.name,
                "description": spec.description,
                "inputSchema": self._build_json_schema(spec.parameters),
            })
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        spec = self._registry.get(name, namespace=None)
        if spec is None:
            return {"content": [{"type": "text", "text": f"工具不存在: {name}"}], "isError": True}
        if not self._is_exposed(spec):
            return {"content": [{"type": "text", "text": f"工具未暴露: {name}"}], "isError": True}
        try:
            result = spec.execute(arguments, self._ctx)
            return {"content": [{"type": "text", "text": result.get("message", "")}]}
        except Exception as exc:
            logger.warning("MCP 工具执行失败: %s", exc)
            return {"content": [{"type": "text", "text": f"执行异常: {exc}"}], "isError": True}

    def _is_exposed(self, spec) -> bool:
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
        return {"type": "object", "properties": properties, "required": list(parameters.keys())}
