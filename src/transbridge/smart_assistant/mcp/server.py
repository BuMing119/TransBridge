import json
import logging
import sys

from .adapter import MCPAdapter

logger = logging.getLogger(__name__)


class MCPServer:
    def __init__(self, registry, adapter: MCPAdapter | None = None, ctx=None, config: dict | None = None):
        self._registry = registry
        self._adapter = adapter or MCPAdapter(registry)
        self._ctx = ctx
        if ctx:
            self._adapter.set_context(ctx)
        self._config = config or {}
        self._running = False

    def run_stdio(self) -> None:
        logger.info("MCP Server 已启动 (stdio)")
        self._running = True
        for line in sys.stdin:
            if not self._running:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                # C7: 认证检查
                if not self._authenticate(request):
                    sys.stdout.write(json.dumps({
                        "jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32001, "message": "Unauthorized"},
                    }, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
                    continue
                response = self._handle_request(request)
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }) + "\n")
                sys.stdout.flush()

    def stop(self) -> None:
        self._running = False

    def _authenticate(self, request: dict) -> bool:
        """C7: 验证 MCP 请求的 auth_token。未配置时放行。"""
        auth_token = self._config.get("auth_token", "").strip()
        if not auth_token:
            return True
        params = request.get("params", {})
        meta = params.get("_meta", {}) if isinstance(params, dict) else {}
        req_token = meta.get("authorization", "")
        return req_token == auth_token

    def _handle_request(self, request: dict) -> dict:
        method = request.get("method", "")
        req_id = request.get("id")
        if method == "tools/list":
            return self._jsonrpc_result(req_id, {"tools": self._adapter.list_tools()})
        elif method == "tools/call":
            params = request.get("params", {})
            return self._jsonrpc_result(
                req_id, self._adapter.call_tool(params.get("name", ""), params.get("arguments", {}))
            )
        else:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}

    @staticmethod
    def _jsonrpc_result(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
