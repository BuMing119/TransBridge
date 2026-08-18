"""MCP JSON-RPC stdio server with an explicit lifecycle state machine."""

from __future__ import annotations

from collections.abc import Callable
import hmac
import json
import logging
import math
import sys
from typing import Any, TextIO

from transbridge import __version__
from transbridge.application.security import SecretRedactor

from .adapter import MCPAdapter, MCPInvalidParams

logger = logging.getLogger(__name__)

_MAX_LINE_LENGTH = 10 * 1024 * 1024
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")


class MCPServer:
    """Line-delimited stdio transport; Windows-safe and independent of PyQt."""

    def __init__(
        self,
        registry: Any = None,
        adapter: MCPAdapter | None = None,
        ctx: Any = None,
        config: dict | None = None,
        *,
        on_close: Callable[[], Any] | None = None,
    ) -> None:
        self._adapter = adapter or MCPAdapter(registry, config=config, ctx=ctx)
        self._registry = registry
        self._ctx = ctx
        self._config = dict(config or {})
        self._strict_lifecycle = self._adapter.strict_lifecycle
        self._initialized = False
        self._initialize_seen = False
        self._running = False
        self._shutdown_requested = False
        self._output: TextIO | None = None
        self._on_close = on_close
        self._closed = False

    def run_stdio(
        self,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        """Run until protocol shutdown or EOF; no ``select(stdin)`` dependency."""

        source = input_stream or sys.stdin
        self._output = output_stream or sys.stdout
        self._running = True
        try:
            for line in source:
                if not self._running:
                    break
                if len(line) > _MAX_LINE_LENGTH:
                    self._write_error(None, -32600, "Request exceeds the transport limit")
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    request = json.loads(stripped, parse_constant=_reject_non_finite)
                except (json.JSONDecodeError, ValueError):
                    self._write_error(None, -32700, "Parse error")
                    continue
                response = self._dispatch(request)
                if response is not None:
                    self._write_json(response)
                if self._shutdown_requested:
                    break
        finally:
            self._running = False
            self.close()

    def stop(self) -> None:
        """Request loop termination; blocking reads end when the stream reaches EOF."""

        self._running = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            try:
                self._on_close()
            except Exception as exc:  # noqa: BLE001 - lifecycle must stay protocol-safe
                logger.error("MCP runtime close failed safely: %s", type(exc).__name__)

    def _dispatch(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error(None, -32600, "Invalid Request")
        is_notification = "id" not in request
        if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            if is_notification:
                return None
            return self._error(request.get("id"), -32600, "Invalid Request")
        if not is_notification and not _valid_request_id(request.get("id")):
            return self._error(None, -32600, "Invalid Request")
        if self._config.get("legacy_auth_required") and not self._authenticate(request):
            if is_notification:
                return None
            return self._error(request.get("id"), -32001, "Unauthorized")
        try:
            return self._handle_request(request)
        except MCPInvalidParams as exc:
            if is_notification:
                logger.warning("MCP notification rejected safely: %s", type(exc).__name__)
                return None
            message = SecretRedactor.default().redact_text(str(exc))
            return self._error(request.get("id"), -32602, message)
        except Exception as exc:  # noqa: BLE001 - never expose raw transport exceptions
            logger.error("MCP request failed safely: %s", type(exc).__name__)
            if is_notification:
                return None
            return self._error(request.get("id"), -32603, "Internal error")

    def _handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request["method"]
        request_id = request.get("id")
        is_notification = "id" not in request

        if method == "initialize":
            if is_notification or self._initialize_seen:
                raise MCPInvalidParams("initialize must be one request at process start")
            params = self._params(request)
            requested = params.get("protocolVersion")
            if not isinstance(requested, str):
                raise MCPInvalidParams("initialize.protocolVersion must be a string")
            negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            self._initialize_seen = True
            return self._result(
                request_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "TransBridge", "version": __version__},
                },
            )

        if method == "notifications/initialized":
            if not is_notification or not self._initialize_seen or self._initialized:
                raise MCPInvalidParams("initialized notification is out of sequence")
            self._initialized = True
            return None

        if self._strict_lifecycle and not self._initialized:
            return self._error(request_id, -32002, "Server is not initialized")

        if method == "tools/list":
            if is_notification:
                return None
            self._params(request)
            return self._result(request_id, {"tools": self._adapter.list_tools()})

        if method == "tools/call":
            if is_notification:
                return None
            params = self._params(request)
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not name:
                raise MCPInvalidParams("tools/call.name must be a non-empty string")
            if not isinstance(arguments, dict):
                raise MCPInvalidParams("tools/call.arguments must be an object")
            return self._result(request_id, self._adapter.call_tool(name, arguments))

        if method == "shutdown":
            if is_notification:
                return None
            self._params(request)
            self._shutdown_requested = True
            return self._result(request_id, None)

        if method in {"notifications/cancelled", "exit"} and is_notification:
            if method == "exit":
                self._shutdown_requested = True
            return None

        if is_notification:
            return None
        return self._error(request_id, -32601, "Method not found")

    @staticmethod
    def _params(request: dict[str, Any]) -> dict[str, Any]:
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise MCPInvalidParams("params must be an object")
        return params

    def _authenticate(self, request: dict) -> bool:
        """Historical facade only; primary stdio topology does not use HTTP auth."""

        auth_token = str(self._config.get("auth_token", "")).strip()
        if not auth_token:
            return False
        params = request.get("params", {})
        metadata = params.get("_meta", {}) if isinstance(params, dict) else {}
        requested = metadata.get("authorization", "") if isinstance(metadata, dict) else ""
        return hmac.compare_digest(str(requested), auth_token)

    def _write_error(self, request_id: Any, code: int, message: str) -> None:
        self._write_json(self._error(request_id, code, message))

    def _write_json(self, data: dict[str, Any]) -> None:
        if self._output is None:
            self._output = sys.stdout
        self._output.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._output.flush()

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    # Compatibility aliases used by older direct tests/callers.
    _jsonrpc_result = _result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _valid_request_id(value: Any) -> bool:
    if value is None or isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)
