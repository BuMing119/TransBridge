"""V2 repository round-trip, migration, quarantine and future-schema contracts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.projects.models import LifecycleProjectUpdate
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    FutureSchemaResult,
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    QuarantineResult,
    ReadOnlyWriteRefused,
    SchemaEnvelope,
    SchemaValidationError,
    SessionDto,
    SessionId,
    SessionRef,
    SessionRepository,
    VariantDto,
    VariantId,
    VariantRef,
    VariantRepository,
)
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore
from transbridge.persistence.v2.repository import VariantRevisionConflict

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-v2-repository"))
FIXTURES = Path(__file__).with_name("fixtures")


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _project_dto(identity: str = "project-1", revision: int = 3) -> ProjectDto:
    return ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            entity_type=ProjectRef(ProjectId(identity)).kind,
            identity=identity,
            revision=revision,
            data={
                "name": "Project",
                "sources": [],
                "variant_ids": ["main"],
                "active_variant_id": "main",
            },
        )
    )


def _variant_dto() -> VariantDto:
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    return VariantDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            ref.kind,
            ref.identity.value,
            2,
            {
                "project_id": ref.project_id.value,
                "translations": {"entry": ""},
                "labels": {"entry": []},
                "label_library": {},
            },
        )
    )


def _session_dto() -> SessionDto:
    ref = SessionRef(SessionId("session-1"))
    return SessionDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            ref.kind,
            ref.identity.value,
            1,
            {
                "name": "Session",
                "messages": [],
                "project_id": "project-1",
                "variant_id": "main",
            },
        )
    )


@pytest.mark.parametrize(
    ("repository", "ref", "dto"),
    (
        (ProjectRepository, ProjectRef(ProjectId("project-1")), _project_dto()),
        (
            VariantRepository,
            VariantRef(VariantId("main"), ProjectId("project-1")),
            _variant_dto(),
        ),
        (SessionRepository, SessionRef(SessionId("session-1")), _session_dto()),
    ),
)
def test_v2_round_trip_uses_staging_replace(repository, ref, dto) -> None:
    filesystem = MemoryFilesystem()
    repo = repository(ROOT, filesystem)

    saved = repo.save(ref, dto)
    loaded = repo.load(ref)

    assert isinstance(saved, LoadedRecord)
    assert isinstance(loaded, LoadedRecord)
    assert loaded.value == dto
    assert any(operation == "replace" and path == repo.path_for(ref) for operation, path in filesystem.calls)
    assert not any(".tmp" in path for path in filesystem.files)


def test_project_remote_binding_schema_round_trip() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    project = _project_dto()
    data = dict(project.envelope.data)
    data["remote_bindings"] = {
        "paratranz": {
            "project_id": 42,
            "project_name": "Cloud",
            "endpoint": "https://paratranz.cn",
            "account_user_id": 7,
            "bound_at": "2026-08-24T10:00:00+08:00",
            "validated_at": None,
        }
    }
    bound = ProjectDto(
        SchemaEnvelope(
            project.envelope.schema_version,
            project.envelope.entity_type,
            project.envelope.identity,
            project.envelope.revision + 1,
            data,
        )
    )

    repo.save(ref, bound)
    loaded = repo.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.value.envelope.data["remote_bindings"] == data["remote_bindings"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("endpoint", "not-a-uri"),
        ("bound_at", "not-a-date"),
    ),
)
def test_project_remote_binding_schema_rejects_invalid_formats(field: str, value: str) -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    project = _project_dto()
    binding = {
        "project_id": 42,
        "project_name": "Cloud",
        "endpoint": "https://paratranz.cn",
        "bound_at": "2026-08-24T10:00:00+08:00",
    }
    binding[field] = value
    data = dict(project.envelope.data)
    data["remote_bindings"] = {"paratranz": binding}
    invalid = ProjectDto(
        SchemaEnvelope(
            project.envelope.schema_version,
            project.envelope.entity_type,
            project.envelope.identity,
            project.envelope.revision + 1,
            data,
        )
    )

    with pytest.raises(SchemaValidationError):
        repo.save(ref, invalid)


def test_project_lifecycle_update_rejects_stale_persisted_revision() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    projects = ProjectRepository(ROOT, filesystem)
    variants = VariantRepository(ROOT, filesystem)
    current = _project_dto(revision=4)
    projects.save(ref, current)
    data = dict(current.envelope.data)
    data["remote_bindings"] = {
        "paratranz": {
            "project_id": 42,
            "project_name": "Cloud",
            "endpoint": "https://paratranz.cn",
        }
    }
    update = LifecycleProjectUpdate(
        ProjectDto(
            SchemaEnvelope(
                current.envelope.schema_version,
                current.envelope.entity_type,
                current.envelope.identity,
                current.envelope.revision + 1,
                data,
            )
        ),
        expected_persisted_project_revision=3,
    )
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    store.begin("tx-stale-binding")
    store.stage_project_update("tx-stale-binding", update)

    with pytest.raises(DomainError) as error:
        store.commit("tx-stale-binding")

    assert error.value.category is ErrorCategory.CONFLICT
    assert error.value.code == "PROJECT_UPDATE_PERSISTED_STALE"
    loaded = projects.load(ref)
    assert isinstance(loaded, LoadedRecord)
    assert loaded.value == current


def test_project_lifecycle_update_commits_matching_persisted_revision() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    projects = ProjectRepository(ROOT, filesystem)
    variants = VariantRepository(ROOT, filesystem)
    current = _project_dto(revision=4)
    projects.save(ref, current)
    data = dict(current.envelope.data)
    data["remote_bindings"] = {
        "paratranz": {
            "project_id": 42,
            "project_name": "Cloud",
            "endpoint": "https://paratranz.cn",
        }
    }
    updated = ProjectDto(
        SchemaEnvelope(
            current.envelope.schema_version,
            current.envelope.entity_type,
            current.envelope.identity,
            current.envelope.revision + 1,
            data,
        )
    )
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    store.begin("tx-current-binding")
    store.stage_project_update(
        "tx-current-binding",
        LifecycleProjectUpdate(updated, expected_persisted_project_revision=4),
    )

    store.commit("tx-current-binding")

    loaded = projects.load(ref)
    assert isinstance(loaded, LoadedRecord)
    assert loaded.value == updated


def test_variant_conditional_save_rejects_matching_revision_with_wrong_digest() -> None:
    filesystem = MemoryFilesystem()
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    variants = VariantRepository(ROOT, filesystem)
    current = _variant_dto()
    variants.save(ref, current)
    before = filesystem.read_bytes(variants.path_for(ref))

    with pytest.raises(VariantRevisionConflict) as error:
        variants.save_if_revision(
            ref,
            current,
            expected_revision=current.envelope.revision,
            expected_source_hash="0" * 64,
        )

    assert error.value.actual_revision == current.envelope.revision
    assert error.value.actual_source_hash == hashlib.sha256(before).hexdigest()
    assert filesystem.read_bytes(variants.path_for(ref)) == before


@pytest.mark.parametrize(
    ("repository", "ref", "fixture"),
    (
        (ProjectRepository, ProjectRef(ProjectId("project-1")), "project-v1.json"),
        (
            VariantRepository,
            VariantRef(VariantId("main"), ProjectId("project-1")),
            "variant-v1.json",
        ),
        (SessionRepository, SessionRef(SessionId("session-1")), "session-v1.json"),
    ),
)
def test_v1_migration_is_verified_deterministic_and_idempotent(repository, ref, fixture) -> None:
    filesystem = MemoryFilesystem()
    repo = repository(ROOT, filesystem)
    path = repo.path_for(ref)
    original = _fixture(fixture)
    filesystem.seed(path, original)

    first = repo.load(ref)
    migrated_bytes = filesystem.read_bytes(path)
    second = repo.load(ref)

    assert isinstance(first, LoadedRecord) and first.migrated is True
    assert first.migration_report is not None
    assert filesystem.read_bytes(first.migration_report.backup_path) == original
    assert first.migration_report.original_hash == hashlib.sha256(original).hexdigest()
    assert json.loads(migrated_bytes)["schema_version"] == SCHEMA_VERSION
    assert isinstance(second, LoadedRecord) and second.migrated is False
    assert filesystem.read_bytes(path) == migrated_bytes


def test_variant_migration_preserves_explicit_empty_translation_and_marks_unknown_history() -> None:
    filesystem = MemoryFilesystem()
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    repo = VariantRepository(ROOT, filesystem)
    filesystem.seed(repo.path_for(ref), _fixture("variant-v1.json"))

    result = repo.load(ref)

    assert isinstance(result, LoadedRecord)
    assert result.value.envelope.data["translations"]["entry-2"] == ""
    assert result.value.envelope.data["legacy"]["stage"] == "unknown"
    assert result.value.envelope.data["labels"]["entry-1"] == ["reviewed"]


def test_legacy_display_variant_name_maps_to_deterministic_opaque_id() -> None:
    filesystem = MemoryFilesystem()
    legacy_name = "版本 One"
    mapped_id = f"legacy-{hashlib.sha256(legacy_name.encode('utf-8')).hexdigest()[:20]}"
    ref = VariantRef(VariantId(mapped_id), ProjectId("project-1"))
    repo = VariantRepository(ROOT, filesystem)
    document = {
        "variant": legacy_name,
        "translations": {"entry": "translation"},
        "labels": {},
        "label_library": {},
    }
    filesystem.seed(repo.path_for(ref), json.dumps(document, ensure_ascii=False).encode())

    result = repo.load(ref)

    assert isinstance(result, LoadedRecord)
    assert result.migrated is True
    assert result.value.envelope.identity == mapped_id


def test_unknown_future_schema_is_read_only_and_not_rewritten() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    raw = json.dumps({
        "schema_version": 99,
        "entity_type": "project",
        "id": "project-1",
        "revision": 9,
        "data": {"future": True},
    }).encode()
    filesystem.seed(repo.path_for(ref), raw)
    calls_before = len(filesystem.calls)

    result = repo.load(ref)

    assert isinstance(result, FutureSchemaResult)
    assert result.read_only is True
    assert filesystem.read_bytes(repo.path_for(ref)) == raw
    assert not any(operation in {"write", "replace", "remove"} for operation, _ in filesystem.calls[calls_before:])

    save_calls_before = len(filesystem.calls)
    with pytest.raises(ReadOnlyWriteRefused, match="future-schema") as raised:
        repo.save(ref, _project_dto())
    assert raised.value.code == "FUTURE_SCHEMA_READ_ONLY"
    assert filesystem.read_bytes(repo.path_for(ref)) == raw
    assert not any(operation in {"write", "replace"} for operation, _ in filesystem.calls[save_calls_before:])


@pytest.mark.parametrize(
    "document",
    (
        {"name": "missing messages", "session_id": "session-1"},
        {"session_id": "../escape", "name": "bad", "messages": []},
        {"session_id": "other", "name": "forged", "messages": []},
    ),
)
def test_invalid_or_forged_v1_is_quarantined_without_changing_source(document: dict[str, Any]) -> None:
    filesystem = MemoryFilesystem()
    ref = SessionRef(SessionId("session-1"))
    repo = SessionRepository(ROOT, filesystem)
    path = repo.path_for(ref)
    raw = json.dumps(document).encode()
    filesystem.seed(path, raw)

    result = repo.load(ref)

    assert isinstance(result, QuarantineResult)
    assert filesystem.read_bytes(path) == raw
    assert filesystem.read_bytes(result.quarantine.path) == raw
    assert os.path.commonpath((ROOT, result.quarantine.path)) == ROOT
    assert result.quarantine.source_retained is True


def test_v2_internal_id_and_reference_mismatch_is_quarantined() -> None:
    filesystem = MemoryFilesystem()
    ref = VariantRef(VariantId("main"), ProjectId("project-1"))
    repo = VariantRepository(ROOT, filesystem)
    document = _variant_dto().envelope.to_dict()
    document["id"] = "other"
    document["data"]["project_id"] = "other-project"
    raw = json.dumps(document).encode()
    filesystem.seed(repo.path_for(ref), raw)

    result = repo.load(ref)

    assert isinstance(result, QuarantineResult)
    assert result.reason_code == "REFERENCE_ID_MISMATCH"
    assert filesystem.read_bytes(repo.path_for(ref)) == raw


def test_broken_project_active_variant_reference_is_quarantined() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project-1"))
    repo = ProjectRepository(ROOT, filesystem)
    document = _project_dto().envelope.to_dict()
    document["data"]["active_variant_id"] = "missing"
    raw = json.dumps(document).encode()
    filesystem.seed(repo.path_for(ref), raw)

    result = repo.load(ref)

    assert isinstance(result, QuarantineResult)
    assert result.reason_code == "BROKEN_ACTIVE_VARIANT_REFERENCE"
    assert filesystem.read_bytes(repo.path_for(ref)) == raw

    with pytest.raises(ReadOnlyWriteRefused) as raised:
        repo.save(ref, _project_dto())
    assert raised.value.code == "INVALID_EXISTING_RECORD"
    assert filesystem.read_bytes(repo.path_for(ref)) == raw
