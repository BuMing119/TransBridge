"""Persist only confirmed upload identities after one batch of remote writes."""

from collections import defaultdict
from dataclasses import replace
import logging

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.tasks.controls import TaskCleanupFailed

from .models import EntrySummary, SyncAction
from .planner import SyncPlanner

_logger = logging.getLogger(__name__)


def commit_upload_associations(uow, request, confirmed, item_ids):
    positions = defaultdict(list)
    candidate = list(request.current_local_entries)
    for index, entry in enumerate(candidate):
        positions[entry.entry_key].append(index)
    changed = False
    transaction, committed = None, False
    try:
        for item_id, outcome in confirmed.items():
            if outcome.action not in {SyncAction.CREATE_REMOTE, SyncAction.UPDATE_REMOTE}:
                continue
            if outcome.external_ref is None:
                continue
            matches = positions[outcome.entry_key]
            if len(matches) != 1:
                raise RuntimeError("upload association local identity changed")
            current = candidate[matches[0]]
            if current.external_ref == outcome.external_ref:
                continue
            # A retry may publish only the association for the same reviewed source.
            if EntrySummary.from_local(current) != item_ids[item_id].after:
                raise RuntimeError("upload association source changed after the remote commit")
            candidate[matches[0]] = replace(
                current, external_ref=outcome.external_ref, revision=current.revision.next()
            )
            changed = True
        if not changed:
            return None
        expected_hash, _ = SyncPlanner().snapshot_hashes(request.current_local_entries, ())
        transaction = uow.begin(expected_snapshot_hash=expected_hash)
        transaction.stage(tuple(candidate))
        decision = transaction.commit(request.run_id, request.commit_guard)
        if not decision.accepted:
            raise RuntimeError(f"upload association publication rejected: {decision.reason}")
        committed = True
        return None
    except Exception:
        _logger.exception("ParaTranz upload association publication failed (run_id=%s)", request.run_id)
        return Diagnostic(
            "UPLOAD_ASSOCIATION_COMMIT_FAILED",
            "Remote writes succeeded, but their local associations were not saved. Recheck before uploading again.",
            DiagnosticSeverity.ERROR,
            ErrorCategory.CONFLICT,
            True,
        )
    finally:
        if transaction is not None and not committed:
            try:
                transaction.rollback()
            except Exception as exc:
                raise TaskCleanupFailed(
                    f"ParaTranz upload association rollback failed (run_id={request.run_id}): {exc}"
                ) from exc
