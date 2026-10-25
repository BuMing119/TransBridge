"""Interpret semantic review outcomes without treating glossary differences as failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transbridge.application.translation.protected_syntax import protected_syntax_matches

from .review_evidence import preserved_review_fields

if TYPE_CHECKING:
    from .base import PostProcessIssue
    from .llm_arbiter import ArbiterDecision, ArbitrationContext


def substantive_errors(issues: list[PostProcessIssue]) -> list[PostProcessIssue]:
    """Old checkpoints may still label advisory terminology findings as errors."""
    return [
        issue
        for issue in issues
        if issue.severity == "error" and issue.issue_type not in {"term_mismatch", "cross_entry_inconsistency"}
    ]


def reviewed_decision(context: ArbitrationContext) -> ArbiterDecision | None:
    """An explicit semantic judgment need not repeat a literal terminology repair."""
    from .llm_arbiter import ArbiterDecision

    refine = context.refine_result
    if not refine or not refine.valid or not refine.review_status:
        return None
    if refine.confidence <= 0 and refine.review_status != "needs_review":
        return None
    if (context.polish_result and context.polish_result.polished_translation != refine.refined_translation) or any(
        issue.execution_failed for issue in context.original_issues
    ):
        return None
    if not protected_syntax_matches(context.entry.original, refine.refined_translation):
        return ArbiterDecision(
            refine.entry_id, "reject", "校对结果改变了原文中的受保护标记", refine.confidence, "重试或人工修正标记"
        )
    pending = refine.review_status == "needs_review" or refine.needs_arbitration or refine.confidence < 0.5
    return ArbiterDecision(
        entry_id=refine.entry_id,
        verdict="pending" if pending else "pass",
        reason=refine.review_reason or refine.note or "已综合校对原文、语境与检测疑点",
        confidence=refine.confidence,
        suggested_action="待复核，保留当前译文" if pending else "接受综合校对结果",
        review_status="needs_review" if pending else refine.review_status,
        review_reason=refine.review_reason or refine.note,
        **preserved_review_fields(refine),
    )


def review_fields(data: dict) -> dict:
    """Validate optional metadata while accepting results from older clients."""
    status = data.get("review_status", "")
    reason = data.get("review_reason", "")
    if status not in {"", "revised", "retained", "term_suspect", "needs_review"} or not isinstance(reason, str):
        raise TypeError("invalid semantic review metadata")
    return {"review_status": status, "review_reason": reason}
