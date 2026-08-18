"""Public, GUI-independent application contracts."""

from .context import RequestContext
from .errors import DomainError, ErrorCategory, map_exception
from .jobs import Deferred, JobRef
from .legacy import LegacyToolResult, operation_result_from_tool_result
from .operation import (
    Diagnostic,
    DiagnosticSeverity,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    operation_result_json_schema,
)

__all__ = [
    "Deferred",
    "Diagnostic",
    "DiagnosticSeverity",
    "DomainError",
    "ErrorCategory",
    "JobRef",
    "LegacyToolResult",
    "OperationCounts",
    "OperationOutcome",
    "OperationResult",
    "RequestContext",
    "map_exception",
    "operation_result_from_tool_result",
    "operation_result_json_schema",
]
