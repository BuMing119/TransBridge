"""MCP transport adapter over application entrypoint operations."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
from typing import Any

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    operation_result_from_tool_result,
)
from transbridge.application.security import SecretRedactor
from transbridge.bootstrap.entrypoints import EntrypointBinding, EntrypointOperations

from ..tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPInvalidParams(ValueError):
    """A tool invocation cannot be mapped to an application operation."""


_FOUNDATION_TOOLS = (
    {
        "name": "transbridge.capabilities",
        "description": "Return structured availability reports for this headless runtime.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "transbridge.project-context",
        "description": "Validate that the request has an explicit Project context.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
)


class MCPAdapter:
    """Maps MCP tool payloads to shared application operations.

    ``registry/config/ctx`` remain accepted for the historical import facade.
    The supported process topology supplies ``binding`` and never consumes GUI
    memory or constructs a TaskManager singleton.
    """

    def __init__(
        self,
        registry: type[ToolRegistry] | Any | None = None,
        config: dict | None = None,
        ctx: Any = None,
        *,
        binding: EntrypointBinding | None = None,
    ) -> None:
        self._binding = binding
        self._operations = EntrypointOperations(binding) if binding is not None else None
        self._registry = registry or ToolRegistry
        self._legacy_ctx = ctx
        cfg = config or {}
        self._admin_whitelist = frozenset(
            value.strip() for value in cfg.get("admin_tool_whitelist", "").split(",") if value.strip()
        )
        self._write_policy = str(cfg.get("write_tool_policy", "deny"))

    @property
    def strict_lifecycle(self) -> bool:
        return self._binding is not None

    def set_context(self, ctx: Any) -> None:
        """Compatibility facade for callers not yet migrated to RuntimeContext."""

        self._legacy_ctx = ctx

    def list_tools(self) -> list[dict[str, Any]]:
        if self._operations is not None:
            return deepcopy(list(_FOUNDATION_TOOLS))

        tools: list[dict[str, Any]] = []
        for spec in self._registry.list_all():
            if not getattr(spec, "available", True) or not self._is_exposed(spec):
                continue
            tools.append({
                "name": spec.name,
                "description": spec.description,
                "inputSchema": self._build_json_schema(spec.parameters),
            })
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise MCPInvalidParams("tool arguments must be an object")
        if self._operations is not None:
            if arguments:
                raise MCPInvalidParams(f"{name} does not accept arguments")
            if name == "transbridge.capabilities":
                result = self._operations.query_capabilities()
            elif name == "transbridge.project-context":
                result = self._operations.require_project_context()
            else:
                raise MCPInvalidParams(f"unknown tool: {name}")
            return self._operation_payload(result)
        return self._call_legacy_tool(name, arguments)

    def _call_legacy_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._registry.get(name, namespace=None)
        if spec is None or not getattr(spec, "available", True):
            raise MCPInvalidParams(f"unknown tool: {name}")
        if not self._is_exposed(spec):
            error = DomainError(
                ErrorCategory.PERMISSION,
                "TOOL_NOT_EXPOSED",
                "The tool is not exposed by this transport.",
            )
            return self._operation_payload(OperationResult.failed(error))
        if self._legacy_ctx is None:
            error = DomainError(
                ErrorCategory.PREREQUISITE,
                "RUNTIME_CONTEXT_REQUIRED",
                "The compatibility adapter requires an injected context.",
            )
            return self._operation_payload(OperationResult.failed(error))
        try:
            from ..tools.base import ExecutionContext, execute_with_guardrails

            execution_context = ExecutionContext(
                app_context=self._legacy_ctx,
                request_context=getattr(self._legacy_ctx, "request_context", None),
                owner_id=getattr(self._legacy_ctx, "owner_id", "legacy:mcp"),
            )
            legacy_result = execute_with_guardrails(spec, arguments, execution_context)
            result = operation_result_from_tool_result(legacy_result)
        except Exception as exc:  # noqa: BLE001 - map at transport boundary
            logger.warning("MCP legacy tool failed safely: %s", type(exc).__name__)
            result = OperationResult.from_exception(exc)
        return self._operation_payload(result)

    @staticmethod
    def _operation_payload(result: OperationResult[Any]) -> dict[str, Any]:
        structured = SecretRedactor.default().redact(result.to_dict())
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                }
            ],
            "structuredContent": structured,
            "isError": result.outcome is not OperationOutcome.COMPLETED,
        }

    def _is_exposed(self, spec: Any) -> bool:
        permission = getattr(spec, "permission", "read")
        if permission == "admin" or getattr(spec, "require_confirmation", False):
            return False
        if permission == "write":
            return self._write_policy == "allow"
        return permission == "read"

    @staticmethod
    def _build_json_schema(parameters: dict[str, Any]) -> dict[str, Any]:
        """ToolSpec parameters are canonical JSON Schema since S04."""

        return deepcopy(parameters)
