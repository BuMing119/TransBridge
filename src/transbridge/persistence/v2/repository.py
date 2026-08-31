"""Root-confined JSON repositories with safe migration and quarantine."""

from __future__ import annotations

import hashlib
import os
from threading import RLock
from typing import cast

from .filesystem import (
    PersistenceFilesystemPort,
    RepositoryPaths,
    staging_replace,
    verified_copy,
)
from .ids import EntityKind, EntityRef, ProjectRef, SessionRef, VariantRef
from .migration import migrate_to_current
from .models import (
    SCHEMA_VERSION,
    FutureSchemaResult,
    LoadedRecord,
    LoadResult,
    MigrationReport,
    PersistenceV2Error,
    ProjectDto,
    QuarantineRef,
    QuarantineResult,
    ReadOnlyWriteRefused,
    SchemaValidationError,
    SessionDto,
    VariantDto,
)
from .schema import parse_json_bytes, serialize_document, validate_v2, version_of

_MUTATION_LOCKS_GUARD = RLock()
_MUTATION_LOCKS: dict[tuple[type[object], str], RLock] = {}


def _mutation_lock_for(root: str, filesystem: PersistenceFilesystemPort) -> RLock:
    """Return the process-wide mutation lock shared by one persistence root.

    Project and Variant repositories are constructed independently by the
    composition root.  Keying the lock by filesystem adapter family and
    canonical root lets their conditional saves participate in one transaction
    boundary, including when equivalent OS adapters are constructed separately.
    """

    key = (type(filesystem), os.path.normcase(filesystem.canonicalize(root)))
    with _MUTATION_LOCKS_GUARD:
        return _MUTATION_LOCKS.setdefault(key, RLock())


