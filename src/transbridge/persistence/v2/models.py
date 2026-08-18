"""V2 persistence DTOs and structured repository outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ids import EntityKind, EntityRef

SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class SchemaEnvelope:
    schema_version: int
    entity_type: EntityKind
    identity: str
    revision: int
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "entity_type": self.entity_type.value,
            "id": self.identity,
            "revision": self.revision,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class ProjectDto:
    envelope: SchemaEnvelope


@dataclass(frozen=True, slots=True)
class VariantDto:
    envelope: SchemaEnvelope


@dataclass(frozen=True, slots=True)
class SessionDto:
    envelope: SchemaEnvelope


PersistenceDto = ProjectDto | VariantDto | SessionDto


@dataclass(frozen=True, slots=True)
class MigrationReport:
    entity_type: EntityKind
    identity: str
    from_version: int
    to_version: int
    original_hash: str
    backup_path: str
    defaults: tuple[str, ...] = ()
    dropped_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedRecord:
    ref: EntityRef
    value: PersistenceDto
    source_hash: str
    migrated: bool = False
    migration_report: MigrationReport | None = None


@dataclass(frozen=True, slots=True)
class QuarantineRef:
    path: str
    report_path: str
    original_hash: str
    source_retained: bool = True


@dataclass(frozen=True, slots=True)
class QuarantineResult:
    ref: EntityRef
    quarantine: QuarantineRef
    reason_code: str
    reason: str
    recovery: str = "Keep the original read-only; repair or restore from a verified backup."


@dataclass(frozen=True, slots=True)
class FutureSchemaResult:
    ref: EntityRef
    found_version: int
    supported_version: int = SCHEMA_VERSION
    read_only: bool = True
    reason_code: str = "FUTURE_SCHEMA_READ_ONLY"


LoadResult = LoadedRecord | QuarantineResult | FutureSchemaResult


@dataclass(frozen=True, slots=True)
class MigrationDraft:
    document: dict[str, Any]
    defaults: tuple[str, ...] = ()
    dropped_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


class PersistenceV2Error(RuntimeError):
    """Base persistence adapter error."""


class SchemaValidationError(PersistenceV2Error):
    def __init__(self, code: str, message: str, *, pointer: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.pointer = pointer


class PathBoundaryError(PersistenceV2Error):
    pass


class BackupVerificationError(PersistenceV2Error):
    pass


class AtomicWriteError(PersistenceV2Error):
    pass


class ReadOnlyWriteRefused(PersistenceV2Error):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


__all__ = [
    "AtomicWriteError",
    "BackupVerificationError",
    "FutureSchemaResult",
    "LoadedRecord",
    "LoadResult",
    "MigrationDraft",
    "MigrationReport",
    "PathBoundaryError",
    "PersistenceDto",
    "PersistenceV2Error",
    "ProjectDto",
    "QuarantineRef",
    "QuarantineResult",
    "ReadOnlyWriteRefused",
    "SCHEMA_VERSION",
    "SchemaEnvelope",
    "SchemaValidationError",
    "SessionDto",
    "VariantDto",
]
