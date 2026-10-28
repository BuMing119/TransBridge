"""Root-confined JSON repositories with safe migration and quarantine."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import os
from threading import RLock
from typing import cast

from .filesystem import (
    OsPersistenceFilesystem,
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
from .project_record_guard import record_matches
from .project_write_lock import ProjectWriteLock
from .schema import parse_json_bytes, serialize_document, validate_v2, version_of
from .session_write_lease import session_write_lease
from .validated_reads import decode_validated_read, remember_validated_read, validated_read

_MUTATION_LOCKS_GUARD = RLock()
_MUTATION_LOCKS: dict[tuple[type[object] | int, str], ProjectWriteLock] = {}


def _mutation_lock_for(root: str, filesystem: PersistenceFilesystemPort):
    """Return the process-wide mutation lock shared by one persistence root.

    Project and Variant repositories are constructed independently by the
    composition root.  Keying the lock by filesystem adapter family and
    canonical root lets their conditional saves participate in one transaction
    boundary, including when equivalent OS adapters are constructed separately.
    """

    family = OsPersistenceFilesystem if isinstance(filesystem, OsPersistenceFilesystem) else id(filesystem)
    key = (family, os.path.normcase(filesystem.canonicalize(root)))
    with _MUTATION_LOCKS_GUARD:
        return _MUTATION_LOCKS.setdefault(key, ProjectWriteLock(root, filesystem))


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
    def mutation_lock(self) -> ProjectWriteLock:
        """Lock shared by repositories targeting the same persistence root."""

        return self._mutation_lock

    def path_for(self, ref: RefT) -> str:
        self._check_ref(ref)
        return self._paths.record(ref)

    def read_snapshot(self, ref: RefT) -> DtoT:
        """Validate a record without writing migrations, backups or quarantine files."""

        return cast(DtoT, self.read_snapshot_record(ref).value)

    def read_snapshot_record(self, ref: RefT) -> LoadedRecord:
        """Read a detached DTO and the hash of its actual persisted source bytes.

        ``migrated`` marks a legacy document normalized only in memory. Such a
        view cannot seed current-schema evidence or bypass load-time migration.
        """
        raw = self._filesystem.read_bytes(self.path_for(ref))
        cached = validated_read(self, ref, raw)
        if cached is not None:
            return cached
        document = parse_json_bytes(raw)
        if version_of(document) < SCHEMA_VERSION:
            document = migrate_to_current(document, ref).document
            return LoadedRecord(ref, validate_v2(document, ref), hashlib.sha256(raw).hexdigest(), migrated=True)
        return decode_validated_read(self, ref, raw, validate=validate_v2)

    def load(self, ref: RefT) -> LoadResult:
        self._check_ref(ref)
        path = self.path_for(ref)
        raw = self._filesystem.read_bytes(path)
        cached = validated_read(self, ref, raw)
        if cached is not None:
            return cached
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
                return decode_validated_read(self, ref, raw, validate=validate_v2)
            except SchemaValidationError as exc:
                return self._quarantine(ref, path, raw, digest, exc)

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
            durable=ref.kind is EntityKind.SESSION,
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
        remember_validated_read(self, migrated_raw, LoadedRecord(ref, value, migrated_digest), full_schema=True)
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
        raw = serialize_document(document)
        validated = decode_validated_read(self, ref, raw, validate=validate_v2, remember=False, reuse_unchanged=True)
        digest = validated.source_hash
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
            durable=ref.kind is EntityKind.SESSION,
        )
        return remember_validated_read(self, raw, validated)

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
            restored = decode_validated_read(self, ref, raw, validate=validate_v2, remember=False, reuse_unchanged=True)
            if not isinstance(restored.value, self._dto_type):
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
            remember_validated_read(self, raw, restored)
            loaded = self.load(ref)
            if not isinstance(loaded, LoadedRecord) or loaded.source_hash != digest:
                raise RuntimeError("restored persistence preimage could not be verified")
            return loaded

    def _assert_writable_destination(self, ref: RefT, path: str) -> None:
        if not self._filesystem.exists(path):
            return
        existing = self._filesystem.read_bytes(path)
        if validated_read(self, ref, existing) is not None:
            return
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
            decode_validated_read(self, ref, existing, validate=validate_v2)
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

    def _recover_before_load_side_effects(self, ref) -> LoadedRecord | None:
        raw = self._filesystem.read_bytes(self.path_for(ref))
        cached = validated_read(self, ref, raw)
        if cached is not None:
            return cached
        try:
            document = parse_json_bytes(raw)
            if version_of(document) > SCHEMA_VERSION:
                return
            if version_of(document) == SCHEMA_VERSION:
                # The caller still owns the read lease. Reuse this validated
                # read rather than parsing and validating the same file again.
                return decode_validated_read(self, ref, raw, validate=validate_v2)
        except SchemaValidationError:
            pass
        self._mutation_lock.recover()
        return None


class ProjectRepository(JsonRepository[ProjectRef, ProjectDto]):
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        super().__init__(root, filesystem, kind=EntityKind.PROJECT, dto_type=ProjectDto)

    def save(self, ref: ProjectRef, value: ProjectDto) -> LoadedRecord:
        with self._mutation_lock:
            return super().save(ref, value)

    def load(self, ref: ProjectRef) -> LoadResult:
        # Legacy loads can publish migrations, and reads must not observe a
        # different process's partially published activation.
        with self._mutation_lock.reading():
            loaded = self._recover_before_load_side_effects(ref)
            return loaded if loaded is not None else super().load(ref)

    def read_snapshot(self, ref: ProjectRef) -> ProjectDto:
        with self._mutation_lock.reading():
            return super().read_snapshot(ref)

    def read_snapshot_record(self, ref: ProjectRef) -> LoadedRecord:
        with self._mutation_lock.reading():
            return super().read_snapshot_record(ref)

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
            if not record_matches(current, expected_revision, expected_source_hash):
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

    def load(self, ref: VariantRef) -> LoadResult:
        with self._mutation_lock.reading():
            loaded = self._recover_before_load_side_effects(ref)
            return loaded if loaded is not None else super().load(ref)

    def read_snapshot(self, ref: VariantRef) -> VariantDto:
        with self._mutation_lock.reading():
            return super().read_snapshot(ref)

    def read_snapshot_record(self, ref: VariantRef) -> LoadedRecord:
        with self._mutation_lock.reading():
            return super().read_snapshot_record(ref)

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
            if not record_matches(current, expected_revision, expected_source_hash):
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

    def read_snapshot(self, ref: SessionRef) -> SessionDto:
        return cast(SessionDto, self.read_snapshot_record(ref).value)

    def read_snapshot_record(self, ref: SessionRef) -> LoadedRecord:
        record = super().read_snapshot_record(ref)
        self._validate_attachments(ref, cast(SessionDto, record.value))
        return record

    @contextmanager
    def write_transaction(self, ref: SessionRef) -> Iterator[None]:
        """Keep revision checks and publication inside one shared, fenced boundary."""
        self._check_ref(ref)
        try:
            with self._mutation_lock, session_write_lease(self.root, ref.identity.value, self._filesystem):
                yield
        except ReadOnlyWriteRefused as exc:
            if exc.code != "PROJECT_WRITER_BUSY":
                raise
            raise ReadOnlyWriteRefused(
                "SESSION_WRITER_BUSY", "Another process owns this Session's root write boundary; reload and retry."
            ) from exc

    def load(self, ref: SessionRef) -> LoadResult:
        # Loading old data publishes migrations, so it uses the same write lease.
        with self.write_transaction(ref):
            loaded = super().load(ref)
            if isinstance(loaded, LoadedRecord):
                try:
                    self._validate_attachments(ref, loaded.value)
                except SchemaValidationError as exc:
                    path = self.path_for(ref)
                    raw = self._filesystem.read_bytes(path)
                    return self._quarantine(ref, path, raw, hashlib.sha256(raw).hexdigest(), exc)
            return loaded

    def save(self, ref: SessionRef, value: SessionDto) -> LoadedRecord:
        with self.write_transaction(ref):
            self._validate_attachments(ref, value)
            return super().save(ref, value)

    def delete(self, ref: SessionRef) -> None:
        with self.write_transaction(ref):
            super().delete(ref)

    def _validate_attachments(self, ref: SessionRef, value: SessionDto) -> None:
        from transbridge.application.assistant_requests.transcript import TranscriptManifest
        from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore

        data = value.envelope.data
        try:
            manifest = TranscriptManifest.from_dict(data.get("transcript_manifest", {}))
            AssistantTranscriptStore(self.root, self._filesystem).validate(ref.identity.value, manifest)
        except (KeyError, TypeError, ValueError, OSError, PersistenceV2Error) as exc:
            raise SchemaValidationError(
                "SESSION_ATTACHMENTS_INVALID",
                "Session transcript references are invalid, unavailable, or corrupt; keep this record read-only.",
                pointer="/data/transcript_manifest",
            ) from exc


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
