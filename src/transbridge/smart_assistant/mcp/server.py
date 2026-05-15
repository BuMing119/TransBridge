import hmac
import json
import logging
import secrets
import select
import sys

from .adapter import MCPAdapter

logger = logging.getLogger(__name__)

# m3: stdin 单行最大长度限制 10MB，防止超大消息导致 OOM
_MAX_LINE_LENGTH = 10 * 1024 * 1024


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
        # CR4: 无认证令牌时自动生成随机令牌并输出到 stderr，防止零认证启动
        auth_token = self._config.get("auth_token", "").strip()
        if not auth_token:
            auth_token = secrets.token_hex(32)
            self._config["auth_token"] = auth_token
            print(
                f"[MCP] 未配置认证令牌，已自动生成: {auth_token}\n"
                f"[MCP] 请在 MCP 客户端配置中设置此令牌，或通过 mcp_auth_token 配置项指定。",
                file=sys.stderr, flush=True,
            )
            logger.warning(
                "MCP Server 未配置认证令牌 (auth_token)，已自动生成随机令牌。"
                "请将令牌配置到 MCP 客户端以启用访问控制。"
            )
        logger.info("MCP Server 已启动 (stdio)")
        self._running = True
        # C29: 使用 select.select() 替代阻塞的 for line in sys.stdin，
        # 使 stop() 能在超时时间内检测到 _running = False 并退出循环。
        while self._running:
            # 等待 stdin 可读，超时 0.5s，确保 stop() 调用后能及时退出
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if not ready:
                continue
            line = sys.stdin.readline()
            if not line:
                # EOF — stdin 已关闭
                break
            # m3: 限制单行消息最大长度，防止超大消息导致 OOM
            if len(line) > _MAX_LINE_LENGTH:
                logger.warning("MCP: 单行消息超过大小限制 (%d bytes)，断开连接", _MAX_LINE_LENGTH)
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                # C7: 认证检查
                if not self._authenticate(request):
                    self._write_json({
                        "jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32001, "message": "Unauthorized"},
                    })
                    continue
                response = self._handle_request(request)
                self._write_json(response)
            except json.JSONDecodeError:
                self._write_json({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                })

    @staticmethod
    def _write_json(data: dict) -> None:
        """将 JSON 序列化后写入 stdout 并立即 flush。"""
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def stop(self) -> None:
        self._running = False

    def _authenticate(self, request: dict) -> bool:
        """C7: 验证 MCP 请求的 auth_token。令牌始终必需（启动时自动生成或由配置提供）。"""
        auth_token = self._config.get("auth_token", "").strip()
        if not auth_token:
            # 防御性检查：正常情况下 run_stdio 已确保令牌存在
            logger.error("MCP 认证令牌缺失，拒绝所有请求。")
            return False
        params = request.get("params", {})
        meta = params.get("_meta", {}) if isinstance(params, dict) else {}
        req_token = meta.get("authorization", "")
        # m36: 使用恒定时间比较防止时序攻击
        return hmac.compare_digest(req_token, auth_token)

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
