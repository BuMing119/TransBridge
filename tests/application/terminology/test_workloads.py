from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.terminology.workloads import (
    BuildWorkloadRequest,
    ChangelogRenderWorkloadRequest,
    HistoryCompareWorkloadRequest,
    PublishWorkloadRequest,
    ReportRenderWorkloadRequest,
    TerminologyExpectedState,
    TerminologyPhase,
    TerminologyProgress,
    TerminologyWorkloadType,
    terminology_job_spec,
    terminology_owner,
)


def expected_state() -> TerminologyExpectedState:
    return TerminologyExpectedState(
        project_revision=2,
        variant_revision=3,
        source_graph_digest="graph-v2",
        source_fingerprint_digest="sources-v4",
        effective_version_id="version-effective",
        base_version_id="version-base",
        draft_id="draft-1",
        draft_revision=5,
        build_freshness_digest="fresh-v7",
    )


def requests():
    expected = expected_state()
    return (
        BuildWorkloadRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected,
            build_key="build-key-1",
        ),
        PublishWorkloadRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected,
            build_ref="build:1",
            publish_digest="publish-digest-1",
        ),
        ReportRenderWorkloadRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected,
            report_snapshot_ref="report-snapshot:1",
            report_snapshot_digest="report-digest-1",
        ),
        ChangelogRenderWorkloadRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected,
            changelog_document_ref="changelog:1",
            changelog_document_digest="changelog-digest-1",
        ),
        HistoryCompareWorkloadRequest(
            project_id="project-1",
            variant_id="variant-1",
            expected=expected,
            version_ref="version-1",
            compare_digest="compare-digest-1",
        ),
    )


def test_job_specs_use_immutable_workload_fingerprints_and_project_variant_owner():
    supplied = requests()

    assert {request.workload_type for request in supplied} == set(TerminologyWorkloadType)
    assert terminology_job_spec(supplied[0]).input_fingerprint == "build-key-1"
    assert [terminology_job_spec(request).input_fingerprint for request in supplied[1:]] == [
        "publish-digest-1",
        "report-digest-1",
        "changelog-digest-1",
        "compare-digest-1",
    ]

    owner = terminology_owner(supplied[0], owner_id="operator", entrypoint="gui")
    assert owner.project_id == "project-1"
    assert owner.variant_id == "variant-1"


def test_progress_payload_is_flat_and_keeps_llm_counters_separate():
    payload = TerminologyProgress(
        phase=TerminologyPhase.REDUCE,
        completed=4,
        total=10,
        current_object="plugin.esm",
        reused=2,
        recomputed=2,
        llm_submitted=3,
        llm_completed=1,
        llm_waiting=2,
        llm_retries=1,
        llm_elapsed_ms=2150,
    ).to_payload(heartbeat_sequence=7)

    assert payload["completed"] == 4
    assert payload["llm_completed"] == 1
    assert payload["llm_waiting"] == 2
    assert all(isinstance(value, str | int | float | bool | type(None)) for value in payload.values())


def test_request_and_expected_state_reject_missing_scope_or_guard_values():
    with pytest.raises(ValueError, match="Project and Variant"):
        BuildWorkloadRequest(
            project_id="",
            variant_id="variant-1",
            expected=expected_state(),
            build_key="build-key",
        )


def test_expected_state_digest_covers_effective_base_and_decision_content() -> None:
    state = expected_state()

    assert replace(state, effective_content_digest="changed").digest != state.digest
    assert replace(state, base_content_digest="changed").digest != state.digest
    assert replace(state, decision_set_digest="changed").digest != state.digest
    with pytest.raises(ValueError, match="source graph"):
        TerminologyExpectedState(
            project_revision=0,
            variant_revision=0,
            source_graph_digest="",
            source_fingerprint_digest="sources",
        )
