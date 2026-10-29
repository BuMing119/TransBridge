from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import TransitionTarget
from transbridge.persistence.project_lifecycle_loader import V2ProjectCandidateLoader
from transbridge.persistence.v2 import (
    FutureSchemaResult,
    LoadedRecord,
    ProjectDto,
    ProjectId,
    ProjectRef,
    SchemaEnvelope,
    SourceBaseline,
    SourceFingerprint,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)


class _Repository:
    def __init__(self, result) -> None:
        self.result = result

    def load(self, _ref):
        return self.result


def _records():
    project_ref = ProjectRef(ProjectId("project"))
    variant_ref = VariantRef(VariantId("variant"), project_ref.identity)
    project = ProjectDto(
        SchemaEnvelope(
            2,
            project_ref.kind,
            project_ref.identity.value,
            3,
            {
                "name": "项目",
                "sources": [],
                "variant_ids": [variant_ref.identity.value],
                "active_variant_id": None,
            },
        )
    )
    namespace = SourceNamespace.from_fingerprint("json", "a" * 64)
    fingerprint = SourceFingerprint(namespace, "a" * 64)
    baseline_entry = VariantEntryState(EntryKey(namespace, "key"), "")
    stored = VariantSnapshot(
        variant_ref,
        (fingerprint,),
        (VariantEntryState(EntryKey(namespace, "key"), "translated"),),
        revision=8,
    )
    return project_ref, variant_ref, project, stored, SourceBaseline(fingerprint, (baseline_entry,))


def test_v2_loader_prepares_isolated_clean_candidate_from_verified_baseline() -> None:
    project_ref, variant_ref, project, stored, baseline = _records()
    loader = V2ProjectCandidateLoader(
        _Repository(LoadedRecord(project_ref, project, "project-hash")),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda _project, _variant, _context: (baseline,),
    )

    candidate = loader.prepare_candidate(
        TransitionTarget(project_ref, variant_ref),
        RequestContext(owner_id="owner", run_id="run"),
    )

    assert candidate.project is project
    assert candidate.variant is not None
    assert candidate.variant.snapshot().entries[0].translation == "translated"
    assert candidate.variant.revision == 8
    assert candidate.persisted_variant_revision == 8
    assert not candidate.dirty


def test_v2_loader_adds_verified_source_defaults_to_an_empty_saved_variant() -> None:
    project_ref, variant_ref, project, _stored, baseline = _records()
    stored = VariantSnapshot(variant_ref, (), (), revision=8)
    loader = V2ProjectCandidateLoader(
        _Repository(LoadedRecord(project_ref, project, "project-hash")),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda _project, _variant, _context: (baseline,),
    )

    candidate = loader.prepare_candidate(
        TransitionTarget(project_ref, variant_ref),
        RequestContext(owner_id="owner", run_id="run"),
    )

    assert candidate.variant is not None
    assert candidate.variant.snapshot().entries == baseline.entries
    assert candidate.variant.snapshot().source_fingerprints == (baseline.fingerprint,)
    assert candidate.persisted_variant_revision is None
    assert candidate.dirty


@pytest.mark.parametrize("snapshot_ref", [None, "saved-snapshot"])
def test_v2_loader_losslessly_unions_sparse_verified_defaults_with_unavailable_saved_rows(snapshot_ref) -> None:
    project_ref, variant_ref, project, stored, baseline = _records()
    missing_namespace = SourceNamespace("missing:source")
    missing_fingerprint = SourceFingerprint(missing_namespace, None)
    missing = VariantEntryState(EntryKey(missing_namespace, "old"), "keep missing", labels=("saved",))
    tombstone = replace(baseline.entries[0], tombstone=True, translation="keep deleted")
    new_default = replace(baseline.entries[0], entry_key=EntryKey(baseline.fingerprint.namespace, "new"))
    baseline = replace(baseline, entries=(*baseline.entries, new_default))
    stored = replace(
        stored, source_fingerprints=(*stored.source_fingerprints, missing_fingerprint), entries=(tombstone, missing)
    )
    loader = V2ProjectCandidateLoader(
        _Repository(LoadedRecord(project_ref, project, "project-hash")),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda *_: (baseline,),
        snapshot_loader=lambda *_: stored,
    )
    candidate = loader.prepare_candidate(
        TransitionTarget(project_ref, variant_ref, snapshot_ref=snapshot_ref),
        RequestContext(owner_id="owner", run_id="run"),
    )
    result = candidate.variant.snapshot()
    assert set(result.entries) == {tombstone, missing, new_default}
    assert result.source_fingerprints == stored.source_fingerprints
    assert candidate.dirty
    assert missing_namespace.value in {item.namespace for item in candidate.missing_inputs}


def test_v2_loader_only_fills_inferred_fields_from_a_verified_baseline() -> None:
    project_ref, variant_ref, project, stored, baseline = _records()
    saved = replace(stored.entries[0], inferred_fields=("stage", "external_refs"), labels=("explicit",))
    stored = replace(stored, entries=(saved,))
    baseline = replace(baseline, entries=(replace(baseline.entries[0], stage=5, labels=("baseline",)),))
    loader = V2ProjectCandidateLoader(
        _Repository(LoadedRecord(project_ref, project, "project-hash")),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda *_: (baseline,),
    )
    candidate = loader.prepare_candidate(
        TransitionTarget(project_ref, variant_ref), RequestContext(owner_id="owner", run_id="run")
    )
    result = candidate.variant.snapshot().entries[0]
    assert result.stage.value == 5 and result.inferred_fields == ("external_refs",)
    assert result.translation == saved.translation and result.labels == saved.labels
    assert candidate.dirty


def test_v2_loader_preserves_saved_fingerprint_and_keys_without_overlaying_changed_source() -> None:
    project_ref, variant_ref, project, stored, baseline = _records()
    changed = SourceBaseline(
        SourceFingerprint(baseline.fingerprint.namespace, "b" * 64),
        baseline.entries,
    )
    loader = V2ProjectCandidateLoader(
        _Repository(LoadedRecord(project_ref, project, "project-hash")),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda _project, _variant, _context: (changed,),
    )

    candidate = loader.prepare_candidate(
        TransitionTarget(project_ref, variant_ref),
        RequestContext(owner_id="owner", run_id="run"),
    )

    assert candidate.variant.snapshot() == stored
    assert candidate.variant.snapshot().source_fingerprints != (changed.fingerprint,)
    assert candidate.variant.snapshot().entries[0].translation == "translated"
    assert not candidate.dirty


def test_v2_loader_refuses_future_project_schema() -> None:
    project_ref, variant_ref, _project, stored, baseline = _records()
    loader = V2ProjectCandidateLoader(
        _Repository(FutureSchemaResult(project_ref, found_version=99)),
        _Repository(LoadedRecord(variant_ref, stored.to_dto(), "variant-hash")),
        lambda _project, _variant, _context: (baseline,),
    )

    try:
        loader.prepare_candidate(
            TransitionTarget(project_ref, variant_ref),
            RequestContext(owner_id="owner", run_id="run"),
        )
    except DomainError as error:
        assert error.code == "PROJECT_RECORD_UNAVAILABLE"
    else:
        raise AssertionError("future Project schema must fail closed")
