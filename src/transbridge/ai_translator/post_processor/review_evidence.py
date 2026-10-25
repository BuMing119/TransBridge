"""Capture legacy review inputs once and bind judgments to the text actually reviewed."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import json

from transbridge.application.translation.entry_alias import ai_entry_id

_STATUSES = frozenset({"revised", "retained", "term_suspect", "needs_review"})
_FIELDS = {
    "review_contract_version": 0,
    "review_reference_terms": {},
    "detected_issues": (),
    "review_before_text": None,
    "reviewed_translation": None,
}


@dataclass(frozen=True, slots=True)
class ReviewRequestEvidence:
    entry_id: str
    before_text: str
    reference_terms: dict[str, str]
    detected_issues: tuple[dict, ...]


def capture_review(entry, issues, terms, *, before_text=None) -> ReviewRequestEvidence:
    """Detach exactly the inputs used to construct this request, without a later glossary lookup."""
    return ReviewRequestEvidence(
        str(ai_entry_id(entry)),
        (entry.translation or "") if before_text is None else before_text,
        deepcopy(dict(terms)),
        tuple(asdict(issue) for issue in issues),
    )


def capture_arbitration(context) -> ReviewRequestEvidence:
    refine, polish = context.refine_result, context.polish_result
    terms = {
        issue.term: issue.standard_translation
        for issue in context.original_issues
        if issue.term and issue.standard_translation
    }
    terms.update(getattr(refine, "review_reference_terms", {}))
    text = (
        getattr(polish, "polished_translation", "")
        or getattr(refine, "refined_translation", "")
        or context.entry.translation
        or ""
    )
    return capture_review(context.entry, context.original_issues, terms, before_text=text)


def format_review_evidence(evidence: ReviewRequestEvidence) -> str:
    """Render the arbitration prompt's single reference/findings section from captured inputs."""
    return json.dumps(
        {"reference_terms": evidence.reference_terms, "detected_issues": evidence.detected_issues},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def review_result_fields(data: dict, evidence: ReviewRequestEvidence | None, text: str) -> dict:
    """Only an explicit model judgment paired with its captured request earns version 1."""
    if evidence is None:
        return {}
    status, reason = data.get("review_status"), data.get("review_reason")
    explicit = isinstance(status, str) and status in _STATUSES and isinstance(reason, str) and bool(reason.strip())
    if not explicit:
        status, reason = "needs_review", "模型未返回完整的综合审核结论，保留候选待复核"
    elif status in {"revised", "retained"}:
        status = "retained" if text == evidence.before_text else "revised"
    return {
        "review_status": status,
        "review_reason": reason,
        "review_contract_version": 1 if explicit else 0,
        "review_reference_terms": deepcopy(evidence.reference_terms),
        "detected_issues": deepcopy(evidence.detected_issues),
        "review_before_text": evidence.before_text,
        "reviewed_translation": text,
    }


def preserved_review_fields(value) -> dict:
    """Copy request evidence through local decisions and serialization without upgrading it."""
    getter = value.get if isinstance(value, dict) else lambda key, default: getattr(value, key, default)
    fields = {key: deepcopy(getter(key, default)) for key, default in _FIELDS.items()}
    fields["detected_issues"] = tuple(fields["detected_issues"])
    return fields


def projected_review_fields(decision, refined, final_text: str, issues) -> dict:
    """Never transfer a judgment about an earlier candidate onto newly polished wording."""
    for value in (decision, refined):
        if (
            value is not None
            and getattr(value, "valid", True)
            and getattr(value, "review_contract_version", 0) == 1
            and getattr(value, "reviewed_translation", None) == final_text
            and getattr(value, "review_status", "") in _STATUSES
            and getattr(value, "review_reason", "").strip()
        ):
            fields = preserved_review_fields(value)
            fields.pop("reviewed_translation")
            return {**fields, "review_status": value.review_status, "review_reason": value.review_reason}
        # An actual later AI judgment/failure takes precedence over a previous stage's evidence.
        if (
            value is decision
            and value is not None
            and (getattr(value, "review_before_text", None) is not None or not getattr(value, "valid", True))
        ):
            break
    previous = next(
        (value for value in (decision, refined) if getattr(value, "review_before_text", None) is not None), None
    )
    fields = preserved_review_fields(previous)
    fields.pop("reviewed_translation")
    fields["review_contract_version"] = 0
    fields["detected_issues"] = tuple(asdict(issue) for issue in issues)
    has_review = bool(issues) or any(getattr(value, "review_status", "") for value in (decision, refined))
    changed = previous is not None and getattr(previous, "reviewed_translation", None) != final_text
    reason = "后续处理改变了已审核译文，当前候选需重新综合校对" if changed else "尚无当前译文的完整综合审核证据"
    if not changed:
        reason = next(
            (
                value.review_reason
                for value in (decision, refined)
                if getattr(value, "review_status", "") == "needs_review" and getattr(value, "review_reason", "")
            ),
            reason,
        )
    return {
        **fields,
        "review_status": "needs_review" if has_review else "",
        "review_reason": reason if has_review else "",
    }
