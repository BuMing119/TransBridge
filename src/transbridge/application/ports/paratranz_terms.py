"""Typed application contracts for ParaTranz terminology operations."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import re
from types import MappingProxyType
from typing import Any, Protocol

from transbridge.ai_translator.term_formats import TermEntry

from .paratranz import CancellationPort

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _digest(value: object, name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _revision(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string or null")
    return value.strip()


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in deepcopy(dict(value)).items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _copy_entry(entry: object) -> TermEntry:
    if not isinstance(entry, TermEntry):
        raise TypeError("entry must be a TermEntry")
    copied = deepcopy(entry)
    copied.term = copied.term.strip()
    copied.translation = copied.translation.strip()
    if not copied.term or not copied.translation:
        raise ValueError("entry term and translation must be non-empty")
    return copied


class TermWriteOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class TermWriteStatus(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParaTranzTerm:
    remote_id: int
    entry: TermEntry
    server_revision: str | None
    observed_digest: str
    readonly_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "remote_id", _positive_integer(self.remote_id, "remote_id"))
        object.__setattr__(self, "entry", _copy_entry(self.entry))
        object.__setattr__(self, "server_revision", _revision(self.server_revision, "server_revision"))
        object.__setattr__(self, "observed_digest", _digest(self.observed_digest, "observed_digest"))
        if not isinstance(self.readonly_metadata, Mapping):
            raise TypeError("readonly_metadata must be a mapping")
        object.__setattr__(self, "readonly_metadata", _freeze_json(self.readonly_metadata))


@dataclass(frozen=True, slots=True)
class ParaTranzTermPage:
    items: tuple[ParaTranzTerm, ...]
    page: int
    page_size: int
    has_next: bool
    snapshot_revision: str | None
    page_digest: str

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not all(isinstance(item, ParaTranzTerm) for item in items):
            raise TypeError("items must contain ParaTranzTerm values")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "page", _positive_integer(self.page, "page"))
        object.__setattr__(self, "page_size", _positive_integer(self.page_size, "page_size"))
        if not isinstance(self.has_next, bool):
            raise TypeError("has_next must be a boolean")
        object.__setattr__(self, "snapshot_revision", _revision(self.snapshot_revision, "snapshot_revision"))
        object.__setattr__(self, "page_digest", _digest(self.page_digest, "page_digest"))


@dataclass(frozen=True, slots=True)
class ParaTranzTermSnapshot:
    project_id: int
    items: tuple[ParaTranzTerm, ...]
    observed_digest: str
    observed_at: datetime
    stable: bool
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _positive_integer(self.project_id, "project_id"))
        items = tuple(sorted(self.items, key=lambda item: item.remote_id))
        if not all(isinstance(item, ParaTranzTerm) for item in items):
            raise TypeError("items must contain ParaTranzTerm values")
        remote_ids = [item.remote_id for item in items]
        if len(remote_ids) != len(set(remote_ids)):
            raise ValueError("snapshot items must have unique remote ids")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "observed_digest", _digest(self.observed_digest, "observed_digest"))
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be a timezone-aware datetime")
        if self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must have a usable timezone offset")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))
        if not isinstance(self.stable, bool):
            raise TypeError("stable must be a boolean")
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, str) and item.strip() for item in diagnostics):
            raise ValueError("diagnostics must contain non-empty strings")
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True, slots=True)
class ParaTranzTermWrite:
    entry: TermEntry
    operation: TermWriteOperation
    remote_id: int | None = None
    expected_revision: str | None = None
    expected_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry", _copy_entry(self.entry))
        try:
            operation = TermWriteOperation(self.operation)
        except (TypeError, ValueError):
            raise ValueError("operation must be create or update") from None
        if operation is TermWriteOperation.DELETE:
            raise ValueError("ParaTranzTermWrite represents create/update payloads only")
        object.__setattr__(self, "operation", operation)
        if operation is TermWriteOperation.CREATE:
            if self.remote_id is not None:
                raise ValueError("create write must not include remote_id")
        else:
            object.__setattr__(self, "remote_id", _positive_integer(self.remote_id, "remote_id"))
        object.__setattr__(self, "expected_revision", _revision(self.expected_revision, "expected_revision"))
        object.__setattr__(self, "expected_digest", _digest(self.expected_digest, "expected_digest", optional=True))


@dataclass(frozen=True, slots=True)
class ParaTranzTermWriteResult:
    operation: TermWriteOperation
    remote_id: int | None
    server_revision: str | None
    observed_digest: str | None
    request_id: str | None
    status: TermWriteStatus
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            operation = TermWriteOperation(self.operation)
            status = TermWriteStatus(self.status)
        except (TypeError, ValueError):
            raise ValueError("invalid write result operation or status") from None
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "status", status)
        if self.remote_id is not None:
            object.__setattr__(self, "remote_id", _positive_integer(self.remote_id, "remote_id"))
        if status is TermWriteStatus.CONFIRMED and self.remote_id is None:
            raise ValueError("confirmed write result must include remote_id")
        object.__setattr__(self, "server_revision", _revision(self.server_revision, "server_revision"))
        object.__setattr__(self, "observed_digest", _digest(self.observed_digest, "observed_digest", optional=True))
        object.__setattr__(self, "request_id", _revision(self.request_id, "request_id"))
        diagnostics = tuple(self.diagnostics)
        if not all(isinstance(item, str) and item.strip() for item in diagnostics):
            raise ValueError("diagnostics must contain non-empty strings")
        object.__setattr__(self, "diagnostics", diagnostics)

    @property
    def confirmed(self) -> bool:
        return self.status is TermWriteStatus.CONFIRMED


class ParaTranzTerminologyPort(Protocol):
    def snapshot_terms(
        self,
        project_id: int,
        *,
        page_size: int = 200,
        max_terms: int = 100_000,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermSnapshot: ...

    def create_term(
        self,
        project_id: int,
        write: ParaTranzTermWrite,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult: ...

    def update_term(
        self,
        project_id: int,
        write: ParaTranzTermWrite,
        *,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult: ...

    def delete_term(
        self,
        project_id: int,
        remote_id: int,
        *,
        expected_revision: str | None = None,
        expected_digest: str | None = None,
        cancellation: CancellationPort | None = None,
    ) -> ParaTranzTermWriteResult: ...


__all__ = [
    "ParaTranzTerm",
    "ParaTranzTermPage",
    "ParaTranzTermSnapshot",
    "ParaTranzTermWrite",
    "ParaTranzTermWriteResult",
    "ParaTranzTerminologyPort",
    "TermWriteOperation",
    "TermWriteStatus",
]
