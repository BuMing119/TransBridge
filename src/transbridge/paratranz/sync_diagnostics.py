"""Single-line diagnostic logging of a completed ParaTranz preflight.

Only planner evidence is projected. This module never performs matching, changes
plans, writes remote data, or serializes translation bodies into diagnostics.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
from uuid import uuid4

from transbridge.application.io.identity import EntryKey, ExternalEntryRef
from transbridge.application.security.redaction import SecretRedactor
from transbridge.application.sync.download_review import DownloadCandidate, DownloadReviewRow
from transbridge.application.sync.models import LocalEntrySnapshot, SyncAction, SyncOperation, SyncPlan, SyncPlanItem

from .sync_diagnostic_reasons import explain_sync_reason

_logger = logging.getLogger(__name__)
_CONTEXT_PREVIEW_LIMIT = 240


def log_sync_diagnostics(
    plan: SyncPlan,
    *,
    project_id: int,
    local_count: int,
    stale_decisions: bool = False,
) -> str:
    """Log one check summary and every unresolved plan item at INFO level.

    A fresh check ID distinguishes repeated checks of an unchanged plan. Counts
    refer to plan items; duplicate local occurrences are recorded separately.
    """
    check_id = f"sync-check-{uuid4().hex}"
    if not _logger.isEnabledFor(logging.INFO):
        return check_id
    unresolved = tuple(item for item in plan.items if item.action is SyncAction.CONFLICT)
    reasons = Counter(item.reason for item in unresolved)
    reviews = plan.upload_review if plan.operation is SyncOperation.UPLOAD else plan.download_review
    by_key = {row.local.entry_key: row for row in reviews if not row.resolved}
    common = {
        "schema_version": 1,
        "check_id": check_id,
        "operation": plan.operation.value,
        "project_id": project_id,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
    }
    _emit({
        **common,
        "event": "PARATRANZ_SYNC_CHECK",
        "scope": plan.scope,
        "local_count": local_count,
        "local_snapshot_hash": plan.local_snapshot_hash,
        "remote_snapshot_hash": plan.remote_snapshot_hash,
        "action_counts": dict(plan.counts),
        "unresolved_count": len(unresolved),
        "reason_counts": dict(sorted(reasons.items())),
        "reason_groups": [
            {"code": reason, "reason": explain_sync_reason(reason, plan.operation), "count": count}
            for reason, count in sorted(reasons.items())
        ],
        "unresolved_local_occurrences": sum(
            by_key[item.entry_key].local_occurrences for item in unresolved if item.entry_key in by_key
        ),
        "detail_unavailable_count": sum(item.entry_key not in by_key for item in unresolved),
        "stale_decisions": stale_decisions,
    })
    for sequence, item in enumerate(unresolved, 1):
        _emit({
            **common,
            "event": "PARATRANZ_SYNC_UNMATCHED",
            "sequence": sequence,
            **_unmatched(item, by_key.get(item.entry_key), plan.operation),
        })
    return check_id


def _unmatched(item: SyncPlanItem, row: DownloadReviewRow | None, operation: SyncOperation) -> dict:
    projected = {
        "entry_key": _entry_key(item.entry_key),
        "reason_code": item.reason,
        "reason": explain_sync_reason(item.reason, operation),
        "row_id": None if row is None else row.row_id,
        "detail_available": row is not None,
        "local_occurrences": None if row is None else row.local_occurrences,
        "local": None if row is None else _local(row.local),
        "review_reason_code": None if row is None else row.reason,
        "allow_remote_choice": None if row is None else row.allow_remote_choice,
        "plan_external_ref": _reference(item.external_ref),
    }
    if row is None:
        return {
            **projected,
            "candidate_state": "detail_unavailable",
            "candidate_count": None,
            "selectable_candidate_count": None,
            "original_equal_count": None,
            "context_equal_count": None,
            "candidates": [],
        }
    candidates = [_candidate(candidate, row.local, operation) for candidate in row.candidates]
    selectable = sum(candidate.selectable for candidate in row.candidates)
    if not candidates:
        state = "no_candidates"
    elif not selectable:
        state = "all_unavailable"
    else:
        state = "multiple_candidates" if len(candidates) > 1 else "single_candidate"
    return {
        **projected,
        "candidate_state": state,
        "candidate_count": len(candidates),
        "selectable_candidate_count": selectable,
        "original_equal_count": sum(candidate["original_equal"] for candidate in candidates),
        "context_equal_count": sum(candidate["context_equal"] for candidate in candidates),
        "candidates": candidates,
    }


def _local(entry: LocalEntrySnapshot) -> dict:
    return {
        "entry_key": _entry_key(entry.entry_key),
        "revision": entry.revision.value,
        "original_sha256": _digest(entry.original),
        "original_length": len(entry.original),
        **_context(entry.context),
        "context_order": entry.context_order,
        "stage": entry.stage,
        "external_ref": _reference(entry.external_ref),
    }


def _candidate(candidate: DownloadCandidate, local: LocalEntrySnapshot, operation: SyncOperation) -> dict:
    entry = candidate.entry
    local_ref, remote_ref = local.external_ref, entry.external_ref
    return {
        "entry_key": _entry_key(entry.entry_key),
        "remote_id": None if remote_ref is None else remote_ref.opaque_id,
        "external_ref": _reference(remote_ref),
        "original_sha256": _digest(entry.original),
        "original_length": len(entry.original),
        **_context(entry.context),
        "context_order": entry.context_order,
        "stage": entry.stage,
        "deleted": entry.deleted,
        "selectable": candidate.selectable,
        "blocked_reason_code": candidate.reason,
        "blocked_reason": explain_sync_reason(candidate.reason, operation) if candidate.reason else "",
        "original_equal": local.original == entry.original,
        "diagnostic_only": {
            "original_equal_after_strip": local.original.strip() == entry.original.strip(),
            "original_equal_after_newline_normalization": _normalize_newlines(local.original)
            == _normalize_newlines(entry.original),
            "original_equal_casefold": local.original.casefold() == entry.original.casefold(),
        },
        "context_equal": local.context == entry.context,
        "ref_equal": None if local_ref is None or remote_ref is None else local_ref.index_key == remote_ref.index_key,
    }


def _entry_key(key: EntryKey) -> dict:
    # Qualified identities contain the complete original; never use to_dict().
    result = {"namespace": key.namespace.value, "local_key": key.local_key}
    if key.original is not None:
        result.update(identity_original_sha256=_digest(key.original), identity_original_length=len(key.original))
    return result


def _reference(reference: ExternalEntryRef | None) -> dict | None:
    if reference is None:
        return None
    return {"system": reference.system, "scope": reference.scope, "opaque_id": reference.opaque_id}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context(value: str) -> dict:
    # Redact the complete value before truncating: otherwise the preview could
    # cut a credential into a fragment that no longer matches redaction rules.
    safe = SecretRedactor.default().redact_text(value)
    return {
        "context": safe[:_CONTEXT_PREVIEW_LIMIT],
        "context_sha256": _digest(value),
        "context_length": len(value),
        "context_truncated": len(safe) > _CONTEXT_PREVIEW_LIMIT,
    }


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _emit(payload: dict) -> None:
    # Redact structured field values first: final-text redaction must not consume
    # JSON quote delimiters around a context such as "token=...".
    safe = SecretRedactor.default().redact(payload)
    message = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    _logger.info(message.replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
