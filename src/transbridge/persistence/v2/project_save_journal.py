"""Durable Project/Variant save journal and recovery protocol."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.projects import LifecycleSave

from .atomic_documents import AtomicDocumentStore
from .filesystem import PersistenceFilesystemPort
from .ids import EntityKind, ProjectId, ProjectRef, VariantId, VariantRef
from .migration import migrate_to_current
from .models import SCHEMA_VERSION, LoadedRecord, ProjectDto, VariantDto
from .repository import (
    ProjectRepository,
    ProjectRevisionConflict,
    RecordContentConflict,
    VariantRepository,
    VariantRevisionConflict,
)
from .schema import parse_json_bytes, serialize_document, validate_v2, version_of

_LOGGER = logging.getLogger(__name__)
_JOURNAL_SCHEMA_VERSION = 2
_JOURNAL_KEYS = frozenset("schema_version transaction_id project_id variant_id project variant manifest_digest".split())
_RECORD_KEYS = frozenset(
    "identity project_id previous_bytes_base64 previous_digest previous_revision "
    "target target_digest target_revision".split()
)


class ProjectSaveProtocol:
    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
        variants: VariantRepository,
    ) -> None:
        if projects.mutation_lock is not variants.mutation_lock:
            raise ValueError("Project and Variant repositories must share one persistence mutation lock")
        self._projects = projects
        self._variants = variants
        self._filesystem = filesystem
        self._documents = AtomicDocumentStore(root, filesystem)
        self._journal_directory = self._documents.path("project-save-journal")
        self._mutation_lock = projects.mutation_lock
        self.recover_pending()

    def commit(self, save: LifecycleSave, transaction_id: str) -> None:
        project_ref = ProjectRef(ProjectId(save.project.envelope.identity))
        if (save.variant is None) != (save.formal_variant_ref is None):
            raise DomainError(
                ErrorCategory.INPUT,
                "ACTIVE_SAVE_VARIANT_CONTRACT_INVALID",
                "The active Variant snapshot and reference must be present together.",
            )
        if save.variant is not None and save.variant.ref != save.formal_variant_ref:
            raise DomainError(
                ErrorCategory.INPUT,
                "ACTIVE_SAVE_VARIANT_CONTRACT_INVALID",
                "The active Variant snapshot does not match its formal reference.",
            )

        with self._mutation_lock:
            self.recover_pending()
            current_project = _load_for_save(self._projects, project_ref)
            current_variant = (
                None if save.formal_variant_ref is None else _load_for_save(self._variants, save.formal_variant_ref)
            )
            conflicts = _save_revision_conflicts(save, current_project, current_variant)
            if conflicts:
                raise _persisted_save_conflict(conflicts)

            assert isinstance(current_project, LoadedRecord)
            write_project = save.project.envelope.revision != save.expected_persisted_project_revision
            write_variant = (
                save.variant is not None
                and save.expected_persisted_revision is not None
                and save.variant.revision != save.expected_persisted_revision
            )
            if write_project and save.project.envelope.revision <= save.expected_persisted_project_revision:
                raise _invalid_target_revision("project")
            if write_variant and save.variant is not None:
                assert save.expected_persisted_revision is not None
                if save.variant.revision <= save.expected_persisted_revision:
                    raise _invalid_target_revision("variant")

            journal_path = None
            previous_variant_bytes = None
            if write_project and write_variant:
                assert isinstance(current_variant, LoadedRecord)
                assert save.variant is not None
                assert save.formal_variant_ref is not None
                previous_project_bytes = self._verified_preimage_bytes(self._projects, project_ref, current_project)
                previous_variant_bytes = self._verified_preimage_bytes(
                    self._variants,
                    save.formal_variant_ref,
                    current_variant,
                )
                journal_path = self._write_journal(
                    transaction_id,
                    project_ref,
                    current_project,
                    previous_project_bytes,
                    save.project,
                    save.formal_variant_ref,
                    current_variant,
                    previous_variant_bytes,
                    save.variant.to_dto(),
                )

            try:
                saved_variant = None
                if write_variant:
                    assert save.formal_variant_ref is not None
                    assert save.variant is not None
                    assert isinstance(current_variant, LoadedRecord)
                    try:
                        saved_variant = self._variants.save_if_revision(
                            save.formal_variant_ref,
                            save.variant.to_dto(),
                            expected_revision=save.expected_persisted_revision,
                            expected_source_hash=current_variant.source_hash,
                        )
                    except VariantRevisionConflict as exc:
                        raise _persisted_save_conflict(
                            (
                                _revision_conflict(
                                    "variant",
                                    save.formal_variant_ref.identity.value,
                                    exc.expected_revision,
                                    exc.actual_revision,
                                    expected_source_hash=exc.expected_source_hash,
                                    actual_source_hash=exc.actual_source_hash,
                                ),
                            ),
                            cause=exc,
                        ) from exc
                    _verify_saved_record(
                        self._variants.load(save.formal_variant_ref),
                        saved_variant,
                        entity_type="variant",
                    )

                if write_project:
                    try:
                        saved_project = self._projects.save_if_revision(
                            project_ref,
                            save.project,
                            expected_revision=save.expected_persisted_project_revision,
                            expected_source_hash=current_project.source_hash,
                        )
                    except Exception as exc:
                        if saved_variant is not None:
                            assert save.formal_variant_ref is not None
                            assert isinstance(current_variant, LoadedRecord)
                            assert previous_variant_bytes is not None
                            self._compensate_variant(
                                save.formal_variant_ref,
                                current_variant,
                                previous_variant_bytes,
                                saved_variant,
                                project_error=exc,
                            )
                        if isinstance(exc, ProjectRevisionConflict):
                            raise _persisted_save_conflict(
                                (
                                    _revision_conflict(
                                        "project",
                                        project_ref.identity.value,
                                        exc.expected_revision,
                                        exc.actual_revision,
                                        expected_source_hash=exc.expected_source_hash,
                                        actual_source_hash=exc.actual_source_hash,
                                    ),
                                ),
                                cause=exc,
                            ) from exc
                        raise
                    _verify_saved_record(
                        self._projects.load(project_ref),
                        saved_project,
                        entity_type="project",
                    )
            except Exception as exc:
                if journal_path is not None:
                    try:
                        resolution = self._recover_journal(journal_path)
                    except Exception as recovery_exc:
                        if (
                            isinstance(exc, DomainError)
                            and exc.code == "ACTIVE_SAVE_COMPENSATION_CONFLICT"
                            and isinstance(recovery_exc, DomainError)
                            and recovery_exc.code == "ACTIVE_SAVE_JOURNAL_CONFLICT"
                        ):
                            raise exc
                        raise recovery_exc from exc
                    if resolution == "committed":
                        return
                raise
            else:
                if journal_path is not None:
                    try:
                        self._remove_journal(journal_path)
                    except Exception:
                        _LOGGER.warning("Committed Project save journal cleanup is pending", exc_info=True)

    def recover_pending(self) -> None:
        with self._mutation_lock:
            try:
                pending = self._filesystem.list_files(self._journal_directory)
            except Exception as exc:
                raise DomainError(
                    ErrorCategory.INTERNAL,
                    "ACTIVE_SAVE_JOURNAL_SCAN_FAILED",
                    "The Project save recovery journal could not be scanned safely.",
                    cause=exc,
                ) from exc
            for path in pending:
                self._recover_journal(path)

    def _verified_preimage_bytes(self, repository: Any, ref: Any, record: LoadedRecord) -> bytes:
        raw = self._filesystem.read_bytes(repository.path_for(ref))
        digest = hashlib.sha256(raw).hexdigest()
        if digest != record.source_hash:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "ACTIVE_SAVE_PREIMAGE_CHANGED",
                "A persistence document changed while its recovery preimage was captured.",
                retryable=True,
                details={
                    "entity_type": ref.kind.value,
                    "identity": ref.identity.value,
                    "expected_digest": record.source_hash,
                    "actual_digest": digest,
                },
            )
        return raw

    def _write_journal(
        self,
        transaction_id: str,
        project_ref: ProjectRef,
        previous_project: LoadedRecord,
        previous_project_bytes: bytes,
        target_project: ProjectDto,
        variant_ref: VariantRef,
        previous_variant: LoadedRecord,
        previous_variant_bytes: bytes,
        target_variant: VariantDto,
    ) -> str:
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 256:
            raise DomainError(
                ErrorCategory.INPUT,
                "ACTIVE_SAVE_TRANSACTION_ID_INVALID",
                "The save transaction identity is invalid.",
            )
        document = {
            "schema_version": _JOURNAL_SCHEMA_VERSION,
            "transaction_id": transaction_id,
            "project_id": project_ref.identity.value,
            "variant_id": variant_ref.identity.value,
            "project": _journal_record(
                project_ref,
                previous_project,
                previous_project_bytes,
                target_project,
            ),
            "variant": _journal_record(
                variant_ref,
                previous_variant,
                previous_variant_bytes,
                target_variant,
            ),
        }
        document["manifest_digest"] = _manifest_digest(document)
        filename = _journal_filename(transaction_id)
        relative_path = os.path.join("project-save-journal", filename)
        path = self._documents.path(relative_path)
        if self._filesystem.exists(path):
            raise DomainError(
                ErrorCategory.CONFLICT,
                "ACTIVE_SAVE_JOURNAL_DUPLICATE",
                "A save transaction with the same identity already has recovery state.",
                details={"transaction_id": transaction_id},
            )
        self._documents.write_json(
            relative_path,
            document,
            f"{transaction_id}-prepare",
            durable=True,
        )
        try:
            loaded = _parse_manifest(self._filesystem.read_bytes(path), path)
        except Exception as exc:
            raise DomainError(
                ErrorCategory.INTERNAL,
                "ACTIVE_SAVE_JOURNAL_VERIFICATION_FAILED",
                "The Project save recovery journal could not be verified before publication.",
                cause=exc,
            ) from exc
        if loaded["transaction_id"] != transaction_id:
            raise DomainError(
                ErrorCategory.INTERNAL,
                "ACTIVE_SAVE_JOURNAL_VERIFICATION_FAILED",
                "The Project save recovery journal changed during publication.",
            )
        return path

    def _recover_journal(self, path: str) -> str:
        try:
            manifest = _parse_manifest(self._filesystem.read_bytes(path), path)
            project = manifest["project"]
            variant = manifest["variant"]
        except Exception as exc:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "ACTIVE_SAVE_JOURNAL_INVALID",
                "An interrupted Project save has an invalid recovery journal; automatic recovery was refused.",
                details={"journal_path": path},
                cause=exc,
            ) from exc

        current_project = _load_for_save(self._projects, project["ref"])
        current_variant = _load_for_save(self._variants, variant["ref"])
        project_state = _record_state(current_project, project)
        variant_state = _record_state(current_variant, variant)
        if project_state == variant_state == "target":
            self._remove_journal(path)
            return "committed"
        if project_state == variant_state == "previous":
            self._remove_journal(path)
            return "rolled-back"
        if project_state == "previous" and variant_state == "target":
            assert isinstance(current_variant, LoadedRecord)
            try:
                restored = self._variants.restore_bytes_if_match(
                    variant["ref"],
                    variant["previous_bytes"],
                    expected_revision=variant["target_revision"],
                    expected_source_hash=variant["target_digest"],
                )
            except RecordContentConflict as exc:
                raise _journal_conflict(path, project_state, "other", cause=exc) from exc
            _verify_saved_record(
                self._variants.load(variant["ref"]),
                restored,
                entity_type="variant recovery",
            )
            if restored.source_hash != variant["previous_digest"]:
                raise RuntimeError("interrupted Variant recovery did not restore its exact preimage")
            self._remove_journal(path)
            return "rolled-back"
        raise _journal_conflict(path, project_state, variant_state)

    def _remove_journal(self, path: str) -> None:
        try:
            self._documents.remove_durable(path, os.path.basename(path))
        except Exception as exc:
            raise DomainError(
                ErrorCategory.INTERNAL,
                "ACTIVE_SAVE_JOURNAL_CLEANUP_FAILED",
                "A resolved Project save journal could not be removed durably.",
                details={"journal_path": path},
                cause=exc,
            ) from exc

    def _compensate_variant(
        self,
        variant_ref: VariantRef,
        previous: LoadedRecord,
        previous_bytes: bytes,
        published: LoadedRecord,
        *,
        project_error: Exception,
    ) -> None:
        try:
            restored = self._variants.restore_bytes_if_match(
                variant_ref,
                previous_bytes,
                expected_revision=published.value.envelope.revision,
                expected_source_hash=published.source_hash,
            )
            if restored.source_hash != previous.source_hash:
                raise RuntimeError("Variant compensation did not restore the verified preimage")
            _verify_saved_record(
                self._variants.load(variant_ref),
                previous,
                entity_type="variant compensation",
            )
        except RecordContentConflict as exc:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "ACTIVE_SAVE_COMPENSATION_CONFLICT",
                "The Project save failed and the Variant changed before rollback; automatic rollback was refused.",
                details={
                    "variant_id": variant_ref.identity.value,
                    "expected_revision": exc.expected_revision,
                    "actual_revision": exc.actual_revision,
                    "expected_digest": exc.expected_source_hash,
                    "actual_digest": exc.actual_source_hash,
                    "project_error_type": type(project_error).__name__,
                },
                cause=exc,
            ) from exc
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                ErrorCategory.INTERNAL,
                "ACTIVE_SAVE_COMPENSATION_FAILED",
                "The Project save failed and the previous Variant could not be restored safely.",
                details={
                    "variant_id": variant_ref.identity.value,
                    "project_error_type": type(project_error).__name__,
                },
                cause=exc,
            ) from exc


def _save_revision_conflicts(
    save: LifecycleSave,
    current_project: object,
    current_variant: object | None,
) -> tuple[dict[str, object], ...]:
    conflicts: list[dict[str, object]] = []
    actual_project_revision = _loaded_revision(current_project)
    if actual_project_revision != save.expected_persisted_project_revision:
        conflicts.append(
            _revision_conflict(
                "project",
                save.project.envelope.identity,
                save.expected_persisted_project_revision,
                actual_project_revision,
                load_result=current_project,
            )
        )
    if save.formal_variant_ref is not None:
        actual_variant_revision = _loaded_revision(current_variant)
        if actual_variant_revision != save.expected_persisted_revision:
            conflicts.append(
                _revision_conflict(
                    "variant",
                    save.formal_variant_ref.identity.value,
                    save.expected_persisted_revision,
                    actual_variant_revision,
                    load_result=current_variant,
                )
            )
    return tuple(conflicts)


def _journal_record(
    ref: ProjectRef | VariantRef,
    previous: LoadedRecord,
    previous_bytes: bytes,
    target: ProjectDto | VariantDto,
) -> dict[str, object]:
    if hashlib.sha256(previous_bytes).hexdigest() != previous.source_hash:
        raise ValueError("journal preimage bytes do not match their loaded digest")
    previous_document = parse_json_bytes(previous_bytes)
    previous_value = validate_v2(previous_document, ref)
    target_document = target.envelope.to_dict()
    if isinstance(ref, ProjectRef) and version_of(target_document) < SCHEMA_VERSION:
        target_document = migrate_to_current(target_document, ref).document
    validated_target = validate_v2(target_document, ref)
    normalized_target = validated_target.envelope.to_dict()
    previous_revision = previous_value.envelope.revision
    target_revision = validated_target.envelope.revision
    if target_revision <= previous_revision:
        raise ValueError("journal target revision must advance its preimage revision")
    return {
        "identity": ref.identity.value,
        "project_id": None if isinstance(ref, ProjectRef) else ref.project_id.value,
        "previous_bytes_base64": base64.b64encode(previous_bytes).decode("ascii"),
        "previous_digest": previous.source_hash,
        "previous_revision": previous_revision,
        "target": normalized_target,
        "target_digest": hashlib.sha256(serialize_document(normalized_target)).hexdigest(),
        "target_revision": target_revision,
    }


def _parse_manifest(raw: bytes, path: str) -> dict[str, Any]:
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != _JOURNAL_KEYS:
        raise ValueError("journal manifest shape is invalid")
    if document["schema_version"] != _JOURNAL_SCHEMA_VERSION:
        raise ValueError("journal manifest schema is unsupported")
    transaction_id = document["transaction_id"]
    if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 256:
        raise ValueError("journal transaction identity is invalid")
    if os.path.basename(path) != _journal_filename(transaction_id):
        raise ValueError("journal filename does not match its transaction identity")
    manifest_digest = document["manifest_digest"]
    if not _is_sha256(manifest_digest) or _manifest_digest(document) != manifest_digest:
        raise ValueError("journal manifest digest does not match")
    project = _parse_journal_record(document["project"], entity_type=EntityKind.PROJECT)
    variant = _parse_journal_record(document["variant"], entity_type=EntityKind.VARIANT)
    if document["project_id"] != project["ref"].identity.value:
        raise ValueError("journal Project identity does not match its record")
    if document["variant_id"] != variant["ref"].identity.value:
        raise ValueError("journal Variant identity does not match its record")
    if variant["ref"].project_id != project["ref"].identity:
        raise ValueError("journal Variant does not belong to its Project")
    return {
        "transaction_id": transaction_id,
        "project": project,
        "variant": variant,
    }


def _parse_journal_record(data: object, *, entity_type: EntityKind) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != _RECORD_KEYS:
        raise ValueError("journal record shape is invalid")
    identity = data["identity"]
    if not isinstance(identity, str):
        raise ValueError("journal record identity is invalid")
    project_id = data["project_id"]
    if entity_type is EntityKind.PROJECT:
        if project_id is not None:
            raise ValueError("journal Project record cannot have a parent Project")
        ref: ProjectRef | VariantRef = ProjectRef(ProjectId(identity))
    else:
        if not isinstance(project_id, str):
            raise ValueError("journal Variant Project identity is invalid")
        ref = VariantRef(VariantId(identity), ProjectId(project_id))
    encoded = data["previous_bytes_base64"]
    if not isinstance(encoded, str):
        raise ValueError("journal preimage encoding is invalid")
    previous_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
    previous_digest = data["previous_digest"]
    target_digest = data["target_digest"]
    if not _is_sha256(previous_digest) or hashlib.sha256(previous_bytes).hexdigest() != previous_digest:
        raise ValueError("journal preimage digest does not match")
    if not _is_sha256(target_digest):
        raise ValueError("journal target digest is invalid")
    previous_document = parse_json_bytes(previous_bytes)
    if version_of(previous_document) != SCHEMA_VERSION:
        raise ValueError("journal preimage schema is not current")
    previous = validate_v2(previous_document, ref)
    target_document = data["target"]
    if not isinstance(target_document, dict):
        raise ValueError("journal target must be an object")
    target = validate_v2(target_document, ref)
    if hashlib.sha256(serialize_document(target_document)).hexdigest() != target_digest:
        raise ValueError("journal target digest does not match")
    previous_revision = data["previous_revision"]
    target_revision = data["target_revision"]
    if previous_revision != previous.envelope.revision or target_revision != target.envelope.revision:
        raise ValueError("journal record revisions do not match their documents")
    if not isinstance(previous_revision, int) or isinstance(previous_revision, bool):
        raise ValueError("journal preimage revision is invalid")
    if (
        not isinstance(target_revision, int)
        or isinstance(target_revision, bool)
        or target_revision <= previous_revision
    ):
        raise ValueError("journal target revision does not advance its preimage")
    return {
        "ref": ref,
        "previous": previous,
        "previous_bytes": previous_bytes,
        "previous_digest": previous_digest,
        "previous_revision": previous_revision,
        "target": target,
        "target_digest": target_digest,
        "target_revision": target_revision,
    }


def _manifest_digest(document: dict[str, object]) -> str:
    unsigned = {key: value for key, value in document.items() if key != "manifest_digest"}
    payload = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _journal_filename(transaction_id: str) -> str:
    return f"{hashlib.sha256(transaction_id.encode()).hexdigest()}.json"


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _record_state(record: object, expected: dict[str, object]) -> str:
    if not isinstance(record, LoadedRecord):
        return "missing-or-read-only"
    if record.source_hash == expected["previous_digest"]:
        return "previous"
    if record.source_hash == expected["target_digest"]:
        return "target"
    return "other"


def _loaded_revision(result: object | None) -> int | None:
    if not isinstance(result, LoadedRecord):
        return None
    return result.value.envelope.revision


def _load_for_save(repository: Any, ref: Any) -> object | None:
    try:
        return repository.load(ref)
    except FileNotFoundError:
        return None


def _revision_conflict(
    entity_type: str,
    identity: str,
    expected_revision: int | None,
    actual_revision: int | None,
    *,
    load_result: object | None = None,
    expected_source_hash: str | None = None,
    actual_source_hash: str | None = None,
) -> dict[str, object]:
    return {
        "entity_type": entity_type,
        "identity": identity,
        "expected_revision": expected_revision,
        "actual_revision": actual_revision,
        "load_result": None if load_result is None else type(load_result).__name__,
        "expected_digest": expected_source_hash,
        "actual_digest": actual_source_hash,
    }


def _persisted_save_conflict(
    conflicts: tuple[dict[str, object], ...],
    *,
    cause: BaseException | None = None,
) -> DomainError:
    return DomainError(
        ErrorCategory.CONFLICT,
        "ACTIVE_SAVE_PERSISTED_STALE",
        "The persisted Project or Variant changed before the active save could commit.",
        retryable=True,
        details={"conflicts": list(conflicts)},
        cause=cause,
    )


def _journal_conflict(
    path: str,
    project_state: str,
    variant_state: str,
    *,
    cause: BaseException | None = None,
) -> DomainError:
    return DomainError(
        ErrorCategory.CONFLICT,
        "ACTIVE_SAVE_JOURNAL_CONFLICT",
        "An interrupted Project save conflicts with newer or unrecognized persisted content.",
        details={
            "journal_path": path,
            "project_state": project_state,
            "variant_state": variant_state,
        },
        cause=cause,
    )


def _invalid_target_revision(entity_type: str) -> DomainError:
    return DomainError(
        ErrorCategory.INPUT,
        "ACTIVE_SAVE_TARGET_REVISION_INVALID",
        "A changed persistence document must advance its revision.",
        details={"entity_type": entity_type},
    )


def _verify_saved_record(actual: object, expected: LoadedRecord, *, entity_type: str) -> None:
    if isinstance(actual, LoadedRecord) and actual.source_hash == expected.source_hash:
        return
    raise DomainError(
        ErrorCategory.INTERNAL,
        "ACTIVE_SAVE_VERIFICATION_FAILED",
        "A persistence document could not be verified after publication.",
        details={
            "entity_type": entity_type,
            "expected_digest": expected.source_hash,
            "actual_digest": None if not isinstance(actual, LoadedRecord) else actual.source_hash,
            "load_result": type(actual).__name__,
        },
    )


__all__ = ["ProjectSaveProtocol"]
