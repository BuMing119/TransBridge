"""Cross-document LifecycleSave concurrency and compensation contracts."""

from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.projects.models import LifecycleProjectUpdate, LifecycleSave
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.lifecycle_transactions import ProjectLifecycleTransactionStore
from transbridge.persistence.v2.models import AtomicWriteError, LoadedRecord, ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.repository import (
    ProjectRepository,
    VariantRepository,
    VariantRevisionConflict,
)
from transbridge.persistence.v2.schema import serialize_document
from transbridge.persistence.v2.variant import VariantSnapshot

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-v2-lifecycle-save"))
PROJECT_REF = ProjectRef(ProjectId("project-1"))
VARIANT_REF = VariantRef(VariantId("main"), PROJECT_REF.identity)


def _project(revision: int) -> ProjectDto:
    return ProjectDto(
        SchemaEnvelope(
            3,
            PROJECT_REF.kind,
            PROJECT_REF.identity.value,
            revision,
            {
                "name": "Project",
                "sources": [],
                "variant_ids": [VARIANT_REF.identity.value],
                "active_variant_id": VARIANT_REF.identity.value,
            },
        )
    )


def _variant(revision: int) -> VariantSnapshot:
    return VariantSnapshot(VARIANT_REF, (), (), revision=revision)


def _repositories(filesystem: MemoryFilesystem):
    projects = ProjectRepository(ROOT, filesystem)
    variants = VariantRepository(ROOT, filesystem)
    assert projects.mutation_lock is variants.mutation_lock
    return projects, variants


def _seed(filesystem: MemoryFilesystem, *, project_revision: int = 3, variant_revision: int = 2):
    projects, variants = _repositories(filesystem)
    projects.save(PROJECT_REF, _project(project_revision))
    variants.save(VARIANT_REF, _variant(variant_revision).to_dto())
    return projects, variants


def _save(
    *,
    project_revision: int = 4,
    variant_revision: int = 3,
    expected_project_revision: int = 3,
    expected_variant_revision: int = 2,
) -> LifecycleSave:
    return LifecycleSave(
        _project(project_revision),
        VARIANT_REF,
        _variant(variant_revision),
        expected_persisted_project_revision=expected_project_revision,
        expected_persisted_revision=expected_variant_revision,
    )


def _commit(store: ProjectLifecycleTransactionStore, transaction_id: str, save: LifecycleSave) -> None:
    store.begin(transaction_id)
    store.stage_save(transaction_id, save)
    store.commit(transaction_id)


def test_variant_repository_conditional_save_rejects_stale_revision_without_write() -> None:
    filesystem = MemoryFilesystem()
    _, variants = _seed(filesystem)
    before = filesystem.read_bytes(variants.path_for(VARIANT_REF))
    call_count = len(filesystem.calls)

    with pytest.raises(VariantRevisionConflict) as error:
        variants.save_if_revision(VARIANT_REF, _variant(3).to_dto(), expected_revision=1)

    assert error.value.expected_revision == 1
    assert error.value.actual_revision == 2
    assert filesystem.read_bytes(variants.path_for(VARIANT_REF)) == before
    assert not any(operation == "replace" for operation, _path in filesystem.calls[call_count:])


def test_lifecycle_save_preflights_both_revisions_and_performs_zero_writes_when_stale() -> None:
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    project_before = filesystem.read_bytes(projects.path_for(PROJECT_REF))
    variant_before = filesystem.read_bytes(variants.path_for(VARIANT_REF))
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    call_count = len(filesystem.calls)

    with pytest.raises(DomainError) as error:
        _commit(
            store,
            "tx-stale-both",
            _save(expected_project_revision=1, expected_variant_revision=1),
        )

    assert error.value.category is ErrorCategory.CONFLICT
    assert error.value.code == "ACTIVE_SAVE_PERSISTED_STALE"
    assert {item["entity_type"] for item in error.value.details["conflicts"]} == {"project", "variant"}
    assert filesystem.read_bytes(projects.path_for(PROJECT_REF)) == project_before
    assert filesystem.read_bytes(variants.path_for(VARIANT_REF)) == variant_before
    formal_paths = {projects.path_for(PROJECT_REF), variants.path_for(VARIANT_REF)}
    assert not any(operation == "replace" and path in formal_paths for operation, path in filesystem.calls[call_count:])


