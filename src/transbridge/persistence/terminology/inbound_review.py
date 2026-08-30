"""SQLite persistence for immutable inbound facts and append-only review snapshots."""

from __future__ import annotations

from datetime import datetime
import json
from typing import TYPE_CHECKING, Any

from transbridge.application.terminology.errors import RevisionConflictError, TerminologyNotFoundError
from transbridge.application.terminology.models import DraftRef
from transbridge.application.terminology_sync.inbound import (
    InboundAppliedProposal,
    InboundChangeKind,
    InboundItemDisposition,
    InboundProposedEffect,
    InboundReviewState,
    InboundReviewStatus,
    InboundTerminologyChange,
    InboundTerminologyChangeSet,
    inbound_review_identity,
)
from transbridge.application.terminology_sync.models import TerminologySyncRunOutcome
from transbridge.application.terminology_sync.plan_models import TerminologyContentSummary

if TYPE_CHECKING:
    from .repository import SqliteTerminologyRepository


class SqliteInboundReviewStore:
    """Persist one immutable fact set and CAS-versioned review snapshots."""

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self._repository = repository
        self._connection = repository._connection

    def save_change_set(self, change_set: InboundTerminologyChangeSet) -> InboundTerminologyChangeSet:
        self._repository._require_project(change_set.project_id)
        self._repository._ensure_writable()
        with self._repository._lock, self._repository.transaction():
            return self._save_change_set_unlocked(change_set)

    def _save_change_set_unlocked(
        self,
        change_set: InboundTerminologyChangeSet,
    ) -> InboundTerminologyChangeSet:
        """Write immutable facts inside the caller's repository transaction."""

        self._repository._require_project(change_set.project_id)
        line = self._connection.execute(
            "SELECT project_id, variant_id, target_id, remote_project_id FROM terminology_sync_lines WHERE line_id = ?",
            (change_set.line_id,),
        ).fetchone()
        if line is None:
            raise TerminologyNotFoundError("terminology sync line was not found")
        if (
            str(line["project_id"]) != change_set.project_id
            or str(line["variant_id"]) != change_set.variant_id
            or str(line["target_id"]) != change_set.target_identity
            or int(line["remote_project_id"]) != change_set.remote_project_id
        ):
            raise ValueError("inbound change set does not match its terminology sync line")

        existing = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_inbound_sets WHERE change_set_id = ? AND revision = ?",
            (change_set.change_set_id, change_set.revision),
        ).fetchone()
        if existing is not None:
            return self._decode_change_set(str(existing["payload_json"]))

        review_identity = inbound_review_identity(change_set)
        repeated_rows = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_inbound_sets "
            "WHERE line_id = ? AND status = 'facts' ORDER BY rowid",
            (change_set.line_id,),
        ).fetchall()
        for row in repeated_rows:
            repeated = self._decode_change_set(str(row["payload_json"]))
            if inbound_review_identity(repeated) == review_identity:
                return repeated

        self._connection.execute(
            "INSERT INTO terminology_sync_inbound_sets("
            "change_set_id, revision, line_id, plan_id, status, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                change_set.change_set_id,
                change_set.revision,
                change_set.line_id,
                change_set.plan_id,
                "facts",
                _dumps(change_set.canonical_payload()),
            ),
        )
        self._connection.executemany(
            "INSERT INTO terminology_sync_inbound_items("
            "change_set_id, change_set_revision, item_id, payload_json"
            ") VALUES (?, ?, ?, ?)",
            (
                (
                    change_set.change_set_id,
                    change_set.revision,
                    item.item_id,
                    _dumps(item.canonical_payload()),
                )
                for item in change_set.items
            ),
        )
        self._insert_review(
            change_set.revision,
            InboundReviewState(change_set.change_set_id, 0, InboundReviewStatus.PENDING),
        )
        return change_set

    def get_change_set(self, change_set_id: str) -> InboundTerminologyChangeSet:
        with self._repository._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM terminology_sync_inbound_sets "
                "WHERE change_set_id = ? ORDER BY revision LIMIT 1",
                (change_set_id,),
            ).fetchone()
            if row is None:
                raise TerminologyNotFoundError("inbound change set was not found")
            change_set = self._decode_change_set(str(row["payload_json"]))
            self._repository._require_project(change_set.project_id)
            return change_set

    def list_change_sets(self, project_id: str, variant_id: str) -> tuple[InboundTerminologyChangeSet, ...]:
        self._repository._require_project(project_id)
        if not variant_id.strip():
            raise ValueError("inbound Variant ID must not be empty")
        with self._repository._lock:
            rows = self._connection.execute(
                "SELECT s.payload_json FROM terminology_sync_inbound_sets s "
                "JOIN terminology_sync_lines l ON l.line_id = s.line_id "
                "WHERE l.project_id = ? AND l.variant_id = ? AND s.status = 'facts' "
                "ORDER BY s.rowid DESC",
                (project_id, variant_id),
            ).fetchall()
            return tuple(self._decode_change_set(str(row["payload_json"])) for row in rows)

    def get_review_state(self, change_set_id: str) -> InboundReviewState:
        with self._repository._lock:
            self.get_change_set(change_set_id)
            return self._latest_review(change_set_id)

    def commit_review(
        self,
        change_set_id: str,
        *,
        expected_revision: int,
        dispositions: tuple[InboundItemDisposition, ...],
        applied_proposal: InboundAppliedProposal,
    ) -> InboundReviewState:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("expected review revision must be a non-negative integer")
        dispositions = tuple(dispositions)
        if not dispositions:
            raise ValueError("review commit requires at least one disposition")
        if len({item.item_id for item in dispositions}) != len(dispositions):
            raise ValueError("review commit disposition item IDs must be unique")
        if any(item.proposal_digest != applied_proposal.proposal_digest for item in dispositions):
            raise ValueError("review dispositions must belong to the applied proposal")

        self._repository._ensure_writable()
        with self._repository._lock, self._repository.transaction():
            change_set = self.get_change_set(change_set_id)
            current = self._latest_review(change_set_id)
            replay = next(
                (
                    proposal
                    for proposal in current.applied_proposals
                    if proposal.proposal_digest == applied_proposal.proposal_digest
                ),
                None,
            )
            if replay is not None:
                if replay != applied_proposal:
                    raise ValueError("applied proposal digest identifies different review facts")
                return current
            if current.revision != expected_revision:
                raise RevisionConflictError(expected_revision, current.revision)

            valid_item_ids = {item.item_id for item in change_set.items}
            existing_item_ids = {item.item_id for item in current.dispositions}
            incoming_item_ids = {item.item_id for item in dispositions}
            if not incoming_item_ids <= valid_item_ids:
                raise ValueError("review disposition references an item outside the immutable change set")
            if incoming_item_ids & existing_item_ids:
                raise ValueError("review dispositions are append-only per item")

            merged_dispositions = current.dispositions + dispositions
            status = (
                InboundReviewStatus.REVIEWED
                if len(merged_dispositions) == len(change_set.items)
                else InboundReviewStatus.PARTIALLY_REVIEWED
            )
            updated = InboundReviewState(
                change_set_id,
                current.revision + 1,
                status,
                merged_dispositions,
                current.applied_proposals + (applied_proposal,),
            )
            self._insert_review(change_set.revision, updated)
            return updated

    def _latest_review(self, change_set_id: str) -> InboundReviewState:
        row = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_inbound_reviews "
            "WHERE change_set_id = ? ORDER BY revision DESC LIMIT 1",
            (change_set_id,),
        ).fetchone()
        if row is None:
            raise TerminologyNotFoundError("inbound review state was not found")
        try:
            return _review_from_payload(_loads(str(row["payload_json"])))
        except (KeyError, TypeError, ValueError) as exc:
            self._repository._mark_corrupt("invalid inbound review payload", cause=exc)

    def _decode_change_set(self, payload: str) -> InboundTerminologyChangeSet:
        try:
            return _change_set_from_payload(_loads(payload))
        except (KeyError, TypeError, ValueError) as exc:
            self._repository._mark_corrupt("invalid inbound change-set payload", cause=exc)

    def _insert_review(self, change_set_revision: int, review: InboundReviewState) -> None:
        self._connection.execute(
            "INSERT INTO terminology_sync_inbound_reviews("
            "change_set_id, change_set_revision, revision, status, payload_json"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                review.change_set_id,
                change_set_revision,
                review.revision,
                review.status.value,
                _dumps(_review_payload(review)),
            ),
        )
        self._connection.executemany(
            "INSERT INTO terminology_sync_inbound_dispositions("
            "change_set_id, review_revision, item_id, payload_json"
            ") VALUES (?, ?, ?, ?)",
            (
                (review.change_set_id, review.revision, item.item_id, _dumps(_disposition_payload(item)))
                for item in review.dispositions
            ),
        )
        self._connection.executemany(
            "INSERT INTO terminology_sync_inbound_proposals("
            "change_set_id, review_revision, proposal_digest, payload_json"
            ") VALUES (?, ?, ?, ?)",
            (
                (
                    review.change_set_id,
                    review.revision,
                    proposal.proposal_digest,
                    _dumps(_proposal_payload(proposal)),
                )
                for proposal in review.applied_proposals
            ),
        )


