"""Compatibility mapping for legacy entrypoint DTOs.

The mapper is structural on purpose: the application contract must not import
Smart Assistant, GUI, or MCP modules. Entrypoint adapters may pass their existing
ToolResult-like value while each call chain is migrated.
"""

from __future__ import annotations

from typing import Any, Protocol

from .errors import DomainError, ErrorCategory
from .operation import (
    Diagnostic,
    DiagnosticSeverity,
    OperationCounts,
    OperationResult,
)


class LegacyToolResult(Protocol):
    success: bool
    partial: bool
    message: str
    data: dict[str, Any] | None
    failed_items: list[dict[str, Any]] | None
    truncated: bool
    error_category: str | None
    error_code: str | None
    recovery_action: str | None
    warnings: list[str] | None
    pagination: dict[str, Any] | None
    execution_meta: dict[str, Any] | None
    tool_suggestions: list[str] | None


_LEGACY_ERROR_CATEGORIES = {
    "network": ErrorCategory.EXTERNAL,
    "external": ErrorCategory.EXTERNAL,
    "auth": ErrorCategory.PERMISSION,
    "permission": ErrorCategory.PERMISSION,
    "input": ErrorCategory.INPUT,
    "config": ErrorCategory.PREREQUISITE,
    "prerequisite": ErrorCategory.PREREQUISITE,
    "conflict": ErrorCategory.CONFLICT,
    "cancelled": ErrorCategory.CANCELLED,
    "internal": ErrorCategory.INTERNAL,
}


def operation_result_from_tool_result(
    result: LegacyToolResult,
    *,
    run_id: str | None = None,
    succeeded_count: int | None = None,
) -> OperationResult[dict[str, Any]]:
    """Losslessly map a legacy ToolResult-shaped DTO into the canonical result.

    Legacy partial DTOs do not always provide an item success count. Callers may
    pass the known count; otherwise one aggregate successful operation is used.
    The original structured fields remain in ``value`` (or failed diagnostic
    details), so display adapters can reproduce the previous observation.
    """

    payload = _legacy_payload(result)
    warnings = tuple(
        Diagnostic(
            "LEGACY_TOOL_WARNING",
            warning,
            severity=DiagnosticSeverity.WARNING,
        )
        for warning in (result.warnings or ())
    )
    failed_items = result.failed_items or []

    if result.partial:
        diagnostic = Diagnostic(
            result.error_code or "LEGACY_PARTIAL_RESULT",
            result.message or "The operation completed partially.",
            category=_legacy_category(result.error_category),
            details=(("failed_items", failed_items),),
        )
        return OperationResult.partial(
            payload,
            counts=OperationCounts(
                succeeded=succeeded_count if succeeded_count is not None else 1,
                failed=max(1, len(failed_items)),
            ),
            diagnostics=(*warnings, diagnostic),
            run_id=run_id,
        )

    if result.success:
        return OperationResult.completed(
            payload,
            counts=OperationCounts(succeeded=succeeded_count if succeeded_count is not None else 1),
            diagnostics=warnings,
            run_id=run_id,
        )

    category = _legacy_category(result.error_category, default=ErrorCategory.INTERNAL)
    if category is ErrorCategory.CANCELLED:
        diagnostic = Diagnostic(
            result.error_code or "OPERATION_CANCELLED",
            result.message or "The operation was cancelled.",
            category=category,
            details=(("legacy_result", payload),),
        )
        return OperationResult.cancelled(diagnostic, run_id=run_id)
    public_message = (
        "The tool operation failed."
        if category is ErrorCategory.INTERNAL
        else result.message or "The tool operation failed."
    )
    error = DomainError(
        category,
        result.error_code or "LEGACY_TOOL_FAILED",
        public_message,
        details={} if category is ErrorCategory.INTERNAL else {"legacy_result": payload},
    )
    return OperationResult.failed(error, run_id=run_id)


def _legacy_payload(result: LegacyToolResult) -> dict[str, Any]:
    if result.partial:
        presentation_status = "[PARTIAL]"
    elif result.success:
        presentation_status = "[OK]"
    else:
        presentation_status = "[FAIL]"
    return {
        "presentation_status": presentation_status,
        "message": result.message,
        "data": result.data,
        "failed_items": result.failed_items,
        "truncated": result.truncated,
        "error_category": result.error_category,
        "error_code": result.error_code,
        "recovery_action": result.recovery_action,
        "warnings": result.warnings,
        "pagination": result.pagination,
        "execution_meta": result.execution_meta,
        "tool_suggestions": result.tool_suggestions,
    }


def _legacy_category(value: str | None, *, default: ErrorCategory | None = None) -> ErrorCategory | None:
    return _LEGACY_ERROR_CATEGORIES.get(value or "", default)
