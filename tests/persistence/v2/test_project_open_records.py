"""Opening current records keeps one verified read and exact publication guards."""

from dataclasses import replace
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.models import LifecycleActivation, TransitionTarget
from transbridge.persistence.project_lifecycle_loader import V2ProjectCandidateLoader
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    ProjectRepository,
    SchemaEnvelope,
    SourceBaseline,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantRepository,
    VariantSnapshot,
)
from transbridge.persistence.v2.project_activation_journal import ProjectActivationProtocol
from transbridge.persistence.v2.schema import serialize_document

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-project-open-records"))


class ReplacingReadFilesystem(MemoryFilesystem):
    """Emulate an external writer replacing a record just after a completed read."""

    def __init__(self):
        super().__init__()
        self.replace_after_read = {}

    def read_bytes(self, path):
        raw = super().read_bytes(path)
        replacement = self.replace_after_read.pop(self.canonicalize(path), None)
        if replacement is not None:
            self.seed(path, replacement)
        return raw


@pytest.fixture
def records():
    filesystem = ReplacingReadFilesystem()
    project_ref = ProjectRef(ProjectId("project"))
    variant_ref = VariantRef(VariantId("variant"), project_ref.identity)
    project = ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            project_ref.kind,
            project_ref.identity.value,
            3,
            {
                "name": "Project",
                "sources": [],
                "source_relations": [],
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": variant_ref.identity.value,
            },
        )
    )
    namespace = SourceNamespace.from_fingerprint("json", "a" * 64)
    fingerprint = SourceFingerprint(namespace, "a" * 64)
    entries = tuple(VariantEntryState(EntryKey(namespace, key), f"translated-{key}") for key in ("a", "b"))
    variant = VariantSnapshot(variant_ref, (fingerprint,), entries, revision=8)
    baseline = SourceBaseline(fingerprint, tuple(replace(entry, translation="") for entry in entries))
    projects = ProjectRepository(ROOT, filesystem)
    variants = VariantRepository(ROOT, filesystem)
    filesystem.seed(projects.path_for(project_ref), serialize_document(project.envelope.to_dict()))
    filesystem.seed(variants.path_for(variant_ref), serialize_document(variant.to_dto().envelope.to_dict()))
    return SimpleNamespace(
        filesystem=filesystem,
        project_ref=project_ref,
        variant_ref=variant_ref,
        project=project,
        variant=variant,
        baseline=baseline,
        projects=projects,
        variants=variants,
        context=RequestContext("test", run_id="open"),
        target=TransitionTarget(project_ref, variant_ref),
    )


def _loader(records, **kwargs):
    return V2ProjectCandidateLoader(
        records.projects,
        records.variants,
        lambda _project, _variant, _context: (records.baseline,),
        **kwargs,
    )


def _activation(records):
    return ProjectActivationProtocol(ROOT, records.filesystem, records.projects, records.variants)


@pytest.mark.parametrize("normalization", ["missing_external_refs", "entry_order"])
def test_opening_normalizable_current_variant_preserves_record_bytes(records, normalization):
    document = records.variant.to_dto().envelope.to_dict()
    if normalization == "missing_external_refs":
        for entry in document["data"]["entries"]:
            entry.pop("external_refs")
            entry["inferred_fields"] = ["provenance"]
    else:
        document["data"]["entries"].reverse()
    path = records.variants.path_for(records.variant_ref)
    original = serialize_document(document)
    records.filesystem.seed(path, original)
    before = dict(records.filesystem.files)

    candidate = _loader(records).prepare_candidate(records.target, records.context)

    assert not candidate.dirty
    assert records.filesystem.files == before
    assert candidate.source_variant_hash == hashlib.sha256(original).hexdigest()
    assert [entry.translation for entry in candidate.variant.snapshot().entries] == ["translated-a", "translated-b"]
    if normalization == "missing_external_refs":
        for entry in candidate.variant.snapshot().entries:
            assert entry.external_refs == ()
            assert entry.inferred_fields == ("external_refs", "provenance")
    _activation(records).commit(LifecycleActivation.capture(None, candidate), "open-normalized")

    assert records.filesystem.read_bytes(path) == original
    assert all(records.filesystem.files[key] == raw for key, raw in before.items())


