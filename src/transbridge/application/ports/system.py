"""Framework-neutral protocols used by the application layer."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from transbridge.application.contracts import Deferred, JobRef, OperationResult, RequestContext


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGeneratorPort(Protocol):
    def new_id(self) -> str: ...


@runtime_checkable
class FileSystemPort(Protocol):
    def read_bytes(self, path: str) -> bytes: ...

    def write_bytes_atomic(self, path: str, data: bytes) -> None: ...

    def exists(self, path: str) -> bool: ...


@runtime_checkable
class SecurityPort(Protocol):
    def authorize(self, context: RequestContext, action: str, resource: str | None = None) -> bool: ...


@runtime_checkable
class SecretPort(Protocol):
    """Secret presence/read port; callers must never serialize returned values."""

    def has_secret(self, name: str, context: RequestContext) -> bool: ...

    def get_secret(self, name: str, context: RequestContext) -> str | None: ...


@runtime_checkable
class RepositoryPort[T](Protocol):
    def get(self, identity: str, context: RequestContext) -> T | None: ...

    def save(self, value: T, context: RequestContext) -> None: ...


@runtime_checkable
class FormatPort(Protocol):
    def parse(self, request: Any, context: RequestContext) -> OperationResult[Any]: ...

    def write(self, request: Any, context: RequestContext) -> OperationResult[Any]: ...


@runtime_checkable
class TaskPort(Protocol):
    def submit(self, specification: Any, context: RequestContext) -> Deferred[JobRef]: ...

    def close(self) -> None: ...


@runtime_checkable
class ClosablePort(Protocol):
    def close(self) -> None: ...


def closeables(values: Iterable[Any]) -> tuple[ClosablePort, ...]:
    """Validate resources before adding them to the runtime lifecycle."""

    result: list[ClosablePort] = []
    for value in values:
        if not isinstance(value, ClosablePort):
            raise TypeError(f"Runtime resource {type(value).__name__} does not provide close()")
        result.append(value)
    return tuple(result)
