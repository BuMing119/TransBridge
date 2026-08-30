from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.errors import RevisionConflictError
from transbridge.application.terminology.models import DraftRef
from transbridge.application.terminology_sync.identity import sync_line_id
from transbridge.application.terminology_sync.inbound import (
    InboundAppliedProposal,
    InboundItemDisposition,
    InboundReviewStatus,
    build_inbound_change_set,
)
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncLine,
    TerminologySyncMode,
    TerminologySyncProfile,
    TerminologySyncRunOutcome,
    TerminologySyncRunRecord,
    TerminologySyncTarget,
)
from transbridge.application.terminology_sync.plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)
from transbridge.persistence.terminology import SqliteTerminologyRepository

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_REMOTE_DIGEST = "a" * 64


def _line() -> tuple[TerminologySyncLine, TerminologySyncProfile]:
    target = TerminologySyncTarget("https://example.com/api", 7, 41)
    line_id = sync_line_id(
        project_id="project-1",
        variant_id="variant-1",
        target_identity=target.target_id,
        profile_revision=0,
    )
    return (
        TerminologySyncLine(line_id, "project-1", "variant-1", target, 0, _NOW.isoformat()),
        TerminologySyncProfile(line_id, 0),
    )


def _change_set(
    line: TerminologySyncLine,
    *,
    run_id: str = "run-1",
    minute: int = 0,
    baseline_revision: int | None = None,
):
    summaries = (
        TerminologyContentSummary("Shield", "shield", "盾", "project"),
        TerminologyContentSummary("Sword", "sword", "剑", "project"),
    )
    plan = TerminologySyncPlan(
        line_id=line.line_id,
        target_identity=line.target.target_id,
        binding_revision=0,
        profile_revision=0,
        mode=TerminologySyncMode.BIDIRECTIONAL,
        local_project_id="project-1",
        local_variant_id="variant-1",
        local_version_id="version-1",
        local_content_digest="local-digest",
        remote_snapshot_digest=_REMOTE_DIGEST,
        baseline_revision=baseline_revision,
        items=tuple(
            TerminologySyncPlanItem(
                f"item-{remote_id}",
                TerminologySyncAction.PROPOSE_LOCAL_ADD,
                TerminologySyncReason.INDEPENDENT_REMOTE,
                remote_id=remote_id,
                remote=summary,
                requires_review=True,
            )
            for remote_id, summary in enumerate(summaries, 1)
        ),
    )
    snapshot = ParaTranzTermSnapshot(
        41,
        tuple(
            ParaTranzTerm(
                remote_id,
                TermEntry(summary.original, summary.translation, "paratranz"),
                f"revision-{remote_id}",
                f"{remote_id:064x}",
                {"createdAt": "2026-08-30T00:00:00Z"},
            )
            for remote_id, summary in enumerate(summaries, 1)
        ),
        _REMOTE_DIGEST,
        _NOW,
        True,
    )
    return build_inbound_change_set(
        plan,
        snapshot,
        source_run_id=run_id,
        source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
        created_at=_NOW + timedelta(minutes=minute),
    )


def _proposal(digest: str = "proposal-1") -> InboundAppliedProposal:
    return InboundAppliedProposal(
        digest,
        DraftRef("draft-1", "project-1", "variant-1", None, "base-digest", 1, "decision-digest"),
        ("action-1",),
        _NOW,
    )


def _sync_commit(line: TerminologySyncLine) -> TerminologySyncCommit:
    run = TerminologySyncRunRecord(
        "run-atomic",
        line.line_id,
        "plan-atomic",
        "owner-1",
        line.target.target_id,
        None,
        TerminologySyncRunOutcome.SUCCEEDED,
        _NOW.isoformat(),
        (_NOW + timedelta(seconds=1)).isoformat(),
    )
    baseline = TerminologySyncBaseline(
        line.line_id,
        0,
        "version-1",
        "local-digest",
        _REMOTE_DIGEST,
        "common-digest",
        run.run_id,
    )
    return TerminologySyncCommit(run, (), baseline)


def _disposition(item_id: str, *, proposal_digest: str = "proposal-1") -> InboundItemDisposition:
    return InboundItemDisposition(
        item_id=item_id,
        status=InboundReviewStatus.ACCEPTED,
        actor="reviewer-1",
        occurred_at=_NOW,
        before_digest="before-digest",
        after_digest="after-digest",
        proposal_digest=proposal_digest,
        remote_id=int(item_id.removeprefix("item-")),
        remote_revision="remote-revision",
        remote_observed_digest="remote-digest",
        action_id=f"action-{item_id}",
    )


