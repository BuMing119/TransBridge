"""Detach canonical report evidence once per source before ledger ingestion."""

from __future__ import annotations

import json

from transbridge.application.translation.polish_report import build_polish_report_snapshot


def source_report_evidence(outcomes, run_id):
    """Return immutable JSON payloads; no nested mutable values cross ledger generations."""
    result = {}
    for outcome in outcomes:
        candidates = {}
        snapshots = (
            getattr(outcome, "snapshot", None),
            getattr(outcome.translation, "post_process_result", None),
            getattr(outcome, "record_snapshot", None),
        )
        for snapshot in snapshots:
            if snapshot is not None:
                candidates.update((candidate.entry_key, candidate) for candidate in snapshot.candidates)
        if outcome.polish:
            polish = build_polish_report_snapshot(outcome.polish, outcome.task.polish_entries, run_id=run_id)
            candidates.update((candidate.entry_key, candidate) for candidate in polish.candidates)
        details_by_key = {}
        for key, candidate in candidates.items():
            details = dict(candidate.report_details)
            if details.get("review_status"):
                details.setdefault("review_before_text", candidate.before_text)
            details_by_key[key] = details
        for key, entry_result in getattr(outcome.translation, "entry_outcomes", {}).items():
            details_by_key.setdefault(key, {}).update(getattr(entry_result, "review_details", ()))
        result[outcome.task.key] = {
            key: json.dumps(details, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            for key, details in details_by_key.items()
        }
    return result
