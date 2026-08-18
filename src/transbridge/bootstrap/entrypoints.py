"""Framework-neutral runtime binding used by entrypoint adapters."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import OperationResult

from .runtime import AppRuntime, RuntimeContext


@dataclass(frozen=True, slots=True)
class EntrypointBinding:
    runtime: AppRuntime
    context: RuntimeContext


class EntrypointOperations:
    """Transport-neutral operations shared by GUI, Agent, CLI and MCP adapters."""

    def __init__(self, binding: EntrypointBinding) -> None:
        self._binding = binding

    @property
    def binding(self) -> EntrypointBinding:
        return self._binding

    def query_capabilities(self) -> OperationResult[dict[str, object]]:
        reports = [report.to_dict() for report in self._binding.runtime.capabilities.snapshot()]
        return OperationResult.completed(
            {"capabilities": reports},
            run_id=self._binding.context.run_id,
        )

    def require_project_context(self) -> OperationResult[dict[str, object]]:
        return self._binding.runtime.require_context(self._binding.context, project=True)


def bind_runtime(
    runtime: AppRuntime,
    owner_id: str,
    *,
    run_id: str | None = None,
    project_id: str | None = None,
    variant_id: str | None = None,
    session_id: str | None = None,
    permissions: frozenset[str] = frozenset(),
    authorized_roots: tuple[str, ...] = (),
    metadata: tuple[tuple[str, str], ...] = (),
) -> EntrypointBinding:
    """Bind an already-built runtime; never create a second runtime implicitly."""

    context = runtime.context(
        owner_id,
        run_id=run_id,
        project_id=project_id,
        variant_id=variant_id,
        session_id=session_id,
        permissions=permissions,
        authorized_roots=authorized_roots,
        metadata=metadata,
    )
    return EntrypointBinding(runtime=runtime, context=context)
