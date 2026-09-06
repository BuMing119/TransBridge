"""Canonical report snapshot for standalone proofreading and polish runs."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
import math
from typing import Any, Protocol

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory, OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision, SourceNamespace

from .postprocess import PostProcessCandidate, ReportSnapshot


class PolishReportEntry(Protocol):
    """Minimum entry shape accepted at the application reporting boundary."""

    id: str
    original: str
    translation: str
    stage: int
    context: str | None


class PolishReportResult(Protocol):
    """Structural result contract; legacy PolishResult and ProofreadResult both match."""

    entry_id: str
    original_translation: str
    polished_translation: str
    confidence: float
    needs_arbitration: bool
    note: str
    changes: Sequence[Mapping[str, Any]]


def build_polish_report_snapshot(
    results: Mapping[str, PolishReportResult | Mapping[str, Any]],
    entries: Sequence[PolishReportEntry],
    *,
    accepted_entry_ids: Collection[str] | None = None,
    rejected_entry_ids: Collection[str] | None = None,
    failed_entry_ids: Collection[str] | None = None,
    pending_entry_ids: Collection[str] | None = None,
    run_id: str,
    polish_level: str | None = None,
    run_spec_summary: Mapping[str, Any] | None = None,
) -> ReportSnapshot:
    """Project UI-independent polish results into the canonical report model.

    Explicit status collections are authoritative when supplied.  Callers that
    only have result objects may omit them; status is then inferred from the
    optional ``accepted`` attribute, confidence and candidate text. Explicit
    ``pending_entry_ids`` represent candidates awaiting a user decision.
    """
    if not run_id or not run_id.strip():
        raise ValueError("polish report run_id must not be empty")

    explicit_sets = _status_sets(accepted_entry_ids, rejected_entry_ids, failed_entry_ids, pending_entry_ids)
    entry_by_id = {str(entry.id): entry for entry in entries}
    diagnostics: list[Diagnostic] = []
    candidates: list[PostProcessCandidate] = []
    counts = {"accepted": 0, "rejected": 0, "failed": 0}
    confidences: list[float] = []
    phases = _report_phases(run_spec_summary)

    for entry_id, entry in entry_by_id.items():
        result = results.get(entry_id)
        confidence = _finite_confidence(_value(result, "confidence", 0.0))
        polished = str(_value(result, "polished_translation", "") or "")
        before_text = str(_value(result, "original_translation", entry.translation) or entry.translation or "")
        status = _result_status(entry_id, result, polished, confidence, explicit_sets)
        counts[status] = counts.get(status, 0) + 1
        confidences.append(confidence)

        issues = tuple(_issue_dict(issue) for issue in (_value(result, "issues", ()) or ()))
        details = {
            "result_status": status,
            "confidence": confidence,
            "changes": _json_safe(_value(result, "changes", ()) or ()),
            "note": str(_value(result, "note", "") or ""),
            "needs_arbitration": bool(_value(result, "needs_arbitration", False)),
            "verdict": str(_value(result, "verdict", "") or ""),
            "refined_translation": _nullable_text(_value(result, "refined_translation", None)),
            "issues": list(issues),
        }
        candidates.append(
            PostProcessCandidate(
                run_id=run_id,
                entry_key=_entry_key(entry),
                before_revision=_entry_revision(entry),
                original=str(entry.original or ""),
                before_text=before_text,
                text=polished or before_text,
                stage=int(entry.stage),
                phases=phases,
                accepted=status == "accepted",
                context=str(entry.context or ""),
                report_details=tuple(details.items()),
            )
        )
        diagnostics.extend(_issue_diagnostics(_entry_key(entry), issues))
        if status == "failed":
            diagnostics.append(
                Diagnostic(
                    "POLISH_ENTRY_FAILED",
                    "A polish candidate failed and the original translation was retained.",
                    severity=DiagnosticSeverity.ERROR,
                    category=ErrorCategory.EXTERNAL,
                    details=(("entry_key", _entry_key(entry).to_dict()),),
                )
            )
        elif status == "rejected":
            diagnostics.append(
                Diagnostic(
                    "POLISH_ENTRY_REJECTED",
                    "A polish candidate was rejected and requires review.",
                    severity=DiagnosticSeverity.WARNING,
                    details=(("entry_key", _entry_key(entry).to_dict()),),
                )
            )

    unknown_ids = sorted(set(map(str, results)) - set(entry_by_id))
    for entry_id in unknown_ids:
        diagnostics.append(
            Diagnostic(
                "POLISH_RESULT_UNMATCHED",
                "A polish result did not match any report input entry.",
                severity=DiagnosticSeverity.WARNING,
                category=ErrorCategory.CONFLICT,
                details=(("entry_id", entry_id),),
            )
        )

    if counts["failed"] and counts["accepted"] + counts["rejected"]:
        outcome = OperationOutcome.PARTIAL
    elif counts["failed"]:
        outcome = OperationOutcome.FAILED
    else:
        outcome = OperationOutcome.COMPLETED

    summary = dict(run_spec_summary or {})
    resolved_level = polish_level if polish_level is not None else summary.get("polish_level", "")
    summary.update({
        "source": "polish",
        "polish_level": str(resolved_level),
        "polish_counts": dict(counts),
        "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
    })
    issue_count = sum(len(dict(candidate.report_details).get("issues", ())) for candidate in candidates)
    issue_count += counts["rejected"]
    return ReportSnapshot(
        schema="transbridge.polish-report.v1",
        run_id=run_id,
        outcome=outcome,
        input_count=len(entries),
        accepted_count=counts["accepted"],
        candidates=tuple(candidates),
        stage_outcomes=(),
        diagnostics=tuple(diagnostics),
        issue_count=issue_count,
        failure_count=counts["failed"],
        run_spec_summary=summary,
    )


def _status_sets(
    accepted: Collection[str] | None,
    rejected: Collection[str] | None,
    failed: Collection[str] | None,
    pending: Collection[str] | None,
) -> tuple[set[str], ...] | None:
    if accepted is None and rejected is None and failed is None and pending is None:
        return None
    values = tuple(set(map(str, group or ())) for group in (accepted, rejected, failed, pending))
    if sum(map(len, values)) != len(set().union(*values)):
        raise ValueError("polish report status collections must be disjoint")
    return values


def _report_phases(run_spec_summary: Mapping[str, Any] | None) -> tuple[str, ...]:
    summary = run_spec_summary or {}
    profile = summary.get("execution_profile")
    stages = profile.get("stages") if isinstance(profile, Mapping) else summary.get("stages")
    if isinstance(stages, Sequence) and not isinstance(stages, (str, bytes, bytearray)):
        normalized = tuple(str(stage) for stage in stages if str(stage))
        if normalized:
            return normalized
    return ("polish",)


def _result_status(
    entry_id: str,
    result: object | None,
    polished: str,
    confidence: float,
    explicit_sets: tuple[set[str], ...] | None,
) -> str:
    if explicit_sets is not None:
        if entry_id in explicit_sets[0]:
            return "accepted"
        if entry_id in explicit_sets[1]:
            return "rejected"
        if entry_id in explicit_sets[2]:
            return "failed"
        if entry_id in explicit_sets[3]:
            return "pending"
        return "failed"
    if result is None or confidence <= 0 or not polished:
        return "failed"
    accepted = _value(result, "accepted", None)
    return "accepted" if accepted is not False else "rejected"


def _value(result: object | None, name: str, default: Any) -> Any:
    if result is None:
        return default
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _entry_key(entry: object) -> EntryKey:
    identity = getattr(entry, "identity", None)
    if isinstance(identity, EntryKey):
        return identity
    entry_key = getattr(entry, "entry_key", None)
    if isinstance(entry_key, EntryKey):
        return entry_key
    return EntryKey(SourceNamespace.legacy(), str(getattr(entry, "id")))


def _entry_revision(entry: object) -> EntryRevision:
    revision = getattr(entry, "revision", EntryRevision())
    return revision if isinstance(revision, EntryRevision) else EntryRevision(int(revision))


def _finite_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return confidence if math.isfinite(confidence) else 0.0


def _nullable_text(value: object) -> str | None:
    return None if value is None else str(value)


def _json_safe(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _issue_dict(issue: object) -> dict[str, Any]:
    if isinstance(issue, Mapping):
        source = issue
        getter = source.get
    else:

        def getter(name: str, default: object = "") -> object:
            return getattr(issue, name, default)

    return {
        "issue_type": str(getter("issue_type", "") or ""),
        "severity": str(getter("severity", "warning") or "warning"),
        "message": str(getter("message", "") or ""),
        "suggestion": str(getter("suggestion", "") or ""),
    }


def _issue_diagnostics(entry_key: EntryKey, issues: Sequence[Mapping[str, Any]]) -> list[Diagnostic]:
    diagnostics = []
    for issue in issues:
        severity_value = str(issue.get("severity", "warning")).lower()
        severity = {
            "error": DiagnosticSeverity.ERROR,
            "info": DiagnosticSeverity.INFO,
        }.get(severity_value, DiagnosticSeverity.WARNING)
        diagnostics.append(
            Diagnostic(
                f"POLISH_ISSUE_{str(issue.get('issue_type') or 'UNKNOWN').upper()}",
                str(issue.get("message") or "A polish quality issue requires review."),
                severity=severity,
                details=(
                    ("entry_key", entry_key.to_dict()),
                    ("suggestion", str(issue.get("suggestion") or "")),
                ),
            )
        )
    return diagnostics


__all__ = ["PolishReportEntry", "PolishReportResult", "build_polish_report_snapshot"]
