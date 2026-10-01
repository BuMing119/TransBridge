"""Durable all-Variant source replacement with exact preimage recovery."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import logging
import os
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory

from .v2.atomic_documents import AtomicDocumentStore
from .v2.filesystem import PersistenceFilesystemPort, RepositoryPaths
from .v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from .v2.migration import migrate_to_current
from .v2.models import SCHEMA_VERSION, ProjectDto
from .v2.repository import ProjectRepository, VariantRepository
from .v2.schema import parse_json_bytes, serialize_document, validate_v2, version_of
from .v2.variant import VariantSnapshot

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceUpdateRead:
    project: ProjectDto
    variants: tuple[VariantSnapshot, ...]
    project_hash: str
    variant_hashes: tuple[tuple[str, str], ...]


class ProjectSourceUpdateStore:
    """Publish Variants first and their Project last under one recovery journal.

    Backups intentionally outlive successful updates and include every old
    Variant entry, including removed translations. No external source file is
    read or changed by this store.
    """

    def __init__(
        self,
        root: str,
        filesystem: PersistenceFilesystemPort,
        projects: ProjectRepository,
        variants: VariantRepository,
    ) -> None:
        if projects.mutation_lock is not variants.mutation_lock:
            raise ValueError("source updates require a shared repository mutation lock")
        self._filesystem = filesystem
        self._projects = projects
        self._variants = variants
        self._lock = projects.mutation_lock
        self._documents = AtomicDocumentStore(root, filesystem)
        self._paths = RepositoryPaths(root, filesystem)
        self._journal_directory = self._documents.path("source-update-journal")
        self.recover_pending()

    def read(self, ref: ProjectRef) -> SourceUpdateRead:
        with self._lock:
            self.recover_pending()
            project, project_hash = self._read_record(ref)
            snapshots: list[VariantSnapshot] = []
            hashes: list[tuple[str, str]] = []
            for identity in _variant_ids(project):
                variant_ref = VariantRef(VariantId(identity), ref.identity)
                dto, digest = self._read_record(variant_ref)
                snapshots.append(VariantSnapshot.from_dto(dto, variant_ref))
                hashes.append((identity, digest))
            return SourceUpdateRead(project, tuple(snapshots), project_hash, tuple(hashes))

    def commit(
        self,
        expected: SourceUpdateRead,
        project: ProjectDto,
        variants: tuple[VariantSnapshot, ...],
        transaction_id: str,
        *,
        backup_metadata: dict[str, Any] | None = None,
    ) -> str:
        if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 256:
            raise ValueError("source update transaction identity must be non-empty and at most 256 characters")
        ref = ProjectRef(ProjectId(project.envelope.identity))
        _validate_targets(expected, project, variants)
        token = _digest(transaction_id.encode())
        backup_path = self._paths.guard(
            os.path.join(self._paths.project_backup_data(ref), "source-updates", f"{token}.json")
        )
        journal_path = self._documents.path(os.path.join("source-update-journal", f"{token}.json"))
        with self._lock:
            self.recover_pending()
            current = self.read(ref)
            if (
                current.project_hash != expected.project_hash
                or current.variant_hashes != expected.variant_hashes
                or current.project.envelope.revision != expected.project.envelope.revision
                or tuple(item.revision for item in current.variants)
                != tuple(item.revision for item in expected.variants)
            ):
                raise _conflict("SOURCE_UPDATE_PERSISTED_STALE", "工程或某个版本已发生修改，请重新预览更新。")
            if self._filesystem.exists(backup_path) or self._filesystem.exists(journal_path):
                raise _conflict("SOURCE_UPDATE_TRANSACTION_EXISTS", "该更新事务已有备份，请使用新的事务标识。")
            hashes = dict(expected.variant_hashes)
            records = [
                self._record(item.ref, item.to_dto(), hashes[item.ref.identity.value])
                for item in sorted(variants, key=lambda item: item.ref.identity.value)
            ]
            records.append(self._record(ref, project, expected.project_hash))
            manifest = {
                "schema_version": 1,
                "status": "prepared",
                "transaction_id": transaction_id,
                "project_id": ref.identity.value,
                "records": records,
                "metadata": backup_metadata or {},
            }
            manifest["manifest_digest"] = _digest(serialize_document(manifest))
            raw = serialize_document(manifest)
            # A permanent backup is durable before the recoverable mutation starts.
            self._documents.write_bytes(backup_path, raw, f"{token}-backup", durable=True)
            self._documents.write_bytes(journal_path, raw, f"{token}-journal", durable=True)
            self._parse_manifest(journal_path)
            try:
                for index, record in enumerate(records):
                    if index == len(records) - 1:
                        # Project publication must never certify a Variant that
                        # changed after its own conditional publication.
                        for published in records[:-1]:
                            published_path = self._path_for(self._record_ref(manifest, published))
                            if _digest(self._filesystem.read_bytes(published_path)) != published["target_digest"]:
                                raise _conflict("SOURCE_UPDATE_PERSISTED_STALE", "发布工程前某个版本已发生修改。")
                    record_ref = self._record_ref(manifest, record)
                    path = self._path_for(record_ref)
                    if _digest(self._filesystem.read_bytes(path)) != record["previous_digest"]:
                        raise _conflict("SOURCE_UPDATE_PERSISTED_STALE", "更新发布期间工程文件发生修改。")
                    self._documents.write_bytes(
                        path, _decode(record["target"]), f"{token}-publish-{index}", durable=True
                    )
            except Exception:
                if self._recover(journal_path, rollback_if_marker_fails=True) == "committed":
                    return backup_path
                raise
            self._recover(journal_path, rollback_if_marker_fails=True)
            return backup_path

    def recover_pending(self) -> None:
        with self._lock:
            for path in self._filesystem.list_files(self._journal_directory):
                self._recover(path)

    def _read_record(self, ref):
        raw = self._filesystem.read_bytes(self._path_for(ref))
        document = parse_json_bytes(raw)
        if version_of(document) < SCHEMA_VERSION:
            document = migrate_to_current(document, ref).document
        return validate_v2(document, ref), _digest(raw)

    def _path_for(self, ref):
        return self._projects.path_for(ref) if isinstance(ref, ProjectRef) else self._variants.path_for(ref)

    def _record(self, ref, target, expected_hash: str) -> dict[str, Any]:
        previous = self._filesystem.read_bytes(self._path_for(ref))
        if _digest(previous) != expected_hash:
            raise _conflict("SOURCE_UPDATE_PREIMAGE_CHANGED", "捕获更新备份时工程文件发生修改。")
        target_bytes = serialize_document(target.envelope.to_dict())
        validate_v2(parse_json_bytes(target_bytes), ref)
        return {
            "kind": ref.kind.value,
            "identity": ref.identity.value,
            "previous": base64.b64encode(previous).decode("ascii"),
            "previous_digest": expected_hash,
            "target": base64.b64encode(target_bytes).decode("ascii"),
            "target_digest": _digest(target_bytes),
        }

    def _parse_manifest(self, path: str) -> dict[str, Any]:
        try:
            manifest = parse_json_bytes(self._filesystem.read_bytes(path))
            digest = manifest.pop("manifest_digest")
            if digest != _digest(serialize_document(manifest)) or manifest["schema_version"] != 1:
                raise ValueError("invalid source update manifest digest/version")
            if manifest.get("status") not in {"prepared", "committed"}:
                raise ValueError("invalid source update journal status")
            manifest["manifest_digest"] = digest
            token = _digest(manifest["transaction_id"].encode())
            if os.path.basename(path) != f"{token}.json":
                raise ValueError("source update journal identity mismatch")
            records = manifest["records"]
            if not isinstance(records, list) or not records or records[-1]["kind"] != "project":
                raise ValueError("source update journal must end with its Project")
            identities = []
            for index, record in enumerate(records):
                ref = self._record_ref(manifest, record)
                if index < len(records) - 1 and not isinstance(ref, VariantRef):
                    raise ValueError("source update journal contains multiple Projects")
                for state in ("previous", "target"):
                    raw = _decode(record[state])
                    if _digest(raw) != record[f"{state}_digest"]:
                        raise ValueError("source update preimage/target digest mismatch")
                    document = parse_json_bytes(raw)
                    if version_of(document) < SCHEMA_VERSION:
                        document = migrate_to_current(document, ref).document
                    dto = validate_v2(document, ref)
                    if isinstance(ref, ProjectRef) and set(_variant_ids(dto)) != set(identities):
                        raise ValueError("source update journal does not cover every Variant")
                if isinstance(ref, VariantRef):
                    identities.append(ref.identity.value)
            if len(identities) != len(set(identities)):
                raise ValueError("source update journal contains duplicate Variants")
            return manifest
        except Exception as exc:
            raise DomainError(
                ErrorCategory.CONFLICT,
                "SOURCE_UPDATE_JOURNAL_INVALID",
                "源文件更新恢复记录无效，已拒绝自动恢复。",
                details={"journal_path": path},
                cause=exc,
            ) from exc

    @staticmethod
    def _record_ref(manifest, record):
        project_id = ProjectId(manifest["project_id"])
        if record["kind"] == "project" and record["identity"] == project_id.value:
            return ProjectRef(project_id)
        if record["kind"] == "variant":
            return VariantRef(VariantId(record["identity"]), project_id)
        raise ValueError("invalid source update record identity")

    def _recover(self, path: str, *, rollback_if_marker_fails: bool = False) -> str:
        manifest = self._parse_manifest(path)
        if manifest["status"] == "committed":
            # Publication is finished. Current records may already contain later
            # valid edits and must no longer participate in recovery comparisons.
            self._cleanup_committed(path, manifest)
            return "committed"
        records = manifest["records"]
        states = []
        for record in records:
            record_path = self._path_for(self._record_ref(manifest, record))
            digest = _digest(self._filesystem.read_bytes(record_path)) if self._filesystem.exists(record_path) else None
            states.append(
                "previous"
                if digest == record["previous_digest"]
                else "target"
                if digest == record["target_digest"]
                else "other"
            )
        if "other" in states or (states[-1] == "target" and any(state != "target" for state in states)):
            raise DomainError(
                ErrorCategory.CONFLICT,
                "SOURCE_UPDATE_RECOVERY_CONFLICT",
                "更新中断后文件被其他操作修改，已保留备份并拒绝覆盖。",
                details={"journal_path": path, "states": states},
            )
        resolution = "committed" if all(state == "target" for state in states) else "rolled-back"
        if resolution == "committed":
            try:
                self._mark_committed(path, manifest)
            except Exception:
                if rollback_if_marker_fails:
                    if self._parse_manifest(path) != manifest:
                        raise _conflict("SOURCE_UPDATE_RECOVERY_CONFLICT", "更新提交标记状态无法确认，已保留恢复记录。")
                    # Do not return success with an ambiguous prepared journal.
                    # Project is restored first, making interrupted rollback safe.
                    self._rollback_records(manifest, states)
                    self._documents.remove_durable(path, f"{manifest['transaction_id']}-rolled-back")
                raise
            self._cleanup_committed(path, manifest)
        else:
            self._rollback_records(manifest, states)
            self._documents.remove_durable(path, f"{manifest['transaction_id']}-resolved")
        return resolution

    def _mark_committed(self, path: str, manifest: dict[str, Any]) -> None:
        committed = {key: value for key, value in manifest.items() if key != "manifest_digest"}
        committed["status"] = "committed"
        committed["manifest_digest"] = _digest(serialize_document(committed))
        payload = serialize_document(committed)
        try:
            self._documents.write_bytes(path, payload, f"{manifest['transaction_id']}-committed", durable=True)
        except Exception:
            # A durable replacement can succeed before its caller observes an
            # error. Only that exact committed payload certifies success.
            if self._filesystem.read_bytes(path) != payload:
                raise

    def _cleanup_committed(self, path: str, manifest: dict[str, Any]) -> None:
        try:
            self._documents.remove_durable(path, f"{manifest['transaction_id']}-resolved")
        except Exception:
            _LOGGER.warning("Committed source update journal cleanup is pending", exc_info=True)

    def _rollback_records(self, manifest: dict[str, Any], states: list[str]) -> None:
        records = manifest["records"]
        for index in reversed(range(len(records))):
            if states[index] != "target":
                continue
            record = records[index]
            record_path = self._path_for(self._record_ref(manifest, record))
            if _digest(self._filesystem.read_bytes(record_path)) != record["target_digest"]:
                raise _conflict("SOURCE_UPDATE_RECOVERY_CONFLICT", "更新回滚期间文件已发生修改。")
            self._documents.write_bytes(
                record_path,
                _decode(record["previous"]),
                f"{manifest['transaction_id']}-restore-{index}",
                durable=True,
            )


def _validate_targets(expected: SourceUpdateRead, project: ProjectDto, variants: tuple[VariantSnapshot, ...]) -> None:
    old = expected.project.envelope
    new = project.envelope
    if old.identity != new.identity or new.revision <= old.revision:
        raise ValueError("source update must advance the same Project revision")
    identities = _variant_ids(expected.project)
    if set(_variant_ids(project)) != set(identities):
        raise ValueError("source update must preserve the complete Variant catalog")
    if len(variants) != len(identities) or {item.ref.identity.value for item in variants} != set(identities):
        raise ValueError("source update must include every Variant exactly once")
    if len(expected.variants) != len(identities) or {item.ref.identity.value for item in expected.variants} != set(
        identities
    ):
        raise ValueError("source update preimages must include every Variant exactly once")
    if len(expected.variant_hashes) != len(identities) or set(dict(expected.variant_hashes)) != set(identities):
        raise ValueError("source update preimage hashes must cover every Variant")
    previous = {item.ref.identity.value: item for item in expected.variants}
    for variant in variants:
        old_variant = previous[variant.ref.identity.value]
        if variant.ref.project_id.value != new.identity or old_variant.ref != variant.ref:
            raise ValueError("source update Variant ownership mismatch")
        if variant.revision <= old_variant.revision:
            raise ValueError("source update must advance every Variant revision")


def _variant_ids(project: ProjectDto) -> tuple[str, ...]:
    values = tuple(VariantId(value).value for value in project.envelope.data["variant_ids"])
    if not values or len(values) != len(set(values)):
        raise ValueError("source update requires a non-empty unique Variant catalog")
    return tuple(sorted(values))


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _decode(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


def _conflict(code: str, message: str) -> DomainError:
    return DomainError(ErrorCategory.CONFLICT, code, message, retryable=True)


__all__ = ["ProjectSourceUpdateStore", "SourceUpdateRead"]