def test_lifecycle_save_publishes_variant_before_project_and_verifies_both() -> None:
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    call_count = len(filesystem.calls)

    _commit(store, "tx-success", _save())

    loaded_project = projects.load(PROJECT_REF)
    loaded_variant = variants.load(VARIANT_REF)
    assert isinstance(loaded_project, LoadedRecord)
    assert isinstance(loaded_variant, LoadedRecord)
    assert loaded_project.value.envelope.revision == 4
    assert loaded_variant.value.envelope.revision == 3
    formal_paths = {projects.path_for(PROJECT_REF), variants.path_for(VARIANT_REF)}
    published = [
        path for operation, path in filesystem.calls[call_count:] if operation == "replace" and path in formal_paths
    ]
    assert published == [variants.path_for(VARIANT_REF), projects.path_for(PROJECT_REF)]


def test_lifecycle_save_restores_verified_variant_preimage_when_project_publish_fails() -> None:
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    project_path = projects.path_for(PROJECT_REF)
    variant_path = variants.path_for(VARIANT_REF)
    project_before = filesystem.read_bytes(project_path)
    variant_before = filesystem.read_bytes(variant_path)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    filesystem.fail_replace_destinations.add(project_path)
    call_count = len(filesystem.calls)

    with pytest.raises(AtomicWriteError):
        _commit(store, "tx-project-failure", _save())

    assert filesystem.read_bytes(project_path) == project_before
    assert filesystem.read_bytes(variant_path) == variant_before
    variant_replaces = [
        path for operation, path in filesystem.calls[call_count:] if operation == "replace" and path == variant_path
    ]
    assert variant_replaces == [variant_path, variant_path]


class _RacingProjectFailureFilesystem(MemoryFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self.project_path: str | None = None
        self.variant_path: str | None = None
        self.external_variant: bytes | None = None
        self.armed = False

    def replace(self, source: str, destination: str) -> None:
        canonical_destination = self.canonicalize(destination)
        if self.armed and canonical_destination == self.project_path:
            assert self.variant_path is not None and self.external_variant is not None
            self.files[self.variant_path] = self.external_variant
            raise OSError("injected Project failure after an external Variant write")
        super().replace(source, destination)


def test_lifecycle_save_compensation_refuses_to_overwrite_later_variant_revision() -> None:
    filesystem = _RacingProjectFailureFilesystem()
    projects, variants = _seed(filesystem)
    filesystem.project_path = projects.path_for(PROJECT_REF)
    filesystem.variant_path = variants.path_for(VARIANT_REF)
    filesystem.external_variant = serialize_document(_variant(4).to_dto().envelope.to_dict())
    filesystem.armed = True
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)

    with pytest.raises(DomainError) as error:
        _commit(store, "tx-compensation-race", _save())

    assert error.value.category is ErrorCategory.CONFLICT
    assert error.value.code == "ACTIVE_SAVE_COMPENSATION_CONFLICT"
    loaded_project = projects.load(PROJECT_REF)
    loaded_variant = variants.load(VARIANT_REF)
    assert isinstance(loaded_project, LoadedRecord)
    assert isinstance(loaded_variant, LoadedRecord)
    assert loaded_project.value.envelope.revision == 3
    assert loaded_variant.value.envelope.revision == 4


class _InjectedProcessStop(BaseException):
    pass