def test_candidate_retains_its_loaded_digest_and_rejects_a_later_external_write(records):
    path = records.variants.path_for(records.variant_ref)
    original = records.filesystem.read_bytes(path)
    changed = replace(
        records.variant,
        entries=tuple(replace(entry, translation="external edit") for entry in records.variant.entries),
    )
    replacement = serialize_document(changed.to_dto().envelope.to_dict())
    records.filesystem.replace_after_read[path] = replacement

    candidate = _loader(records).prepare_candidate(records.target, records.context)

    assert candidate.source_variant_hash == hashlib.sha256(original).hexdigest()
    assert candidate.variant.snapshot().entries == records.variant.entries
    with pytest.raises(DomainError) as error:
        _activation(records).commit(LifecycleActivation.capture(None, candidate), "stale-open")
    assert error.value.code == "PROJECT_ACTIVATION_STALE"
    assert records.filesystem.read_bytes(path) == replacement
    assert not records.filesystem.exists(os.path.join(ROOT, "active-project.json"))


def test_snapshot_candidate_keeps_formal_record_digest_and_revision(records):
    snapshot = replace(
        records.variant,
        revision=4,
        entries=tuple(replace(entry, translation="snapshot") for entry in records.variant.entries),
    )
    formal_raw = records.filesystem.read_bytes(records.variants.path_for(records.variant_ref))
    loader = _loader(records, snapshot_loader=lambda _identity, _ref, _context: snapshot)

    candidate = loader.prepare_candidate(replace(records.target, snapshot_ref="saved"), records.context)

    assert candidate.variant.snapshot() == snapshot
    assert candidate.source_ref == "saved"
    assert candidate.source_variant_revision == records.variant.revision
    assert candidate.source_variant_hash == hashlib.sha256(formal_raw).hexdigest()


@pytest.mark.parametrize("entity", ["project", "variant"])
def test_load_returns_one_consistent_read_and_does_not_cache_the_next_load(records, entity):
    repository = records.projects if entity == "project" else records.variants
    ref = records.project_ref if entity == "project" else records.variant_ref
    dto = records.project if entity == "project" else records.variant.to_dto()
    path = repository.path_for(ref)
    original = records.filesystem.read_bytes(path)
    changed = replace(dto, envelope=replace(dto.envelope, revision=dto.envelope.revision + 1))
    replacement = serialize_document(changed.envelope.to_dict())
    records.filesystem.replace_after_read[path] = replacement

    loaded = repository.load(ref)
    next_load = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.value == dto
    assert loaded.source_hash == hashlib.sha256(original).hexdigest()
    assert isinstance(next_load, LoadedRecord)
    assert next_load.value == changed
    assert next_load.source_hash == hashlib.sha256(replacement).hexdigest()


@pytest.mark.parametrize("entity", ["project", "variant"])
@pytest.mark.parametrize("damage", ["invalid", "legacy"])
def test_recovery_replaces_preflight_bytes_before_loading_or_migrating(records, entity, damage):
    repository = records.projects if entity == "project" else records.variants
    ref = records.project_ref if entity == "project" else records.variant_ref
    path = repository.path_for(ref)
    recovered = records.filesystem.read_bytes(path)
    raw = b"{" if damage == "invalid" else (Path(__file__).with_name("fixtures") / f"{entity}-v1.json").read_bytes()
    records.filesystem.seed(path, raw)

    class Recovery:
        def run(self):
            records.filesystem.seed(path, recovered)

    recovery = Recovery()
    repository.mutation_lock.add_recovery(recovery.run)
    loaded = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.source_hash == hashlib.sha256(recovered).hexdigest()
    assert not loaded.migrated
    assert records.filesystem.read_bytes(path) == recovered
    assert not any("quarantine" in key or "backup" in key for key in records.filesystem.files)


@pytest.mark.parametrize("entity", ["project", "variant"])
def test_unchanged_activation_still_checks_exact_preimage(records, entity):
    candidate = _loader(records).prepare_candidate(records.target, records.context)
    repository = records.projects if entity == "project" else records.variants
    ref = records.project_ref if entity == "project" else records.variant_ref
    path = repository.path_for(ref)
    dto = records.project if entity == "project" else records.variant.to_dto()
    changed = replace(dto, envelope=replace(dto.envelope, revision=dto.envelope.revision + 1))
    replacement = serialize_document(changed.envelope.to_dict())
    records.filesystem.replace_after_read[path] = replacement

    with pytest.raises(DomainError) as error:
        _activation(records).commit(LifecycleActivation.capture(None, candidate), f"changed-{entity}-preimage")

    assert error.value.code == "ACTIVE_SAVE_PREIMAGE_CHANGED"
    assert records.filesystem.read_bytes(path) == replacement
    assert not records.filesystem.exists(os.path.join(ROOT, "active-project.json"))
