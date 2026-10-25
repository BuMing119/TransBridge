"""Evidence and semantic-review annotations for candidate-only post-processing."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from transbridge.application.io import EntryKey

from .postprocess import PostProcessCandidate
from .proofread_advisory import REVIEW_CONTRACT_VERSION, REVIEW_STATUSES, has_review_evidence


def with_detected_issues(candidate: PostProcessCandidate, issues: list) -> PostProcessCandidate:
    """Keep all checker findings together for the later semantic model request."""
    details = dict(candidate.report_details)
    detected = list(details.get("detected_issues", ()))
    for issue in issues:
        value = asdict(issue)
        if value not in detected:
            detected.append(value)
    details["detected_issues"] = detected
    return candidate.with_report_details(details)


def review_entry(candidate: PostProcessCandidate) -> dict[str, Any]:
    details = dict(candidate.report_details)
    return {
        "entry_key": candidate.entry_key.to_dict(),
        "original": candidate.original,
        "current": candidate.text,
        "stage": candidate.stage,
        "context": candidate.context,
        "reference_terms": details.get("reference_terms", details.get("review_reference_terms", {})),
        "detected_issues": details.get("detected_issues", details.get("issues", [])),
        "prior_review_status": details.get("review_status", ""),
        "prior_review_reason": details.get("review_reason", ""),
    }


def review_content(candidate: PostProcessCandidate) -> str:
    """Budget the same context, references and findings that the model receives."""
    return json.dumps(review_entry(candidate), ensure_ascii=False, separators=(",", ":"))


def review_instruction(phase: str) -> str:
    common = (
        "Review all detected concerns for each entry together against its source and context. "
        "Findings are possible problems, not established errors. Reference terms are advisory and may be wrong or "
        "contextually inapplicable; a glossary mismatch alone never warrants rejection or forced replacement. "
        "Respect prior semantic review judgments. Preserve source meaning, placeholders and formatting markers. "
        "Set review_status to revised, retained, term_suspect, or needs_review and explain the judgment in "
        "review_reason. Use term_suspect when a reference is unsuitable; do not rewrite the glossary. "
    )
    if phase == "arbitrate":
        return common + "Return value as pass, reject, or pending; reject only clear substantive translation errors."
    return common + "Return the final translation as value. Keep a suitable current translation unchanged."


def response_reviews(payload: dict) -> tuple[tuple[EntryKey, dict[str, str]], ...]:
    results = []
    for item in payload["results"]:
        status = item.get("review_status", "")
        reason = item.get("review_reason", "")
        if (status and status not in REVIEW_STATUSES) or not isinstance(reason, str):
            raise ValueError("invalid post-process review metadata")
        if status or reason:
            results.append((EntryKey.from_dict(item["entry_key"]), {"review_status": status, "review_reason": reason}))
    return tuple(results)


def annotate_review(
    before: PostProcessCandidate,
    after: PostProcessCandidate,
    metadata: dict[str, str],
    phase: str,
    value: str,
) -> PostProcessCandidate:
    details = dict(after.report_details)
    if not after.accepted and phase != "arbitrate":
        details.update(
            review_status="needs_review",
            review_reason="校对结果未通过结构校验，已保留原译文。",
            review_contract_version=0,
            result_status="failed",
        )
        return after.with_report_details(details)
    status = metadata.get("review_status")
    reason = metadata.get("review_reason", "")
    explicit = status in REVIEW_STATUSES and bool(reason.strip())
    references = details.get("reference_terms", {})
    inherited = (
        has_review_evidence(details)
        and before.text == after.text
        and details.get("review_reference_terms") == references
    )
    if explicit:
        if status in {"revised", "retained"} and phase != "arbitrate":
            status = "revised" if before.text != after.text else "retained"
        details.update(
            review_contract_version=REVIEW_CONTRACT_VERSION,
            review_reference_terms=references,
            review_before_text=before.text,
        )
    elif inherited:
        status, reason = details["review_status"], details["review_reason"]
    else:
        status = "needs_review"
        reason = "模型未返回完整的校对判断与理由，已保留候选译文，待复核。"
        details.update(review_contract_version=0, review_reference_terms=references)
    if phase == "arbitrate":
        details["verdict"] = value
        if value in {"pending", "reject"}:
            status = "needs_review"
    if explicit and status == "needs_review" and phase != "arbitrate":
        details["proposed_translation"] = value
        after = before
    details.setdefault("detected_issues", [])
    details.update(review_status=status, review_reason=reason, result_status="completed")
    return after.with_report_details(details)
