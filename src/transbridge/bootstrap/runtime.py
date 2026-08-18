"""Process-scoped application runtime and lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any

from transbridge.application.capabilities import CapabilityRegistry
from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
    map_exception,
)
from transbridge.application.ports import ClockPort, ClosablePort, IdGeneratorPort, SecretPort, SecurityPort
from transbridge.application.tasks import TaskRuntime
from transbridge.application.use_cases import ContextRequirements, ValidateContextUseCase

RuntimeContext = RequestContext


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    clock: ClockPort
    ids: IdGeneratorPort
    secrets: SecretPort
    security: SecurityPort


class UseCaseRegistry:
    """Per-runtime registry; no module-level mutable use-case state."""

    def __init__(self, initial: Mapping[str, Any] | None = None) -> None:
        self._items = dict(initial or {})

    def register(self, name: str, use_case: Any) -> None:
        if not name or not name.strip():
            raise ValueError("use-case name must not be empty")
        self._items[name] = use_case

    def resolve(self, name: str) -> Any:
        try:
            return self._items[name]
        except KeyError as exc:
            raise KeyError(f"Use case is not registered: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


class AppRuntime:
    """The only process-level owner of application dependencies and resources."""

    def __init__(
        self,
        *,
        settings: Mapping[str, Any],
        capabilities: CapabilityRegistry,
        ports: RuntimePorts,
        use_cases: UseCaseRegistry,
        tasks: TaskRuntime,
        resources: tuple[ClosablePort, ...] = (),
        internal_resources: tuple[ClosablePort, ...] = (),
    ) -> None:
        self.settings = _freeze_mapping(settings)
        self.capabilities = capabilities
        self.ports = ports
        self.use_cases = use_cases
        self.tasks = tasks
        self.state: dict[str, Any] = {}
        self._resources = list(resources)
        self._internal_resources = list(internal_resources)
        self._close_lock = RLock()
        self._closed = False
        self._close_result: OperationResult[dict[str, int]] | None = None

    @property
    def closed(self) -> bool:
        with self._close_lock:
            return self._closed

    def context(
        self,
        owner_id: str,
        *,
        run_id: str | None = None,
        project_id: str | None = None,
        variant_id: str | None = None,
        session_id: str | None = None,
        permissions: frozenset[str] = frozenset(),
        authorized_roots: tuple[str, ...] = (),
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> RuntimeContext:
        return RuntimeContext(
            owner_id=owner_id,
            run_id=run_id or self.ports.ids.new_id(),
            project_id=project_id,
            variant_id=variant_id,
            session_id=session_id,
            permissions=permissions,
            authorized_roots=authorized_roots,
            metadata=metadata,
        )

    def require_context(
        self,
        context: RuntimeContext | None,
        *,
        project: bool = False,
        secrets: tuple[str, ...] = (),
    ) -> OperationResult[dict[str, object]]:
        validator: ValidateContextUseCase = self.use_cases.resolve("validate_context")
        return validator.execute(context, ContextRequirements(project=project, secrets=secrets))

    def register_resource(self, resource: ClosablePort) -> None:
        with self._close_lock:
            if self._closed:
                raise RuntimeError("Cannot register a resource after runtime close")
            if not isinstance(resource, ClosablePort):
                raise TypeError("Runtime resources must provide close()")
            self._resources.append(resource)

    def close(self) -> OperationResult[dict[str, int]]:
        """Close resources in reverse order exactly once.

        All resources get a release attempt even when an earlier close fails.
        Concurrent/repeated calls return the first immutable result.
        """

        with self._close_lock:
            if self._close_result is not None:
                return self._close_result
            self._closed = True
            succeeded = 0
            diagnostics: list[Diagnostic] = []
            shutdown_grace = float(self.settings.get("task_shutdown_grace_seconds", 5.0))
            task_shutdown = self.tasks.shutdown(grace=max(0.0, shutdown_grace))
            if not task_shutdown.backend_released:
                diagnostics.append(
                    Diagnostic(
                        code="TASK_RUNTIME_SHUTDOWN_INCOMPLETE",
                        message="Task runtime still has active backend resources after shutdown grace",
                    )
                )
            for resource in reversed(self._resources):
                try:
                    resource.close()
                    succeeded += 1
                except Exception as exc:  # noqa: BLE001 - release remaining resources
                    diagnostics.append(Diagnostic.from_error(map_exception(exc)))
            for resource in reversed(self._internal_resources):
                try:
                    resource.close()
                except Exception as exc:  # noqa: BLE001 - release remaining resources
                    diagnostics.append(Diagnostic.from_error(map_exception(exc)))

            failed = len(diagnostics)
            counts = OperationCounts(succeeded=succeeded, failed=failed)
            value = {"closed": succeeded, "failed": failed}
            if not failed:
                result = OperationResult.completed(value, counts=counts)
            elif succeeded:
                result = OperationResult.partial(value, counts=counts, diagnostics=tuple(diagnostics))
            else:
                result = OperationResult(
                    OperationOutcome.FAILED,
                    diagnostics=tuple(diagnostics),
                    counts=counts,
                )
            self._resources.clear()
            self._internal_resources.clear()
            self._close_result = result
            return result

    shutdown = close


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_value(value) for key, value in values.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value
