"""Build the canonical report snapshot at the AI translation completion boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory, OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision

from .postprocess import PostProcessCandidate, ReportSnapshot


class TranslationCompletionResult(Protocol):
    success_count: int
    failed_count: int
    skipped_count: int
    new_dynamic_terms: int
    post_process_result: ReportSnapshot | None


class TranslationReportEntry(Protocol):
    identity: EntryKey
    revision: EntryRevision
    original: str
    translation: str
    stage: int
    context: str


def build_translation_report_snapshot(
    result: TranslationCompletionResult,
    entries: Sequence[TranslationReportEntry],
    *,
    run_id: str,
    cancelled: bool,
    before_text_by_key: Mapping[EntryKey, str] | None = None,
) -> ReportSnapshot:
    """Merge translation counts with an optional canonical post-process snapshot."""
    existing = result.post_process_result
    if existing is not None and existing.run_id != run_id:
        raise ValueError("translation and post-process report run IDs must match")
    if cancelled:
        outcome = OperationOutcome.CANCELLED
    elif result.failed_count and result.success_count:
        outcome = OperationOutcome.PARTIAL
    elif result.failed_count:
        outcome = OperationOutcome.FAILED
    elif existing is not None:
        outcome = existing.outcome
    else:
        outcome = OperationOutcome.COMPLETED

    diagnostics = list(existing.diagnostics if existing is not None else ())
    if result.failed_count:
        diagnostics.append(
            Diagnostic(
                "AI_TRANSLATION_ITEMS_FAILED",
                "Some translation entries failed; see the translation counts for the affected total.",
                severity=DiagnosticSeverity.ERROR,
                category=ErrorCategory.EXTERNAL,
                details=(("failed_count", result.failed_count),),
            )
        )
    if cancelled:
        diagnostics.append(
            Diagnostic(
                "AI_TRANSLATION_CANCELLED",
                "The AI translation run was cancelled before all entries completed.",
                severity=DiagnosticSeverity.WARNING,
                category=ErrorCategory.CANCELLED,
            )
        )

    if existing is None:
        candidates = tuple(
            PostProcessCandidate(
                run_id=run_id,
                entry_key=entry.identity,
                before_revision=entry.revision,
                original=entry.original,
                before_text=(before_text_by_key or {}).get(entry.identity, entry.translation),
                text=entry.translation,
                stage=entry.stage,
                context=entry.context or "",
            )
            for entry in entries
        )
        input_count = len(entries)
        accepted_count = len(candidates)
        stage_outcomes = ()
        issue_count = 0
        timing_ms = ()
        summary = {}
    else:
        candidates = existing.candidates
        input_count = existing.input_count
        accepted_count = existing.accepted_count
        stage_outcomes = existing.stage_outcomes
        issue_count = existing.issue_count
        timing_ms = existing.timing_ms
        summary = dict(existing.run_spec_summary)

    summary.update({
        "source": "auto-translator",
        "translation_counts": {
            "succeeded": result.success_count,
            "failed": result.failed_count,
            "skipped": result.skipped_count,
            "new_dynamic_terms": result.new_dynamic_terms,
        },
        "post_process_enabled": bool(existing and existing.stage_outcomes),
    })
    return ReportSnapshot(
        schema="transbridge.postprocess-report.v1",
        run_id=run_id,
        outcome=outcome,
        input_count=input_count,
        accepted_count=accepted_count,
        candidates=candidates,
        stage_outcomes=stage_outcomes,
        diagnostics=tuple(diagnostics),
        issue_count=issue_count,
        failure_count=(existing.failure_count if existing is not None else 0) + result.failed_count,
        timing_ms=timing_ms,
        run_spec_summary=summary,
    )


__all__ = ["TranslationCompletionResult", "TranslationReportEntry", "build_translation_report_snapshot"]
