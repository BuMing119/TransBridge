"""Deterministic terminology validation and one bounded Refiner recovery pass."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import CancelledError
from dataclasses import fields, replace
from typing import Any

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import EntryKey
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.token_counting import TiktokenContentTokenCounter

from .postprocess import PostProcessCandidate
from .protected_syntax import protected_syntax_matches
from .token_batching import StableContentBatcher


class ProofreadTerminologyClosure:
    """Accept proofread output only after deterministic terminology closure."""

    def __init__(
        self,
        refiner: object | None,
        *,
        model: str,
        max_tokens_per_batch: int,
        max_items: int = 5,
    ) -> None:
        self._refiner = refiner
        self._batcher = StableContentBatcher(
            TiktokenContentTokenCounter(model),
            max_tokens_per_batch,
            max_items=max_items,
        )

    def apply(
        self,
        original_candidates: tuple[PostProcessCandidate, ...],
        proofread_candidates: tuple[PostProcessCandidate, ...],
        terms_by_key: Mapping[object, Mapping[str, str]],
    ) -> tuple[tuple[PostProcessCandidate, ...], tuple[Diagnostic, ...]]:
        before_by_key = {candidate.entry_key: candidate for candidate in original_candidates}
        issues_by_key = {
            candidate.entry_key: _term_issues(candidate, terms_by_key.get(candidate.entry_key, {}))
            for candidate in proofread_candidates
            if candidate.accepted and "proofread" in candidate.phases
        }
        failed = tuple(candidate for candidate in proofread_candidates if issues_by_key.get(candidate.entry_key))
        if not failed:
            return proofread_candidates, ()
        if self._refiner is None:
            diagnostics = tuple(
                _failure_diagnostic(candidate.entry_key, issues_by_key[candidate.entry_key], "refiner_unavailable")
                for candidate in failed
            )
            return _rollback(
                proofread_candidates, before_by_key, {candidate.entry_key for candidate in failed}
            ), diagnostics

        plan = self._batcher.plan(
            failed,
            key=lambda candidate: candidate.entry_key,
            content=lambda candidate: (
                candidate.original,
                candidate.text,
                candidate.context,
                *(
                    f"{getattr(issue, 'term', '')}|{getattr(issue, 'matched_form', '')}|"
                    f"{getattr(issue, 'standard_translation', '')}"
                    for issue in issues_by_key[candidate.entry_key]
                ),
            ),
        )
        updated = {candidate.entry_key: candidate for candidate in proofread_candidates}
        diagnostics: list[Diagnostic] = []
        for oversized in plan.oversized:
            updated[oversized.entry_key] = _rollback_one(before_by_key[oversized.entry_key])
            diagnostics.append(
                Diagnostic(
                    "PROOFREAD_REFINEMENT_TOKEN_LIMIT",
                    oversized.message,
                    category=ErrorCategory.INPUT,
                    severity=DiagnosticSeverity.WARNING,
                    details=(("entry_key", oversized.entry_key.to_dict()),),
                )
            )

        for batch in plan.batches:
            entries = [_to_entry(candidate) for candidate in batch.items]
            entry_by_id = {entry.id: candidate for entry, candidate in zip(entries, batch.items, strict=True)}
            batch_issues = {
                entry.id: list(issues_by_key[candidate.entry_key])
                for entry, candidate in zip(entries, batch.items, strict=True)
            }
            batch_terms = {
                entry.id: dict(terms_by_key.get(candidate.entry_key, {}))
                for entry, candidate in zip(entries, batch.items, strict=True)
            }
            try:
                results = self._refiner.refine_batch(entries, batch_issues, terms_map=batch_terms)
            except (CancelledError, AiRequestCancelledError) as exc:
                for candidate in failed:
                    updated[candidate.entry_key] = _rollback_one(before_by_key[candidate.entry_key])
                diagnostics.append(
                    Diagnostic(
                        "PROOFREAD_REFINEMENT_CANCELLED",
                        "Terminology refinement was cancelled; run-start translations were retained.",
                        category=ErrorCategory.CANCELLED,
                        severity=DiagnosticSeverity.WARNING,
                        details=(("error_type", type(exc).__name__),),
                    )
                )
                break
            except Exception as exc:
                for candidate in batch.items:
                    updated[candidate.entry_key] = _rollback_one(before_by_key[candidate.entry_key])
                    diagnostics.append(
                        _failure_diagnostic(
                            candidate.entry_key,
                            issues_by_key[candidate.entry_key],
                            "refiner_call_failed",
                            error_type=type(exc).__name__,
                            category=ErrorCategory.EXTERNAL,
                        )
                    )
                continue

            cancelled_result = False
            for entry_id, candidate in entry_by_id.items():
                result = results.get(entry_id) if isinstance(results, Mapping) else None
                refined = getattr(result, "refined_translation", None)
                reason = _invalid_reason(result, candidate, refined, terms_by_key.get(candidate.entry_key, {}))
                if reason is not None:
                    updated[candidate.entry_key] = _rollback_one(before_by_key[candidate.entry_key])
                    diagnostic_issues = issues_by_key[candidate.entry_key]
                    if reason == "terminology_still_inconsistent" and isinstance(refined, str):
                        diagnostic_issues = _term_issues(
                            replace(candidate, text=refined),
                            terms_by_key.get(candidate.entry_key, {}),
                        )
                    if reason == "cancelled":
                        cancelled_result = True
                        diagnostics.append(
                            Diagnostic(
                                "PROOFREAD_REFINEMENT_CANCELLED",
                                "Terminology refinement was cancelled; the run-start translation was retained.",
                                category=ErrorCategory.CANCELLED,
                                severity=DiagnosticSeverity.WARNING,
                                details=(("entry_key", candidate.entry_key.to_dict()),),
                            )
                        )
                    else:
                        diagnostics.append(
                            _failure_diagnostic(
                                candidate.entry_key,
                                diagnostic_issues,
                                reason,
                                category=(ErrorCategory.EXTERNAL if reason == "call_failed" else ErrorCategory.INPUT),
                            )
                        )
                    continue
                updated[candidate.entry_key] = candidate.with_text(refined, "refinement")
            if cancelled_result:
                for candidate in failed:
                    updated[candidate.entry_key] = _rollback_one(before_by_key[candidate.entry_key])
                break

        return tuple(updated[candidate.entry_key] for candidate in proofread_candidates), tuple(diagnostics)


def _term_issues(
    candidate: PostProcessCandidate,
    terms: Mapping[str, str],
) -> tuple[PostProcessIssue, ...]:
    return tuple(
        _make_issue(candidate, term, standard) for term, standard in terms.items() if standard not in candidate.text
    )


def _make_issue(candidate: PostProcessCandidate, term: str, standard: str) -> PostProcessIssue:
    values: dict[str, Any] = {
        "entry_id": candidate.entry_key.serialize(),
        "issue_type": PostProcessIssue.TERM_MISMATCH,
        "severity": "warning",
        "message": f"标准术语未采用：{term} → {standard}",
        "original": candidate.original,
        "translation": candidate.text,
        "suggestion": standard,
        "term": term,
        "matched_form": term,
        "standard_translation": standard,
    }
    supported = {item.name for item in fields(PostProcessIssue)}
    return PostProcessIssue(**{key: value for key, value in values.items() if key in supported})


def _to_entry(candidate: PostProcessCandidate) -> TranslationEntry:
    return TranslationEntry(
        id=candidate.entry_key.serialize(),
        key=candidate.entry_key.local_key,
        original=candidate.original,
        translation=candidate.text,
        stage=candidate.stage,
        context=candidate.context,
        entry_key=candidate.entry_key,
        revision=candidate.before_revision,
        metadata=candidate.report_details,
    )


def _invalid_reason(
    result: object | None,
    candidate: PostProcessCandidate,
    refined: object,
    terms: Mapping[str, str],
) -> str | None:
    if result is None:
        return "missing_result"
    failure_code = getattr(result, "failure_code", "")
    if failure_code:
        return str(failure_code)
    if getattr(result, "valid", True) is not True:
        return "invalid_result"
    if not isinstance(refined, str) or not refined.strip():
        return "empty_translation"
    if not protected_syntax_matches(candidate.original, refined):
        return "protected_syntax_mismatch"
    if any(standard not in refined for standard in terms.values()):
        return "terminology_still_inconsistent"
    return None


def _rollback(
    candidates: tuple[PostProcessCandidate, ...],
    before_by_key: Mapping[EntryKey, PostProcessCandidate],
    failed_keys: set[EntryKey],
) -> tuple[PostProcessCandidate, ...]:
    return tuple(
        _rollback_one(before_by_key[candidate.entry_key]) if candidate.entry_key in failed_keys else candidate
        for candidate in candidates
    )


def _rollback_one(candidate: PostProcessCandidate) -> PostProcessCandidate:
    return replace(candidate, text=candidate.before_text, accepted=False)


def _failure_diagnostic(
    entry_key: EntryKey,
    issues: tuple[PostProcessIssue, ...],
    reason: str,
    *,
    error_type: str = "",
    category: ErrorCategory = ErrorCategory.INPUT,
) -> Diagnostic:
    remaining = tuple(
        {
            "term": getattr(issue, "term", None) or "",
            "matched_form": getattr(issue, "matched_form", None) or "",
            "standard_translation": getattr(issue, "standard_translation", None) or issue.suggestion,
        }
        for issue in issues
    )
    details: tuple[tuple[str, object], ...] = (
        ("entry_key", entry_key.to_dict()),
        ("reason", reason),
        ("remaining_terms", remaining),
    )
    if error_type:
        details = (*details, ("error_type", error_type))
    return Diagnostic(
        "PROOFREAD_TERMINOLOGY_REFINEMENT_FAILED",
        "Terminology refinement did not produce a safe, compliant candidate; the run-start translation was retained.",
        category=category,
        severity=DiagnosticSeverity.ERROR if category is ErrorCategory.EXTERNAL else DiagnosticSeverity.WARNING,
        retryable=False,
        details=details,
    )


__all__ = ["ProofreadTerminologyClosure"]
