import json
import logging
import sys

from .adapter import MCPAdapter

logger = logging.getLogger(__name__)


class MCPServer:
    def __init__(self, registry, adapter: MCPAdapter | None = None, ctx=None):
        self._registry = registry
        self._adapter = adapter or MCPAdapter(registry)
        self._ctx = ctx
        if ctx:
            self._adapter.set_context(ctx)
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
