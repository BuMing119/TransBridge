"""Canonical report composition for mixed translation and polish runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from transbridge.application.contracts import OperationOutcome

from .postprocess import ReportSnapshot


def build_mixed_report_snapshot(
    translation: ReportSnapshot | None,
    polish: ReportSnapshot | None,
    *,
    run_id: str,
    execution_order: str,
    run_spec_summary: Mapping[str, Any] | None = None,
) -> ReportSnapshot:
    """Combine both mixed branches without inventing a second report model."""
    if not run_id or not run_id.strip():
        raise ValueError("mixed report run_id must not be empty")
    snapshots = tuple(snapshot for snapshot in (translation, polish) if snapshot is not None)
    if any(snapshot.run_id != run_id for snapshot in snapshots):
        raise ValueError("mixed report branches must use the parent run ID")
    candidates = tuple(candidate for snapshot in snapshots for candidate in snapshot.candidates)
    if any(candidate.run_id != run_id for candidate in candidates):
        raise ValueError("mixed report candidates must use the parent run ID")

    failure_count = sum(snapshot.failure_count for snapshot in snapshots)
    accepted_count = sum(snapshot.accepted_count for snapshot in snapshots)
    outcomes = {snapshot.outcome for snapshot in snapshots}
    if OperationOutcome.CANCELLED in outcomes:
        outcome = OperationOutcome.CANCELLED
    elif OperationOutcome.FAILED in outcomes or OperationOutcome.PARTIAL in outcomes or failure_count:
        outcome = OperationOutcome.PARTIAL if accepted_count else OperationOutcome.FAILED
    else:
        outcome = OperationOutcome.COMPLETED

    summary = dict(run_spec_summary or {})
    summary.update({
        "source": "mixed",
        "execution_order": execution_order,
        "translation": None if translation is None else dict(translation.run_spec_summary),
        "polish": None if polish is None else dict(polish.run_spec_summary),
    })
    return ReportSnapshot(
        schema="transbridge.mixed-report.v1",
        run_id=run_id,
        outcome=outcome,
        input_count=sum(snapshot.input_count for snapshot in snapshots),
        accepted_count=accepted_count,
        candidates=candidates,
        stage_outcomes=tuple(stage for snapshot in snapshots for stage in snapshot.stage_outcomes),
        diagnostics=tuple(diagnostic for snapshot in snapshots for diagnostic in snapshot.diagnostics),
        issue_count=sum(snapshot.issue_count for snapshot in snapshots),
        failure_count=failure_count,
        timing_ms=tuple(timing for snapshot in snapshots for timing in snapshot.timing_ms),
        run_spec_summary=summary,
    )


__all__ = ["build_mixed_report_snapshot"]
