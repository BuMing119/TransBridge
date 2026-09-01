"""GUI process adapter with lazy PyQt import."""

from __future__ import annotations

from typing import Any

from transbridge.application.contracts import OperationResult
from transbridge.application.io import ParseRequest, ParseResult, TranslationIoUseCase
from transbridge.bootstrap.entrypoints import EntrypointBinding, EntrypointOperations


def invoke_operation(binding: EntrypointBinding, command: str) -> OperationResult[Any]:
    """Project a shared application operation without importing PyQt."""

    operations = EntrypointOperations(binding)
    if command == "capabilities":
        return operations.query_capabilities()
    if command == "project-context":
        return operations.require_project_context()
    raise ValueError(f"Unsupported GUI operation: {command}")


def parse_translation_source(use_case: TranslationIoUseCase, request: ParseRequest) -> ParseResult:
    """GUI projection of the same translation I/O parse use case as Agent."""
    return use_case.parse(request)


def main(*, initial_project_path: str | None = None, initial_import_path: str | None = None) -> int:
    from transbridge.ui.app import main as run_gui

    result = run_gui(initial_project_path=initial_project_path, initial_import_path=initial_import_path)
    return 0 if result is None else int(result)
