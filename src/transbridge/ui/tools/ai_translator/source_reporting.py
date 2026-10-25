"""Snapshot and report projections for source workflow results."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key

if TYPE_CHECKING:
    from .source_execution import SourceOutcome


def build_source_snapshot(outcome: SourceOutcome, request, *, cancelled: bool = False):
    """Build an in-memory report without exporting files."""
    from transbridge.application.contracts import OperationOutcome
    from transbridge.application.io import EntryKey
    from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
    from transbridge.application.translation.polish_report import build_polish_report_snapshot

    if outcome.record_snapshot is not None:
        return outcome.record_snapshot
    cancelled = cancelled or outcome.cancelled
    snapshot = getattr(outcome.translation, "post_process_result", None)
    if outcome.task.polish_entries:
        summary = outcome.polish_summary
        failed = tuple(
            ai_entry_id(entry) for entry in outcome.task.polish_entries if ai_entry_key(entry) in outcome.failed_keys
        )
        polish = build_polish_report_snapshot(
            outcome.polish,
            list(outcome.task.polish_entries),
            run_id=request.run_id,
            accepted_entry_ids=summary.accepted_entry_ids if summary else (),
            failed_entry_ids=summary.failed_entry_ids if summary else failed,
            rejected_entry_ids=summary.rejected_entry_ids if summary else (),
            pending_entry_ids=(
                ()
                if summary
                else tuple(
                    ai_entry_id(entry) for entry in outcome.task.polish_entries if ai_entry_id(entry) not in failed
                )
            ),
        )
        candidates = []
        failed_keys = set()
        counts: dict[str, int] = {"accepted": 0, "rejected": 0, "failed": 0}
        for candidate, entry in zip(polish.candidates, outcome.task.polish_entries, strict=True):
            result = outcome.polish.get(ai_entry_id(entry))
            details = dict(candidate.report_details)
            processing = getattr(result, "processing_status", "")
            if result is None:
                processing = "not_started"
            if processing:
                details["processing_status"] = processing
            if processing in {"cancelled", "not_started"}:
                details["result_status"] = processing
            elif cancelled and processing == "completed":
                details["result_status"] = "not_applied"
            status = details["result_status"]
            counts[status] = counts.get(status, 0) + 1
            if status == "failed":
                failed_keys.add(candidate.entry_key)
            candidates.append(
                replace(
                    candidate,
                    text=getattr(result, "candidate_translation", None) or candidate.text,
                    report_details=tuple(details.items()),
                )
            )
        polish = replace(
            polish,
            candidates=tuple(candidates),
            diagnostics=(
                *(
                    item
                    for item in polish.diagnostics
                    if item.code != "POLISH_ENTRY_FAILED"
                    or EntryKey.from_dict(dict(item.details)["entry_key"]) in failed_keys
                ),
                *outcome.diagnostics,
            ),
            failure_count=counts.get("failed", 0),
            run_spec_summary={**polish.run_spec_summary, "polish_counts": counts},
            outcome=OperationOutcome.CANCELLED if cancelled else polish.outcome,
        )
        snapshot = (
            build_mixed_report_snapshot(
                snapshot,
                polish,
                run_id=request.run_id,
                execution_order=request.config.mixed_execution_order,
            )
            if snapshot is not None
            else polish
        )
    if snapshot is None or len(snapshot.candidates) < len(outcome.task.entries):
        snapshot = _include_unprocessed(snapshot, outcome, request.run_id)
    snapshot = _include_translation_failures(snapshot, outcome)
    if cancelled:
        snapshot = replace(snapshot, outcome=OperationOutcome.CANCELLED)
    outcome.snapshot = snapshot
    return snapshot


def _include_translation_failures(snapshot, outcome):
    """Project execution failures onto rows, including empty and retained old translations."""
    reasons = tuple(str(item) for item in getattr(outcome.translation, "failed_entries", ()) or ())
    # Cancellation marks the entire source failed for atomic application. Only
    # explicit per-entry failures remain failures in its historical record.
    by_id: dict[str, list[str]] = {}
    by_key = {ai_entry_key(entry): ai_entry_id(entry) for entry in outcome.task.translate_entries}
    for reason in reasons:
        entry_id = by_key.get(reason, reason.partition(": ")[0])
        by_id.setdefault(entry_id, []).append(reason)
    has_owned_failures = any(ai_entry_id(entry) in by_id for entry in outcome.task.translate_entries)
    use_source_failures = outcome.translation is None or (
        getattr(outcome.translation, "failed_count", 0) > 0 and not has_owned_failures
    )
    # A later polish exception also marks every source key failed. An available
    # translation result is authoritative for that stage's individual failures.
    failed_keys = set(outcome.failed_keys) if use_source_failures and not outcome.cancelled else set()
    failures = {}
    for entry in outcome.task.translate_entries:
        owned = by_id.get(ai_entry_id(entry), ())
        if owned or ai_entry_key(entry) in failed_keys:
            failures[entry.identity] = "\n".join(owned) or outcome.error or "翻译失败"
    if not failures:
        return snapshot
    candidates = []
    for candidate in snapshot.candidates:
        reason = failures.get(candidate.entry_key)
        if reason is not None:
            details = dict(candidate.report_details)
            details.update(result_status="failed", processing_status="failed", note=reason)
            candidate = replace(candidate, accepted=False, report_details=tuple(details.items()))
        candidates.append(candidate)
    return replace(
        snapshot,
        candidates=tuple(candidates),
        accepted_count=sum(candidate.accepted for candidate in candidates),
        failure_count=max(snapshot.failure_count, len(failures)),
    )


def _include_unprocessed(snapshot, outcome, run_id):
    from transbridge.application.contracts import OperationOutcome
    from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot

    known = {candidate.entry_key for candidate in snapshot.candidates} if snapshot else set()
    missing = tuple(
        PostProcessCandidate(
            run_id=run_id,
            entry_key=entry.identity,
            before_revision=entry.revision,
            original=entry.original or "",
            before_text=entry.translation or "",
            text=entry.translation or "",
            stage=entry.stage,
            accepted=False,
            context=entry.context or "",
            report_details=(("result_status", "not_started"), ("processing_status", "not_started")),
        )
        for entry in outcome.task.entries
        if entry.identity not in known
    )
    if snapshot:
        return replace(snapshot, candidates=(*snapshot.candidates, *missing), input_count=len(outcome.task.entries))
    return ReportSnapshot(
        schema="transbridge.polish-report.v1",
        run_id=run_id,
        outcome=OperationOutcome.FAILED,
        input_count=len(outcome.task.entries),
        accepted_count=0,
        candidates=missing,
        stage_outcomes=(),
        diagnostics=(),
    )


def build_task_record(outcomes, request, *, state: str, applied: bool = False, saved: bool = False) -> dict:
    """A durable result record, not an executable checkpoint."""
    sources = [
        {
            "key": outcome.task.key,
            "label": outcome.task.label,
            "error": outcome.error,
            "snapshot": build_source_snapshot(outcome, request, cancelled=state == "cancelled").to_dict(),
        }
        for outcome in outcomes
    ]
    counts = {"successful": 0, "failed": 0, "unprocessed": 0, "cancelled": 0}
    for source in sources:
        for entry in source["snapshot"]["entries"]:
            details = entry.get("report_details", {})
            status = details.get("processing_status", details.get("result_status", ""))
            if status == "not_started":
                counts["unprocessed"] += 1
            elif status == "cancelled":
                counts["cancelled"] += 1
            elif status == "failed":
                counts["failed"] += 1
            elif (
                status == "completed"
                or entry["accepted"]
                or (details.get("verdict") == "pass" and details.get("confidence", 0) > 0)
            ):
                counts["successful"] += 1
            else:
                counts["failed"] += 1
    now = datetime.now(UTC).isoformat()
    owner = getattr(getattr(request, "spec", None), "owner", None)
    return {
        "schema": "transbridge.task-history.v1",
        "run_id": request.run_id,
        "created_at": now,
        "updated_at": now,
        "ended_at": now if state in {"completed", "cancelled", "failed", "error"} else None,
        "project_id": getattr(owner, "project_id", None),
        "variant_id": getattr(owner, "variant_id", None),
        "state": state,
        "applied": applied,
        "saved": saved,
        "counts": counts,
        "sources": sources,
    }


def render_source_report(outcome: SourceOutcome, request) -> None:
    """Compatibility export entrypoint, invoked only when an export is requested."""
    from .reporting import render_translation_report

    snapshot = build_source_snapshot(outcome, request)
    if snapshot is not None:
        outcome.report = render_translation_report(snapshot, Path(outcome.task.esp_path or outcome.task.label).stem)