def test_change_set_and_review_round_trip_across_reopen_and_preserve_first_writer(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    first = _change_set(line)
    repeated = _change_set(line, run_id="run-2", minute=1, baseline_revision=0)
    assert repeated.change_set_id != first.change_set_id
    try:
        repository.sync_state.activate_line(line, profile)
        assert repository.inbound_reviews.save_change_set(first) == first
        assert repository.inbound_reviews.save_change_set(repeated) == first
        assert repository.inbound_reviews.list_change_sets("project-1", "variant-1") == (first,)
        assert repository.inbound_reviews.list_change_sets("project-1", "other-variant") == ()
        assert repository.inbound_reviews.get_review_state(first.change_set_id).status is InboundReviewStatus.PENDING
    finally:
        repository.close()

    reopened = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        assert reopened.inbound_reviews.get_change_set(first.change_set_id) == first
        assert reopened.inbound_reviews.get_review_state(first.change_set_id).revision == 0
    finally:
        reopened.close()


def test_review_commit_is_append_only_cas_and_proposal_replay_is_idempotent(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    change_set = _change_set(line)
    first_proposal = _proposal()
    first_disposition = _disposition("item-1")
    try:
        repository.sync_state.activate_line(line, profile)
        repository.inbound_reviews.save_change_set(change_set)
        partial = repository.inbound_reviews.commit_review(
            change_set.change_set_id,
            expected_revision=0,
            dispositions=(first_disposition,),
            applied_proposal=first_proposal,
        )
        assert partial.revision == 1
        assert partial.status is InboundReviewStatus.PARTIALLY_REVIEWED

        assert (
            repository.inbound_reviews.commit_review(
                change_set.change_set_id,
                expected_revision=0,
                dispositions=(first_disposition,),
                applied_proposal=first_proposal,
            )
            == partial
        )
        with pytest.raises(RevisionConflictError):
            repository.inbound_reviews.commit_review(
                change_set.change_set_id,
                expected_revision=0,
                dispositions=(_disposition("item-2", proposal_digest="proposal-2"),),
                applied_proposal=_proposal("proposal-2"),
            )

        final = repository.inbound_reviews.commit_review(
            change_set.change_set_id,
            expected_revision=1,
            dispositions=(_disposition("item-2", proposal_digest="proposal-2"),),
            applied_proposal=replace(_proposal("proposal-2"), action_ids=("action-2",)),
        )
        assert final.revision == 2
        assert final.status is InboundReviewStatus.REVIEWED
        assert len(final.dispositions) == 2
        assert len(final.applied_proposals) == 2
    finally:
        repository.close()


def test_invalid_review_item_rolls_back_without_a_new_snapshot(tmp_path: Path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    change_set = _change_set(line)
    try:
        repository.sync_state.activate_line(line, profile)
        repository.inbound_reviews.save_change_set(change_set)
        with pytest.raises(ValueError, match="outside"):
            repository.inbound_reviews.commit_review(
                change_set.change_set_id,
                expected_revision=0,
                dispositions=(_disposition("item-999"),),
                applied_proposal=_proposal(),
            )
        assert repository.inbound_reviews.get_review_state(change_set.change_set_id).revision == 0
    finally:
        repository.close()


def test_bidirectional_run_and_inbound_facts_rollback_as_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    line, profile = _line()
    change_set = _change_set(line, run_id="run-atomic")
    commit = _sync_commit(line)
    try:
        repository.sync_state.activate_line(line, profile)
        original = repository.inbound_reviews._insert_review

        def fail_review(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("injected inbound persistence failure")

        monkeypatch.setattr(repository.inbound_reviews, "_insert_review", fail_review)
        with pytest.raises(RuntimeError, match="injected"):
            repository.sync_state.commit_run_with_inbound(
                commit,
                change_set,
                expected_baseline_revision=None,
            )

        assert repository.sync_state.get_baseline(line.line_id) is None
        assert repository.inbound_reviews.list_change_sets("project-1", "variant-1") == ()

        monkeypatch.setattr(repository.inbound_reviews, "_insert_review", original)
        repository.sync_state.commit_run_with_inbound(
            commit,
            change_set,
            expected_baseline_revision=None,
        )
        assert repository.sync_state.get_baseline(line.line_id) == commit.baseline
        assert repository.inbound_reviews.list_change_sets("project-1", "variant-1") == (change_set,)
    finally:
        repository.close()
