from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology_sync.inbound import (
    InboundChangeKind,
    InboundProposedEffect,
    InboundTerminologyChangeSet,
    build_inbound_change_set,
)
from transbridge.application.terminology_sync.models import TerminologySyncMode, TerminologySyncRunOutcome
from transbridge.application.terminology_sync.plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)

_NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
_REMOTE_DIGEST = "a" * 64


def _summary(original: str, translation: str) -> TerminologyContentSummary:
    return TerminologyContentSummary(original, original.casefold(), translation, "project")


def _remote(remote_id: int, original: str, translation: str) -> ParaTranzTerm:
    return ParaTranzTerm(
        remote_id,
        TermEntry(original, translation, "paratranz"),
        f"revision-{remote_id}",
        f"{remote_id:064x}",
        {"createdAt": "2026-08-30T00:00:00Z"},
    )


def _plan(*, mode: TerminologySyncMode = TerminologySyncMode.BIDIRECTIONAL) -> TerminologySyncPlan:
    local_update = _summary("Sword", "剑")
    remote_update = _summary("Sword", "长剑")
    local_delete = _summary("Axe", "斧")
    return TerminologySyncPlan(
        line_id="line-1",
        target_identity="target-1",
        binding_revision=2,
        profile_revision=3,
        mode=mode,
        local_project_id="project-1",
        local_variant_id="variant-1",
        local_version_id="version-1",
        local_content_digest="local-digest",
        remote_snapshot_digest=_REMOTE_DIGEST,
        baseline_revision=4,
        items=(
            TerminologySyncPlanItem(
                "item-add",
                TerminologySyncAction.PROPOSE_LOCAL_ADD,
                TerminologySyncReason.INDEPENDENT_REMOTE,
                remote_id=1,
                remote=_summary("Shield", "盾"),
                requires_review=True,
            ),
            TerminologySyncPlanItem(
                "item-update",
                TerminologySyncAction.PROPOSE_LOCAL_UPDATE,
                TerminologySyncReason.REMOTE_CHANGED,
                local_term_id="term-sword",
                remote_id=2,
                base_digest=local_update.digest,
                local=local_update,
                remote=remote_update,
                managed=True,
                requires_review=True,
            ),
            TerminologySyncPlanItem(
                "item-delete",
                TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION,
                TerminologySyncReason.REMOTE_DELETED,
                local_term_id="term-axe",
                remote_id=3,
                base_digest=local_delete.digest,
                local=local_delete,
                managed=True,
                requires_review=True,
            ),
            TerminologySyncPlanItem(
                "item-conflict",
                TerminologySyncAction.CONFLICT,
                TerminologySyncReason.BOTH_CHANGED,
                local_term_id="term-bow",
                remote_id=4,
                local=_summary("Bow", "弓"),
                remote=_summary("Bow", "长弓"),
                requires_review=True,
            ),
        ),
    )


def _snapshot(*, stable: bool = True, digest: str = _REMOTE_DIGEST) -> ParaTranzTermSnapshot:
    return ParaTranzTermSnapshot(
        41,
        (
            _remote(1, "Shield", "盾"),
            _remote(2, "Sword", "长剑"),
            _remote(4, "Bow", "长弓"),
        ),
        digest,
        _NOW,
        stable,
    )


def test_build_freezes_all_inbound_kinds_remote_provenance_and_partial_source() -> None:
    change_set = build_inbound_change_set(
        _plan(),
        _snapshot(),
        source_run_id="run-partial",
        source_run_outcome=TerminologySyncRunOutcome.PARTIAL,
        created_at=_NOW,
    )

    assert [item.kind for item in change_set.items] == [
        InboundChangeKind.REMOTE_ADD,
        InboundChangeKind.REMOTE_CONFLICT,
        InboundChangeKind.REMOTE_DELETE,
        InboundChangeKind.REMOTE_UPDATE,
    ]
    assert [item.proposed_effect for item in change_set.items] == [
        InboundProposedEffect.ADD,
        InboundProposedEffect.CONFLICT,
        InboundProposedEffect.SUPPRESS,
        InboundProposedEffect.UPDATE,
    ]
    assert change_set.source_run_outcome is TerminologySyncRunOutcome.PARTIAL
    assert change_set.project_id == "project-1"
    assert change_set.variant_id == "variant-1"
    assert change_set.items[0].remote_revision == "revision-1"
    assert change_set.items[2].remote_observed_digest is None
    assert change_set.created_at.tzinfo is UTC
    assert change_set.change_set_id.startswith("terminology-sync.inbound-set-id.v1:")
    assert change_set.content_digest.startswith("terminology-sync.inbound-set-content.v1:")


def test_change_set_identity_and_item_identity_are_idempotent_across_repeated_runs() -> None:
    first = build_inbound_change_set(
        _plan(),
        _snapshot(),
        source_run_id="run-1",
        source_run_outcome=TerminologySyncRunOutcome.PARTIAL,
        created_at=_NOW,
    )
    repeated = build_inbound_change_set(
        _plan(),
        _snapshot(),
        source_run_id="run-2",
        source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
        created_at=_NOW + timedelta(minutes=1),
    )

    assert repeated.change_set_id == first.change_set_id
    assert [item.item_id for item in repeated.items] == [item.item_id for item in first.items]
    assert repeated.content_digest != first.content_digest


def test_first_sync_preserves_absent_baseline_revision_in_identity() -> None:
    plan = replace(_plan(), baseline_revision=None, plan_hash="")
    change_set = build_inbound_change_set(
        plan,
        _snapshot(),
        source_run_id="run-first",
        source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
        created_at=_NOW,
    )

    assert change_set.baseline_revision is None
    assert change_set.change_set_id.startswith("terminology-sync.inbound-set-id.v1:")


@pytest.mark.parametrize(
    ("plan", "snapshot", "message"),
    [
        (_plan(mode=TerminologySyncMode.BACKUP), _snapshot(), "bidirectional"),
        (_plan(), _snapshot(stable=False), "blocked or unstable"),
        (_plan(), _snapshot(digest="b" * 64), "no longer matches"),
    ],
)
def test_change_set_rejects_non_bidirectional_unstable_or_stale_inputs(plan, snapshot, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        build_inbound_change_set(
            plan,
            snapshot,
            source_run_id="run-1",
            source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
            created_at=_NOW,
        )


def test_change_set_is_immutable_and_digest_tampering_is_rejected() -> None:
    change_set = build_inbound_change_set(
        _plan(),
        _snapshot(),
        source_run_id="run-1",
        source_run_outcome=TerminologySyncRunOutcome.SUCCEEDED,
        created_at=_NOW,
    )
    with pytest.raises(AttributeError):
        change_set.items = ()
    values = {field: getattr(change_set, field) for field in change_set.__dataclass_fields__}
    values["content_digest"] = "tampered"
    with pytest.raises(ValueError, match="content digest"):
        InboundTerminologyChangeSet(**values)
