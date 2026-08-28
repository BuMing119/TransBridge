from __future__ import annotations

from dataclasses import replace

import pytest

from tests.application.terminology.story08_support import Permit, State, build, decision, draft, expected
from transbridge.application.terminology.models import ArtifactStatus, DraftRef, TerminologyDraft
from transbridge.application.terminology.publish import PublishTerminologyRequest, VersionPublisher
from transbridge.application.terminology.workloads import TerminologyExpectedState
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository


def _publisher(repository, state):
    return VersionPublisher(repository.publisher, State(state), Permit())


def _initial(repository):
    source = build()
    reviewed = draft()
    repository.put_build(source)
    repository.create_draft(reviewed)
    request = PublishTerminologyRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=expected(),
        build_ref=source.ref,
        draft_ref=reviewed.ref,
        version_id="v1",
        published_at="2026-08-28T01:00:00+00:00",
    )
    return _publisher(repository, request.expected).publish(request)


def _second_draft(repository, first):
    reviewed = TerminologyDraft(
        DraftRef(
            "draft-2",
            "project-1",
            "variant-1",
            "v1",
            first.version_ref.content_digest,
            0,
            "decision-set-v2",
        ),
        (decision(translation="巨龙"),),
    )
    repository.create_draft(reviewed)
    state = TerminologyExpectedState(
        8,
        4,
        "source-graph-2",
        "source-fingerprints-2",
        effective_version_id="v1",
        base_version_id="v1",
        draft_id="draft-2",
        draft_revision=0,
        build_freshness_digest="current",
    )
    request = PublishTerminologyRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=state,
        build_ref=build().ref,
        draft_ref=reviewed.ref,
        version_id="v2",
        published_at="2026-08-28T02:00:00+00:00",
    )
    return reviewed, state, request


def test_publish_commits_version_diff_changelog_pending_artifacts_then_pointer(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    result = _initial(repository)

    assert repository.effective_version("project-1", "variant-1").ref == result.version_ref
    assert repository.active_draft("project-1", "variant-1") is None
    assert repository._connection.execute("SELECT count(*) FROM version_terms").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM canonical_diffs").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM changelog_documents").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM changelog_manifests").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM changelog_sections").fetchone()[0] > 0
    manifest = repository.changelogs.get_changelog_manifest(result.changelog.ref)
    assert manifest.section_count("changes") == len(result.changelog.changes)
    assert repository.changelogs.list_changelog_changes(result.changelog.ref).items == result.changelog.changes
    assert repository._connection.execute("SELECT count(*) FROM artifact_ledger").fetchone()[0] == 2
    assert all(
        repository.publisher.get_artifact(item.artifact_id).status is ArtifactStatus.PENDING
        for item in result.artifacts
    )


@pytest.mark.parametrize(
    "failed_step",
    [
        "guard_validated",
        "inputs_validated",
        "version_membership_written",
        "canonical_diff_written",
        "changelog_written",
        "artifact_ledger_written",
        "draft_consumed",
        "effective_pointer_moved",
    ],
)
def test_fault_at_any_publish_step_rolls_back_every_fact_and_preserves_old_pointer(tmp_path, failed_step) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    first = _initial(repository)
    reviewed, state, request = _second_draft(repository, first)

    def fail(step: str) -> None:
        if step == failed_step:
            raise RuntimeError(f"fault:{step}")

    with pytest.raises(RuntimeError, match=f"fault:{failed_step}"):
        _publisher(repository, state).publish(request, fault_injector=fail)

    assert repository.effective_version("project-1", "variant-1").ref == first.version_ref
    assert repository.active_draft("project-1", "variant-1") == reviewed
    assert repository._connection.execute("SELECT count(*) FROM versions").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM canonical_diffs").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM changelog_documents").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM artifact_ledger").fetchone()[0] == 2


def test_rollback_publishes_a_new_child_version_without_rewriting_history(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    first = _initial(repository)
    state = replace(
        expected(draft_id="no-draft", draft_revision=0),
        effective_version_id="v1",
        base_version_id="v1",
    )
    request = PublishTerminologyRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=state,
        build_ref=build().ref,
        rollback_from=first.version_ref,
        version_id="v2-rollback",
        published_at="2026-08-28T03:00:00+00:00",
    )

    rolled_back = _publisher(repository, state).publish(request)

    assert rolled_back.version_ref.version_id == "v2-rollback"
    assert repository.effective_version("project-1", "variant-1").parent_version_id == "v1"
    assert repository.get_version(first.version_ref).ref == first.version_ref
    assert repository._connection.execute("SELECT count(*) FROM versions").fetchone()[0] == 2


def test_renderer_failure_only_changes_mutable_ledger(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    result = _initial(repository)
    publisher = _publisher(repository, expected())
    before = repository.effective_version("project-1", "variant-1")

    failed = publisher.record_renderer_result(result.artifacts[0].artifact_id, diagnostic="renderer unavailable")

    assert failed.status is ArtifactStatus.FAILED
    assert failed.retry_count == 1
    assert repository.effective_version("project-1", "variant-1") == before
    assert repository._connection.execute("SELECT count(*) FROM versions").fetchone()[0] == 1
    assert repository._connection.execute("SELECT count(*) FROM changelog_documents").fetchone()[0] == 1
