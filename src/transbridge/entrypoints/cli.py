"""Transport-neutral headless CLI operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from transbridge.application.contracts import OperationResult
from transbridge.bootstrap.entrypoints import EntrypointBinding, EntrypointOperations

from .headless import build_headless_binding


def invoke_operation(binding: EntrypointBinding, command: str) -> OperationResult[Any]:
    """Invoke a CLI operation through an already-owned runtime binding."""

    operations = EntrypointOperations(binding)
    if command == "capabilities":
        return operations.query_capabilities()
    if command == "project-context":
        return operations.require_project_context()
    raise ValueError(f"Unsupported CLI operation: {command}")


def run_operation(
    command: str,
    *,
    project_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> OperationResult[Any]:
    """Execute one headless operation and release its process-scoped runtime."""

    binding = build_headless_binding("cli", environ=environ, project_id=project_id)
    try:
        return invoke_operation(binding, command)
    finally:
        binding.runtime.close()