def _change_set_from_payload(payload: dict[str, Any]) -> InboundTerminologyChangeSet:
    return InboundTerminologyChangeSet(
        change_set_id=payload["change_set_id"],
        revision=payload["revision"],
        line_id=payload["line_id"],
        project_id=payload["project_id"],
        variant_id=payload["variant_id"],
        target_identity=payload["target_identity"],
        remote_project_id=payload["remote_project_id"],
        plan_id=payload["plan_id"],
        plan_hash=payload["plan_hash"],
        baseline_revision=payload["baseline_revision"],
        remote_snapshot_digest=payload["remote_snapshot_digest"],
        source_run_id=payload["source_run_id"],
        source_run_outcome=TerminologySyncRunOutcome(payload["source_run_outcome"]),
        items=tuple(_change_from_payload(item) for item in payload["items"]),
        content_digest=payload["content_digest"],
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def _change_from_payload(payload: dict[str, Any]) -> InboundTerminologyChange:
    return InboundTerminologyChange(
        item_id=payload["item_id"],
        kind=InboundChangeKind(payload["kind"]),
        remote_id=payload["remote_id"],
        remote_revision=payload["remote_revision"],
        remote_observed_digest=payload["remote_observed_digest"],
        base_digest=payload["base_digest"],
        local=_summary_from_payload(payload["local"]),
        remote=_summary_from_payload(payload["remote"]),
        local_term_id=payload["local_term_id"],
        proposed_effect=InboundProposedEffect(payload["proposed_effect"]),
        reason=payload["reason"],
        content_digest=payload["content_digest"],
    )


def _summary_from_payload(payload: dict[str, Any] | None) -> TerminologyContentSummary | None:
    if payload is None:
        return None
    return TerminologyContentSummary(
        original=payload["original"],
        normalized_original=payload["normalized_original"],
        translation=payload["translation"],
        scope=payload["scope"],
        suppressed=payload["suppressed"],
        variants=tuple(payload["variants"]),
        case_sensitive=payload["case_sensitive"],
        part_of_speech=payload["part_of_speech"],
        note=payload["note"],
        digest=payload["digest"],
    )


def _review_payload(review: InboundReviewState) -> dict[str, Any]:
    return {
        "change_set_id": review.change_set_id,
        "revision": review.revision,
        "status": review.status.value,
        "dispositions": [_disposition_payload(item) for item in review.dispositions],
        "applied_proposals": [_proposal_payload(item) for item in review.applied_proposals],
    }


def _review_from_payload(payload: dict[str, Any]) -> InboundReviewState:
    return InboundReviewState(
        change_set_id=payload["change_set_id"],
        revision=payload["revision"],
        status=InboundReviewStatus(payload["status"]),
        dispositions=tuple(_disposition_from_payload(item) for item in payload["dispositions"]),
        applied_proposals=tuple(_proposal_from_payload(item) for item in payload["applied_proposals"]),
    )


def _disposition_payload(disposition: InboundItemDisposition) -> dict[str, Any]:
    return {
        "item_id": disposition.item_id,
        "status": disposition.status.value,
        "actor": disposition.actor,
        "occurred_at": disposition.occurred_at.isoformat(),
        "before_digest": disposition.before_digest,
        "after_digest": disposition.after_digest,
        "proposal_digest": disposition.proposal_digest,
        "remote_id": disposition.remote_id,
        "remote_revision": disposition.remote_revision,
        "remote_observed_digest": disposition.remote_observed_digest,
        "action_id": disposition.action_id,
        "reason": disposition.reason,
    }


def _disposition_from_payload(payload: dict[str, Any]) -> InboundItemDisposition:
    return InboundItemDisposition(
        item_id=payload["item_id"],
        status=InboundReviewStatus(payload["status"]),
        actor=payload["actor"],
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        before_digest=payload["before_digest"],
        after_digest=payload["after_digest"],
        proposal_digest=payload["proposal_digest"],
        remote_id=payload["remote_id"],
        remote_revision=payload["remote_revision"],
        remote_observed_digest=payload["remote_observed_digest"],
        action_id=payload["action_id"],
        reason=payload["reason"],
    )


def _proposal_payload(proposal: InboundAppliedProposal) -> dict[str, Any]:
    return {
        "proposal_digest": proposal.proposal_digest,
        "draft_ref": None if proposal.draft_ref is None else _draft_ref_payload(proposal.draft_ref),
        "action_ids": list(proposal.action_ids),
        "committed_at": proposal.committed_at.isoformat(),
    }


def _proposal_from_payload(payload: dict[str, Any]) -> InboundAppliedProposal:
    raw_ref = payload["draft_ref"]
    return InboundAppliedProposal(
        proposal_digest=payload["proposal_digest"],
        draft_ref=None if raw_ref is None else DraftRef(**raw_ref),
        action_ids=tuple(payload["action_ids"]),
        committed_at=datetime.fromisoformat(payload["committed_at"]),
    )


def _draft_ref_payload(ref: DraftRef) -> dict[str, Any]:
    return {
        "draft_id": ref.draft_id,
        "project_id": ref.project_id,
        "variant_id": ref.variant_id,
        "base_version_id": ref.base_version_id,
        "base_content_digest": ref.base_content_digest,
        "revision": ref.revision,
        "decision_set_digest": ref.decision_set_digest,
    }


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(payload: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("inbound persistence payload must be an object")
    return value


__all__ = ["SqliteInboundReviewStore"]
