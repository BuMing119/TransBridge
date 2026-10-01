"""All-Variant source replacement recovery and optimistic concurrency."""

from __future__ import annotations

import base64
from dataclasses import replace
import json
import os

import pytest

from transbridge.application.contracts import DomainError
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.persistence.source_update_store import ProjectSourceUpdateStore
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.repository import ProjectRepository, VariantRepository
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "source-update-test"))
REF = ProjectRef(ProjectId("project-1"))


class Crash(BaseException):
    """Process interruption bypasses ordinary in-process compensation."""


class FaultFilesystem(MemoryFilesystem):
    fail_at: str | None = None
    crash: bool = False
    after_replace: bool = False

    def replace_durable(self, source, destination):
        should_fail = self.canonicalize(destination) == self.fail_at
        if should_fail:
            self.fail_at = None
            if self.after_replace:
                super().replace_durable(source, destination)
            raise Crash() if self.crash else OSError("source update publication fault")
        super().replace_durable(source, destination)


def _setup():
    filesystem = FaultFilesystem()
    projects = ProjectRepository(ROOT, filesystem)
    variants = VariantRepository(ROOT, filesystem)
    project = ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            REF.kind,
            REF.identity.value,
            2,
            {"name": "Test", "sources": [], "variant_ids": ["main", "other"], "active_variant_id": "main"},
        )
    )
    projects.save(REF, project)
    namespace = SourceNamespace("source")
    for identity in ("main", "other"):
        ref = VariantRef(VariantId(identity), REF.identity)
        snapshot = VariantSnapshot(
            ref,
            (SourceFingerprint(namespace, "a" * 64),),
            (VariantEntryState(EntryKey(namespace, "removed"), translation=f"translation-{identity}"),),
            revision=3,
        )
        variants.save(ref, snapshot.to_dto())
    store = ProjectSourceUpdateStore(ROOT, filesystem, projects, variants)
    expected = store.read(REF)
    target_project = ProjectDto(replace(project.envelope, revision=3))
    target_variants = tuple(replace(item, revision=4, entries=()) for item in expected.variants)
    return filesystem, projects, variants, store, expected, target_project, target_variants


def _formal_bytes(filesystem, projects, variants, expected):
    paths = [projects.path_for(REF), *(variants.path_for(item.ref) for item in expected.variants)]
    return {path: filesystem.read_bytes(path) for path in paths}


def test_commit_covers_all_variants_and_preserves_removed_translations_in_backup():
    fs, projects, variants, store, expected, project, targets = _setup()
    before = _formal_bytes(fs, projects, variants, expected)
    backup = store.commit(expected, project, targets, "commit", backup_metadata={"reason": "replace source"})
    assert store.read(REF).variants == targets
    manifest = json.loads(fs.read_bytes(backup))
    assert manifest["metadata"] == {"reason": "replace source"}
    old_payloads = [base64.b64decode(item["previous"]) for item in manifest["records"]]
    assert set(old_payloads) == set(before.values())
    assert any(b"translation-main" in raw for raw in old_payloads)
    assert any(b"translation-other" in raw for raw in old_payloads)
    assert fs.list_files(os.path.join(ROOT, "source-update-journal")) == ()


@pytest.mark.parametrize("target_index", [0, 1, 2])
def test_failed_publication_restores_exact_preimages(target_index):
    fs, projects, variants, store, expected, project, targets = _setup()
    before = _formal_bytes(fs, projects, variants, expected)
    paths = [*(variants.path_for(item.ref) for item in targets), projects.path_for(REF)]
    fs.fail_at = paths[target_index]
    with pytest.raises(OSError, match="publication fault"):
        store.commit(expected, project, targets, "failure")
    assert _formal_bytes(fs, projects, variants, expected) == before
    assert fs.list_files(os.path.join(ROOT, "source-update-journal")) == ()


def test_process_interruption_recovers_before_project_publication():
    fs, projects, variants, store, expected, project, targets = _setup()
    before = _formal_bytes(fs, projects, variants, expected)
    fs.fail_at = projects.path_for(REF)
    fs.crash = True
    with pytest.raises(Crash):
        store.commit(expected, project, targets, "crash")
    assert variants.read_snapshot(targets[0].ref).envelope.revision == 4
    ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    assert _formal_bytes(fs, projects, variants, expected) == before


def test_interruption_after_final_publication_recovers_as_committed():
    fs, projects, variants, store, expected, project, targets = _setup()
    fs.fail_at = projects.path_for(REF)
    fs.crash = True
    fs.after_replace = True
    with pytest.raises(Crash):
        store.commit(expected, project, targets, "committed-crash")
    recovered = ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    assert recovered.read(REF).project == project
    assert recovered.read(REF).variants == targets


def test_error_after_final_publication_returns_committed_backup():
    fs, projects, _, store, expected, project, targets = _setup()
    fs.fail_at = projects.path_for(REF)
    fs.after_replace = True
    backup = store.commit(expected, project, targets, "committed-error")
    assert fs.exists(backup)
    assert store.read(REF).variants == targets


def test_backup_failure_does_not_publish_any_record():
    fs, projects, variants, store, expected, project, targets = _setup()
    before = _formal_bytes(fs, projects, variants, expected)
    original_replace = fs.replace_durable

    def fail_backup(source, destination):
        if "source-updates" in destination:
            raise OSError("backup unavailable")
        original_replace(source, destination)

    fs.replace_durable = fail_backup
    with pytest.raises(OSError, match="backup unavailable"):
        store.commit(expected, project, targets, "failed-backup")
    assert _formal_bytes(fs, projects, variants, expected) == before