class JsonRepository[RefT, DtoT]:
    """Generic implementation behind typed Project/Variant/Session facades."""

    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        *,
        kind: EntityKind,
        dto_type: type[DtoT],
    ) -> None:
        self._filesystem = filesystem
        self._paths = RepositoryPaths(root, filesystem)
        self._kind = kind
        self._dto_type = dto_type
        self._mutation_lock = _mutation_lock_for(root, filesystem)

    @property
    def root(self) -> str:
        return self._paths.root

    @property
    def mutation_lock(self) -> RLock:
        """Lock shared by repositories targeting the same persistence root."""

        return self._mutation_lock

    def path_for(self, ref: RefT) -> str:
        self._check_ref(ref)
        return self._paths.record(ref)

    def read_snapshot(self, ref: RefT) -> DtoT:
        """Validate a record without writing migrations, backups or quarantine files."""

        document = parse_json_bytes(self._filesystem.read_bytes(self.path_for(ref)))
        if version_of(document) < SCHEMA_VERSION:
            document = migrate_to_current(document, ref).document
        return cast(DtoT, validate_v2(document, ref))

    def load(self, ref: RefT) -> LoadResult:
        self._check_ref(ref)
        path = self.path_for(ref)
        raw = self._filesystem.read_bytes(path)
        digest = hashlib.sha256(raw).hexdigest()
        try:
            document = parse_json_bytes(raw)
            version = version_of(document)
        except SchemaValidationError as exc:
            return self._quarantine(ref, path, raw, digest, exc)

        if version > SCHEMA_VERSION:
            identity_error = _validate_future_identity(document, ref)
            if identity_error is not None:
                return self._quarantine(ref, path, raw, digest, identity_error)
            return FutureSchemaResult(ref=ref, found_version=version)

        if version == SCHEMA_VERSION:
            try:
                value = validate_v2(document, ref)
            except SchemaValidationError as exc:
                return self._quarantine(ref, path, raw, digest, exc)
            return LoadedRecord(ref, value, digest)

        backup_path = self._paths.backup(ref, digest, version)
        verified_copy(
            self._filesystem,
            self._paths,
            ref,
            backup_path,
            raw,
            digest=digest,
            purpose="backup",
        )
        try:
            draft = migrate_to_current(document, ref)
            value = validate_v2(draft.document, ref)
        except SchemaValidationError as exc:
            return self._quarantine(ref, path, raw, digest, exc)

        migrated_raw = serialize_document(draft.document)
        migrated_digest = hashlib.sha256(migrated_raw).hexdigest()
        staging_replace(
            self._filesystem,
            self._paths,
            ref,
            path,
            migrated_raw,
            token=migrated_digest,
            purpose="migration",
        )
        report = MigrationReport(
            entity_type=ref.kind,
            identity=ref.identity.value,
            from_version=version,
            to_version=SCHEMA_VERSION,
            original_hash=digest,
            backup_path=backup_path,
            defaults=draft.defaults,
            dropped_fields=draft.dropped_fields,
            conflicts=draft.conflicts,
        )
        return LoadedRecord(
            ref,
            value,
            migrated_digest,
            migrated=True,
            migration_report=report,
        )

    def save(self, ref: RefT, value: DtoT) -> LoadedRecord:
        self._check_ref(ref)
        if not isinstance(value, self._dto_type):
            raise TypeError(f"{self._kind.value} repository received the wrong DTO type")
        document = value.envelope.to_dict()
        if self._kind is EntityKind.PROJECT and version_of(document) == 2:
            document = migrate_to_current(document, ref).document
        validated = validate_v2(document, ref)
        raw = serialize_document(document)
        digest = hashlib.sha256(raw).hexdigest()
        path = self.path_for(ref)
        self._assert_writable_destination(ref, path)
        staging_replace(
            self._filesystem,
            self._paths,
            ref,
            path,
            raw,
            token=digest,
            purpose="save",
        )
        return LoadedRecord(ref, validated, digest)

    def delete(self, ref: RefT) -> None:
        """Delete one validated record after its owning aggregate dropped the reference."""

        self._check_ref(ref)
        path = self.path_for(ref)
        self._assert_writable_destination(ref, path)
        self._filesystem.remove(path, missing_ok=False)

    def restore_bytes_if_match(
        self,
        ref: RefT,
        raw: bytes,
        *,
        expected_revision: int,
        expected_source_hash: str,
    ) -> LoadedRecord:
        """Restore an exact verified preimage only over the expected published bytes."""

        self._check_ref(ref)
        with self._mutation_lock:
            try:
                current = self.load(ref)
            except FileNotFoundError as exc:
                raise RecordContentConflict(expected_revision, expected_source_hash, None, None) from exc
            if not isinstance(current, LoadedRecord):
                raise RecordContentConflict(expected_revision, expected_source_hash, None, None)
            actual_revision = current.value.envelope.revision
            if actual_revision != expected_revision or current.source_hash != expected_source_hash:
                raise RecordContentConflict(
                    expected_revision,
                    expected_source_hash,
                    actual_revision,
                    current.source_hash,
                )
            document = parse_json_bytes(raw)
            restored = validate_v2(document, ref)
            if not isinstance(restored, self._dto_type):
                raise TypeError("restored persistence preimage has the wrong DTO type")
            digest = hashlib.sha256(raw).hexdigest()
            path = self.path_for(ref)
            staging_replace(
                self._filesystem,
                self._paths,
                ref,
                path,
                raw,
                token=digest,
                purpose="restore",
            )
            loaded = self.load(ref)
            if not isinstance(loaded, LoadedRecord) or loaded.source_hash != digest:
                raise RuntimeError("restored persistence preimage could not be verified")
            return loaded

    def _assert_writable_destination(self, ref: RefT, path: str) -> None:
        if not self._filesystem.exists(path):
            return
        existing = self._filesystem.read_bytes(path)
        try:
            document = parse_json_bytes(existing)
            version = version_of(document)
        except SchemaValidationError as exc:
            raise ReadOnlyWriteRefused(
                "INVALID_EXISTING_RECORD",
                "Refusing to overwrite an invalid persistence record.",
            ) from exc
        if version > SCHEMA_VERSION:
            raise ReadOnlyWriteRefused(
                "FUTURE_SCHEMA_READ_ONLY",
                "Refusing to overwrite a future-schema persistence record.",
            )
        if version < SCHEMA_VERSION:
            raise ReadOnlyWriteRefused(
                "MIGRATION_REQUIRED",
                "Load and migrate the legacy record before saving current-schema data.",
            )
        try:
            validate_v2(document, ref)
        except SchemaValidationError as exc:
            raise ReadOnlyWriteRefused(
                "INVALID_EXISTING_RECORD",
                "Refusing to overwrite a mismatched or invalid persistence record.",
            ) from exc

    def _quarantine(
        self,
        ref: RefT,
        source_path: str,
        raw: bytes,
        digest: str,
        error: SchemaValidationError,
    ) -> QuarantineResult:
        quarantine_path = self._paths.quarantine_payload(ref, digest)
        report_path = self._paths.quarantine_report(ref, digest)
        reason = _safe_reason(error)
        report = serialize_document({
            "schema_version": SCHEMA_VERSION,
            "entity_type": "quarantine-report",
            "id": ref.identity.value,
            "revision": 0,
            "data": {
                "source_path": source_path,
                "original_hash": digest,
                "reason_code": error.code,
                "reason": reason,
                "pointer": error.pointer,
                "source_retained": True,
                "recovery": "Keep the source read-only; repair it or restore a verified backup.",
            },
        })
        payload_created = False
        try:
            payload_created = verified_copy(
                self._filesystem,
                self._paths,
                ref,
                quarantine_path,
                raw,
                digest=digest,
                purpose="quarantine-payload",
            )
            verified_copy(
                self._filesystem,
                self._paths,
                ref,
                report_path,
                report,
                digest=hashlib.sha256(report).hexdigest(),
                purpose="quarantine-report",
            )
        except Exception:
            if payload_created:
                try:
                    self._filesystem.remove(quarantine_path, missing_ok=True)
                except Exception:
                    pass
            raise
        return QuarantineResult(
            ref,
            QuarantineRef(quarantine_path, report_path, digest),
            error.code,
            reason,
        )

    def _check_ref(self, ref: EntityRef) -> None:
        if ref.kind is not self._kind:
            raise TypeError(f"{self._kind.value} repository received a {ref.kind.value} reference")


