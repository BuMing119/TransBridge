"""Retain ambiguous syntax results as successful entries requiring review."""

from dataclasses import replace

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.converter.translation_entry import STAGE_QUESTIONABLE

from .postprocess import PostProcessCandidate


def retain_syntax_questions(
    candidates: tuple[PostProcessCandidate, ...], diagnostics: tuple[Diagnostic, ...], *, cancelled: bool
) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
    """Run after recovery; other failures and cancellation remain unsuccessful."""
    if cancelled or any(item.category is ErrorCategory.CANCELLED for item in diagnostics):
        return candidates, diagnostics
    reviewed = set()
    updated = []
    for candidate in candidates:
        if candidate.accepted or not candidate.before_text.strip():
            updated.append(candidate)
            continue
        owned = tuple(item for item in diagnostics if _owns(item, candidate))
        failures = tuple(item for item in owned if item.severity is not DiagnosticSeverity.INFO)
        if failures and all(_syntax_mismatch(item) for item in failures):
            reviewed.add(candidate.entry_key)
            candidate = replace(
                candidate,
                text=candidate.before_text,
                stage=STAGE_QUESTIONABLE,
                accepted=True,
                phases=tuple(dict.fromkeys((*candidate.phases, "proofread"))),
                report_details=tuple({**dict(candidate.report_details), "questionable": True}.items()),
            )
        updated.append(candidate)
    return tuple(updated), tuple(
        replace(
            item,
            code="PROOFREAD_SYNTAX_REVIEW_REQUIRED",
            message="标记存在差异，已保留原译文并标为有疑问",
            retryable=False,
        )
        if _syntax_mismatch(item) and any(dict(item.details).get("entry_key") == key.to_dict() for key in reviewed)
        else item
        for item in diagnostics
    )


def _syntax_mismatch(item: Diagnostic) -> bool:
    return item.code == "PROOFREAD_PROTECTED_SYNTAX_MISMATCH" or (
        item.code == "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED"
        and dict(item.details).get("reason") == "protected_syntax_mismatch"
    )


def _owns(item: Diagnostic, candidate: PostProcessCandidate) -> bool:
    details = dict(item.details)
    keys = (details["entry_key"],) if "entry_key" in details else details.get("entry_keys", ())
    return not keys or candidate.entry_key.to_dict() in keys or candidate.entry_key.serialize() in keys
