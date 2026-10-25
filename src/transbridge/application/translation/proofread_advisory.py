"""Collect fallible review evidence without rejecting translation candidates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import asdict
import json

from transbridge.converter.translation_entry import TranslationEntry

from .postprocess import PostProcessCandidate

REVIEW_STATUSES = frozenset({"revised", "retained", "term_suspect", "needs_review"})
REVIEW_CONTRACT_VERSION = 1


def collect_review_findings(
    candidates: tuple[PostProcessCandidate, ...], *, is_cancelled: Callable[[], bool] = lambda: False
) -> tuple[PostProcessCandidate, ...]:
    """Run local format and cross-entry detectors once, on the calling worker.

    Grouping is linear in entries. At most eight distinct wording examples per
    source are attached, so a frequent source cannot produce quadratic payloads.
    Differences across contexts are questions, never automatic corrections.
    """
    from transbridge.ai_translator.post_processor.format_validator import FormatValidator

    groups: dict[str, dict[str, dict]] = defaultdict(dict)
    for candidate in candidates:
        if is_cancelled():
            return candidates
        if candidate.original.strip() and candidate.text.strip():
            versions = groups[candidate.original]
            if candidate.text not in versions:
                versions[candidate.text] = {"translation": candidate.text, "context": candidate.context, "count": 0}
            versions[candidate.text]["count"] += 1
    differences = {
        source: {
            "issue_type": "cross_entry_inconsistency",
            "severity": "info",
            "message": "相同原文存在不同译法，请结合各自语境判断是否应统一；差异本身不代表错误。",
            "variant_count": len(versions),
            "examples": list(versions.values())[:8],
        }
        for source, versions in groups.items()
        if len(versions) > 1
    }
    validator = FormatValidator()
    updated = []
    for index, candidate in enumerate(candidates):
        if is_cancelled():
            return (*updated, *candidates[index:])
        entry = TranslationEntry(
            candidate.entry_key.serialize(),
            candidate.entry_key.local_key,
            candidate.original,
            candidate.text,
            candidate.stage,
            candidate.context,
            entry_key=candidate.entry_key,
            revision=candidate.before_revision,
        )
        details = dict(candidate.report_details)
        existing = details.get("detected_issues", ())
        issues = list(existing) if isinstance(existing, (list, tuple)) else []
        issues.extend(asdict(issue) for issue in validator.check(entry))
        if candidate.original in differences:
            issues.append(differences[candidate.original])
        details["detected_issues"] = issues
        updated.append(candidate.with_report_details(details))
    return tuple(updated)


def prepare_review_candidate(candidate: PostProcessCandidate, terms: Mapping[str, str]) -> PostProcessCandidate:
    """Combine existing detector findings and literal differences as questions."""
    # AI validation itself imports this application package; load the detector
    # only when reviewing, after both packages have finished initialization.
    from transbridge.ai_translator.required_term_matching import MandatoryTermMatcher
    from transbridge.ai_translator.term_validation import contains_term

    details = dict(candidate.report_details)
    issues: list[dict] = []
    seen: set[str] = set()

    def add(issue: Mapping) -> None:
        value = dict(issue)
        signature = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        if signature not in seen:
            issues.append(value)
            seen.add(signature)

    for field in ("detected_issues", "issues"):
        existing = details.get(field, ())
        if not isinstance(existing, (list, tuple)):
            continue
        for issue in existing:
            if isinstance(issue, Mapping):
                add(issue)
    matched = MandatoryTermMatcher({source: (source, target, False) for source, target in terms.items()}).match(
        candidate.original
    )
    for source, target in matched.items():
        if not contains_term(candidate.text, target):
            add({
                "issue_type": "term_mismatch",
                "term": source,
                "standard_translation": target,
                "message": f"当前译文与参考术语不同：{source} → {target}；请结合语境判断，不代表译文有误。",
                "severity": "info",
            })
    details["detected_issues"] = issues
    details["review_reference_terms"] = dict(terms)
    details.setdefault("review_before_text", candidate.text)
    details["review_status"] = "needs_review"
    details["review_reason"] = "已收集质量疑点，尚未取得本次 AI 综合判断。"
    details["review_contract_version"] = 0
    return candidate.with_report_details(details)


def has_review_evidence(details: Mapping) -> bool:
    return (
        details.get("review_contract_version") == REVIEW_CONTRACT_VERSION
        and isinstance(details.get("review_status"), str)
        and details.get("review_status") in REVIEW_STATUSES
        and isinstance(details.get("review_reason"), str)
        and bool(details["review_reason"].strip())
        and isinstance(details.get("detected_issues"), (list, tuple))
        and isinstance(details.get("review_reference_terms"), Mapping)
    )
