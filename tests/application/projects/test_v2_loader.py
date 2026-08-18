from __future__ import annotations

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


def test_v2_loader_marks_materialized_content_change_for_persistence() -> None:
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
    assert candidate.persisted_variant_revision is None
    assert candidate.dirty


def test_v2_loader_refuses_fingerprint_conflict_without_blind_local_key_overlay() -> None:
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

    try:
        loader.prepare_candidate(
            TransitionTarget(project_ref, variant_ref),
            RequestContext(owner_id="owner", run_id="run"),
        )
    except DomainError as error:
        assert error.code == "VARIANT_SOURCE_FINGERPRINT_CONFLICT"
    else:
        raise AssertionError("fingerprint conflict must fail closed")


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