class _ProcessStopFilesystem(MemoryFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self.project_path: str | None = None
        self.armed = False

    def replace(self, source: str, destination: str) -> None:
        if self.armed and self.canonicalize(destination) == self.project_path:
            raise _InjectedProcessStop("simulated process termination between document publications")
        super().replace(source, destination)


class _PostReplaceReadOnceFaultFilesystem(MemoryFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self.fault_destination: str | None = None
        self.armed = False
        self._pending_read_fault: str | None = None

    def replace(self, source: str, destination: str) -> None:
        super().replace(source, destination)
        canonical_destination = self.canonicalize(destination)
        if self.armed and canonical_destination == self.fault_destination:
            self._pending_read_fault = canonical_destination
            self.armed = False

    def read_bytes(self, path: str) -> bytes:
        canonical = self.canonicalize(path)
        if canonical == self._pending_read_fault:
            self._pending_read_fault = None
            raise OSError("injected one-shot post-publication read fault")
        return super().read_bytes(path)


def test_startup_recovers_variant_when_process_stops_between_publications() -> None:
    filesystem = _ProcessStopFilesystem()
    projects, variants = _seed(filesystem)
    project_path = projects.path_for(PROJECT_REF)
    variant_path = variants.path_for(VARIANT_REF)
    project_before = filesystem.read_bytes(project_path)
    variant_before = filesystem.read_bytes(variant_path)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    filesystem.project_path = project_path
    filesystem.armed = True

    with pytest.raises(_InjectedProcessStop):
        _commit(store, "tx-process-stop", _save())

    assert filesystem.read_bytes(project_path) == project_before
    assert filesystem.read_bytes(variant_path) != variant_before
    journal_dir = os.path.join(ROOT, "project-save-journal")
    assert len(filesystem.list_files(journal_dir)) == 1

    filesystem.armed = False
    restarted_projects, restarted_variants = _repositories(filesystem)
    ProjectLifecycleTransactionStore(ROOT, filesystem, restarted_projects, restarted_variants)

    assert filesystem.read_bytes(project_path) == project_before
    assert filesystem.read_bytes(variant_path) == variant_before
    assert filesystem.list_files(journal_dir) == ()


def test_variant_post_publication_read_failure_rolls_back_immediately() -> None:
    filesystem = _PostReplaceReadOnceFaultFilesystem()
    projects, variants = _seed(filesystem)
    project_path = projects.path_for(PROJECT_REF)
    variant_path = variants.path_for(VARIANT_REF)
    project_before = filesystem.read_bytes(project_path)
    variant_before = filesystem.read_bytes(variant_path)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    filesystem.fault_destination = variant_path
    filesystem.armed = True

    with pytest.raises(OSError, match="post-publication read fault"):
        _commit(store, "tx-variant-post-read-fault", _save())

    assert filesystem.read_bytes(project_path) == project_before
    assert filesystem.read_bytes(variant_path) == variant_before
    assert filesystem.list_files(os.path.join(ROOT, "project-save-journal")) == ()


def test_project_post_publication_read_failure_resolves_as_committed() -> None:
    filesystem = _PostReplaceReadOnceFaultFilesystem()
    projects, variants = _seed(filesystem)
    project_path = projects.path_for(PROJECT_REF)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    filesystem.fault_destination = project_path
    filesystem.armed = True

    _commit(store, "tx-project-post-read-fault", _save())

    loaded_project = projects.load(PROJECT_REF)
    loaded_variant = variants.load(VARIANT_REF)
    assert isinstance(loaded_project, LoadedRecord)
    assert isinstance(loaded_variant, LoadedRecord)
    assert loaded_project.value.envelope.revision == 4
    assert loaded_variant.value.envelope.revision == 3
    assert filesystem.list_files(os.path.join(ROOT, "project-save-journal")) == ()


def test_startup_recovery_restores_exact_noncanonical_variant_preimage_bytes() -> None:
    filesystem = _ProcessStopFilesystem()
    projects, variants = _seed(filesystem)
    project_path = projects.path_for(PROJECT_REF)
    variant_path = variants.path_for(VARIANT_REF)
    variant_before = json.dumps(
        _variant(2).to_dto().envelope.to_dict(),
        ensure_ascii=False,
        indent=2,
    ).encode()
    filesystem.seed(variant_path, variant_before)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    filesystem.project_path = project_path
    filesystem.armed = True

    with pytest.raises(_InjectedProcessStop):
        _commit(store, "tx-noncanonical-preimage", _save())

    journal_path = filesystem.list_files(os.path.join(ROOT, "project-save-journal"))[0]
    manifest = json.loads(filesystem.read_bytes(journal_path))
    assert base64.b64decode(manifest["variant"]["previous_bytes_base64"]) == variant_before

    filesystem.armed = False
    restarted_projects, restarted_variants = _repositories(filesystem)
    ProjectLifecycleTransactionStore(ROOT, filesystem, restarted_projects, restarted_variants)

    assert filesystem.read_bytes(variant_path) == variant_before


def test_compensation_refuses_same_revision_with_different_variant_digest() -> None:
    filesystem = _RacingProjectFailureFilesystem()
    projects, variants = _seed(filesystem)
    filesystem.project_path = projects.path_for(PROJECT_REF)
    filesystem.variant_path = variants.path_for(VARIANT_REF)
    external = VariantSnapshot(
        VARIANT_REF,
        (),
        (),
        revision=3,
        label_library=(("external", {"changed": True}),),
    )
    filesystem.external_variant = serialize_document(external.to_dto().envelope.to_dict())
    filesystem.armed = True
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)

    with pytest.raises(DomainError) as error:
        _commit(store, "tx-same-revision-race", _save())

    assert error.value.code == "ACTIVE_SAVE_COMPENSATION_CONFLICT"
    loaded_variant = variants.load(VARIANT_REF)
    assert isinstance(loaded_variant, LoadedRecord)
    assert loaded_variant.value.envelope.revision == 3
    assert loaded_variant.source_hash == hashlib.sha256(filesystem.external_variant).hexdigest()


class _JournalCleanupFaultFilesystem(MemoryFilesystem):
    def __init__(self) -> None:
        super().__init__()
        self.fail_cleanup = True

    def replace_durable(self, source: str, destination: str) -> None:
        canonical_source = self.canonicalize(source)
        if self.fail_cleanup and os.path.basename(os.path.dirname(canonical_source)) == "project-save-journal":
            raise OSError("injected durable journal cleanup fault")
        super().replace_durable(source, destination)


def test_terminal_journal_cleanup_failure_blocks_the_next_save_before_formal_writes() -> None:
    filesystem = _JournalCleanupFaultFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)

    _commit(store, "tx-cleanup-pending", _save())

    journal_dir = os.path.join(ROOT, "project-save-journal")
    assert len(filesystem.list_files(journal_dir)) == 1
    call_count = len(filesystem.calls)
    with pytest.raises(DomainError) as error:
        _commit(
            store,
            "tx-must-not-pass-pending-cleanup",
            _save(
                project_revision=5,
                variant_revision=4,
                expected_project_revision=4,
                expected_variant_revision=3,
            ),
        )

    assert error.value.code == "ACTIVE_SAVE_JOURNAL_CLEANUP_FAILED"

    formal_paths = {projects.path_for(PROJECT_REF), variants.path_for(VARIANT_REF)}
    assert not any(operation == "replace" and path in formal_paths for operation, path in filesystem.calls[call_count:])


