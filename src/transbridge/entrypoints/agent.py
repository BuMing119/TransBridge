"""Agent adapter over an explicitly injected application runtime binding."""

from __future__ import annotations

from typing import Any

from transbridge.application.contracts import OperationResult
from transbridge.application.io import ParseRequest, ParseResult, TranslationIoUseCase
from transbridge.bootstrap.entrypoints import EntrypointBinding, EntrypointOperations


def invoke_operation(binding: EntrypointBinding, command: str) -> OperationResult[Any]:
    """Invoke the same application contract used by other process adapters."""

    operations = EntrypointOperations(binding)
    if command == "capabilities":
        return operations.query_capabilities()
    if command == "project-context":
        return operations.require_project_context()
    raise ValueError(f"Unsupported Agent operation: {command}")


def parse_translation_source(use_case: TranslationIoUseCase, request: ParseRequest) -> ParseResult:
    """Agent projection of the shared translation I/O parse use case."""
    return use_case.parse(request)