class ProjectRepository(JsonRepository[ProjectRef, ProjectDto]):
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        super().__init__(root, filesystem, kind=EntityKind.PROJECT, dto_type=ProjectDto)

    def save(self, ref: ProjectRef, value: ProjectDto) -> LoadedRecord:
        with self._mutation_lock:
            return super().save(ref, value)

    def save_if_revision(
        self,
        ref: ProjectRef,
        value: ProjectDto,
        *,
        expected_revision: int,
        expected_source_hash: str | None = None,
    ) -> LoadedRecord:
        """Replace one Project only when its persisted revision still matches."""

        with self._mutation_lock:
            try:
                current = super().load(ref)
            except FileNotFoundError as exc:
                raise ProjectRevisionConflict(expected_revision, None) from exc
            if not isinstance(current, LoadedRecord):
                raise ProjectRevisionConflict(expected_revision, None)
            actual_revision = current.value.envelope.revision
            if actual_revision != expected_revision or (
                expected_source_hash is not None and current.source_hash != expected_source_hash
            ):
                raise ProjectRevisionConflict(
                    expected_revision,
                    actual_revision,
                    expected_source_hash=expected_source_hash,
                    actual_source_hash=current.source_hash,
                )
            return super().save(ref, value)

    def delete(self, ref: ProjectRef) -> None:
        with self._mutation_lock:
            super().delete(ref)


class ProjectRevisionConflict(PersistenceV2Error):
    def __init__(
        self,
        expected_revision: int,
        actual_revision: int | None,
        *,
        expected_source_hash: str | None = None,
        actual_source_hash: str | None = None,
    ) -> None:
        super().__init__("persisted Project revision or content changed before conditional save")
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.expected_source_hash = expected_source_hash
        self.actual_source_hash = actual_source_hash


class RecordContentConflict(PersistenceV2Error):
    def __init__(
        self,
        expected_revision: int,
        expected_source_hash: str,
        actual_revision: int | None,
        actual_source_hash: str | None,
    ) -> None:
        super().__init__("persisted record content changed before exact restore")
        self.expected_revision = expected_revision
        self.expected_source_hash = expected_source_hash
        self.actual_revision = actual_revision
        self.actual_source_hash = actual_source_hash


class VariantRepository(JsonRepository[VariantRef, VariantDto]):
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        super().__init__(root, filesystem, kind=EntityKind.VARIANT, dto_type=VariantDto)

    def save(self, ref: VariantRef, value: VariantDto) -> LoadedRecord:
        with self._mutation_lock:
            return super().save(ref, value)

    def save_if_revision(
        self,
        ref: VariantRef,
        value: VariantDto,
        *,
        expected_revision: int,
        expected_source_hash: str | None = None,
    ) -> LoadedRecord:
        """Replace one Variant only when its persisted revision still matches."""

        with self._mutation_lock:
            try:
                current = super().load(ref)
            except FileNotFoundError as exc:
                raise VariantRevisionConflict(expected_revision, None) from exc
            if not isinstance(current, LoadedRecord):
                raise VariantRevisionConflict(expected_revision, None)
            actual_revision = current.value.envelope.revision
            if actual_revision != expected_revision or (
                expected_source_hash is not None and current.source_hash != expected_source_hash
            ):
                raise VariantRevisionConflict(
                    expected_revision,
                    actual_revision,
                    expected_source_hash=expected_source_hash,
                    actual_source_hash=current.source_hash,
                )
            return super().save(ref, value)

    def delete(self, ref: VariantRef) -> None:
        with self._mutation_lock:
            super().delete(ref)


class VariantRevisionConflict(PersistenceV2Error):
    def __init__(
        self,
        expected_revision: int,
        actual_revision: int | None,
        *,
        expected_source_hash: str | None = None,
        actual_source_hash: str | None = None,
    ) -> None:
        super().__init__("persisted Variant revision or content changed before conditional save")
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.expected_source_hash = expected_source_hash
        self.actual_source_hash = actual_source_hash


class SessionRepository(JsonRepository[SessionRef, SessionDto]):
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        super().__init__(root, filesystem, kind=EntityKind.SESSION, dto_type=SessionDto)


def _validate_future_identity(document: dict[str, object], ref: EntityRef) -> SchemaValidationError | None:
    identity = document.get("id")
    entity_type = document.get("entity_type")
    if identity != ref.identity.value:
        return SchemaValidationError(
            "FUTURE_REFERENCE_ID_MISMATCH",
            "Future-schema identity does not match the requested reference.",
            pointer="/id",
        )
    if entity_type != ref.kind.value:
        return SchemaValidationError(
            "FUTURE_ENTITY_TYPE_MISMATCH",
            "Future-schema entity type does not match the repository.",
            pointer="/entity_type",
        )
    return None


def _safe_reason(error: SchemaValidationError) -> str:
    location = error.pointer or "/"
    return f"Persistence validation failed at {location}."


__all__ = [
    "JsonRepository",
    "ProjectRepository",
    "ProjectRevisionConflict",
    "RecordContentConflict",
    "SessionRepository",
    "VariantRevisionConflict",
    "VariantRepository",
]