def test_prepublication_recheck_detects_edits_to_already_published_variant():
    fs, projects, variants, store, expected, project, targets = _setup()
    original_replace = fs.replace_durable
    other_path = variants.path_for(targets[1].ref)
    main_path = variants.path_for(targets[0].ref)
    project_before = fs.read_bytes(projects.path_for(REF))

    def concurrent_edit(source, destination):
        original_replace(source, destination)
        if fs.canonicalize(destination) == other_path:
            fs.seed(main_path, b"concurrent edit")

    fs.replace_durable = concurrent_edit
    with pytest.raises(DomainError) as error:
        store.commit(expected, project, targets, "mid-commit-edit")
    assert error.value.code == "SOURCE_UPDATE_RECOVERY_CONFLICT"
    assert fs.read_bytes(projects.path_for(REF)) == project_before
    assert fs.read_bytes(main_path) == b"concurrent edit"


def test_stale_same_revision_content_is_rejected_without_writing():
    fs, projects, variants, store, expected, project, targets = _setup()
    changed = replace(expected.variants[1], entries=(replace(expected.variants[1].entries[0], translation="edited"),))
    variants.save(changed.ref, changed.to_dto())
    before = dict(fs.files)
    with pytest.raises(DomainError) as error:
        store.commit(expected, project, targets, "stale")
    assert error.value.code == "SOURCE_UPDATE_PERSISTED_STALE"
    assert fs.files == before


def test_recovery_refuses_third_party_changes_and_retains_journal():
    fs, projects, variants, store, expected, project, targets = _setup()
    fs.fail_at = projects.path_for(REF)
    fs.crash = True
    with pytest.raises(Crash):
        store.commit(expected, project, targets, "conflict")
    changed = replace(targets[0], revision=5)
    variants.save(changed.ref, changed.to_dto())
    before = dict(fs.files)
    with pytest.raises(DomainError) as error:
        ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    assert error.value.code == "SOURCE_UPDATE_RECOVERY_CONFLICT"
    assert fs.files == before


def test_missing_variant_and_foreign_ownership_are_rejected_before_writes():
    fs, _, _, store, expected, project, targets = _setup()
    before = dict(fs.files)
    with pytest.raises(ValueError, match="every Variant"):
        store.commit(expected, project, targets[:1], "missing")
    foreign = replace(targets[0], ref=VariantRef(targets[0].ref.identity, ProjectId("foreign")))
    with pytest.raises(ValueError, match="ownership"):
        store.commit(expected, project, (foreign, targets[1]), "foreign")
    assert fs.files == before


def test_corrupt_recovery_manifest_is_rejected_without_writes():
    fs, projects, variants, store, expected, project, targets = _setup()
    fs.fail_at = projects.path_for(REF)
    fs.crash = True
    with pytest.raises(Crash):
        store.commit(expected, project, targets, "corrupt")
    journal = fs.list_files(os.path.join(ROOT, "source-update-journal"))[0]
    data = json.loads(fs.read_bytes(journal))
    data["project_id"] = "foreign"
    fs.seed(journal, json.dumps(data).encode())
    before = dict(fs.files)
    with pytest.raises(DomainError) as error:
        ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    assert error.value.code == "SOURCE_UPDATE_JOURNAL_INVALID"
    assert fs.files == before


@pytest.mark.parametrize("recover_from_crash", [False, True])
def test_committed_cleanup_failure_allows_later_save_and_restart(recover_from_crash):
    fs, projects, variants, store, expected, project, targets = _setup()
    original_replace = fs.replace_durable

    def fail_cleanup(source, destination):
        if destination.endswith(".removed"):
            raise OSError("cleanup unavailable")
        original_replace(source, destination)

    fs.replace_durable = fail_cleanup
    if recover_from_crash:
        fs.fail_at = projects.path_for(REF)
        fs.crash = True
        fs.after_replace = True
        with pytest.raises(Crash):
            store.commit(expected, project, targets, "cleanup-crash")
        ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    else:
        store.commit(expected, project, targets, "cleanup-error")
    journal = fs.list_files(os.path.join(ROOT, "source-update-journal"))[0]
    assert json.loads(fs.read_bytes(journal))["status"] == "committed"
    changed = replace(targets[0], revision=5)
    variants.save(changed.ref, changed.to_dto())
    project_changed = ProjectDto(replace(project.envelope, revision=4))
    projects.save(REF, project_changed)
    fs.replace_durable = original_replace
    restarted = ProjectSourceUpdateStore(ROOT, fs, projects, variants)
    assert restarted.read(REF).variants[0] == changed
    assert restarted.read(REF).project == project_changed
    assert not fs.exists(journal)


@pytest.mark.parametrize("after_marker_replace", [False, True])
def test_commit_marker_failure_rolls_back_unless_marker_was_durably_published(after_marker_replace):
    fs, projects, variants, store, expected, project, targets = _setup()
    before = _formal_bytes(fs, projects, variants, expected)
    original_replace = fs.replace_durable

    def fail_marker(source, destination):
        if "source-update-journal" in destination and json.loads(fs.read_bytes(source))["status"] == "committed":
            if after_marker_replace:
                original_replace(source, destination)
            raise OSError("commit marker unavailable")
        original_replace(source, destination)

    fs.replace_durable = fail_marker
    if after_marker_replace:
        store.commit(expected, project, targets, "marker-written")
        assert store.read(REF).variants == targets
    else:
        with pytest.raises(OSError, match="commit marker unavailable"):
            store.commit(expected, project, targets, "marker-failed")
        assert _formal_bytes(fs, projects, variants, expected) == before
    assert fs.list_files(os.path.join(ROOT, "source-update-journal")) == ()