def test_pending_journal_blocks_project_update_before_its_formal_write() -> None:
    filesystem = _JournalCleanupFaultFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    _commit(store, "tx-cleanup-before-project-update", _save())
    store.begin("tx-project-update-must-not-bypass")
    store.stage_project_update(
        "tx-project-update-must-not-bypass",
        LifecycleProjectUpdate(_project(5), expected_persisted_project_revision=4),
    )
    call_count = len(filesystem.calls)

    with pytest.raises(DomainError) as error:
        store.commit("tx-project-update-must-not-bypass")

    assert error.value.code == "ACTIVE_SAVE_JOURNAL_CLEANUP_FAILED"
    formal_paths = {projects.path_for(PROJECT_REF), variants.path_for(VARIANT_REF)}
    assert not any(operation == "replace" and path in formal_paths for operation, path in filesystem.calls[call_count:])


def test_startup_rejects_manifest_whose_transaction_identity_does_not_match_filename() -> None:
    filesystem = _ProcessStopFilesystem()
    projects, variants = _seed(filesystem)
    filesystem.project_path = projects.path_for(PROJECT_REF)
    filesystem.armed = True
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)

    with pytest.raises(_InjectedProcessStop):
        _commit(store, "tx-original-name", _save())

    journal_path = filesystem.list_files(os.path.join(ROOT, "project-save-journal"))[0]
    manifest = json.loads(filesystem.read_bytes(journal_path))
    manifest["transaction_id"] = "tx-tampered-name"
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_digest"] = hashlib.sha256(canonical).hexdigest()
    filesystem.seed(
        journal_path,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    )
    filesystem.armed = False
    restarted_projects, restarted_variants = _repositories(filesystem)

    with pytest.raises(DomainError) as error:
        ProjectLifecycleTransactionStore(ROOT, filesystem, restarted_projects, restarted_variants)

    assert error.value.code == "ACTIVE_SAVE_JOURNAL_INVALID"


def test_durable_journal_failure_occurs_before_any_formal_document_write() -> None:
    filesystem = MemoryFilesystem()
    projects, variants = _seed(filesystem)
    store = ProjectLifecycleTransactionStore(ROOT, filesystem, projects, variants)
    journal_name = f"{hashlib.sha256(b'tx-journal-durable-fault').hexdigest()}.json"
    journal_path = os.path.join(ROOT, "project-save-journal", journal_name)
    filesystem.fail_durable_replace_destinations.add(journal_path)
    project_before = filesystem.read_bytes(projects.path_for(PROJECT_REF))
    variant_before = filesystem.read_bytes(variants.path_for(VARIANT_REF))

    with pytest.raises(OSError, match="durable replace fault"):
        _commit(store, "tx-journal-durable-fault", _save())

    assert filesystem.read_bytes(projects.path_for(PROJECT_REF)) == project_before
    assert filesystem.read_bytes(variants.path_for(VARIANT_REF)) == variant_before
