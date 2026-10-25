"""Validated local result models and parsers for LLM refinement responses."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING

from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key

from .review_evidence import ReviewRequestEvidence, review_result_fields

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry


@dataclass
class FixApplied:
    """One correction reported by the Refiner."""

    issue_type: str
    original_problem: str
    fix_description: str


@dataclass
class RefineResult:
    """One local Refiner result, including transport/contract validity."""

    entry_id: str
    original_translation: str
    refined_translation: str
    fixes_applied: list[FixApplied] = field(default_factory=list)
    confidence: float = 0.0
    needs_arbitration: bool = False
    note: str = ""
    valid: bool = True
    failure_code: str = ""
    review_status: str = ""
    review_reason: str = ""
    review_contract_version: int = 0
    review_reference_terms: dict = field(default_factory=dict)
    detected_issues: tuple[dict, ...] = ()
    review_before_text: str | None = None
    reviewed_translation: str | None = None


def failed_refine_result(entry: TranslationEntry, note: str, failure_code: str) -> RefineResult:
    """Build a structured failure that retains the input translation."""

    return RefineResult(
        entry_id=ai_entry_id(entry),
        original_translation=entry.translation or "",
        refined_translation=entry.translation or "",
        confidence=0.0,
        needs_arbitration=True,
        note=note,
        valid=False,
        failure_code=failure_code,
        review_status="needs_review",
        review_reason=note,
    )


def parse_refinement_response(
    entry: TranslationEntry, response: str, evidence: ReviewRequestEvidence | None = None
) -> RefineResult:
    """Parse one response without attempting lossy JSON repair."""

    try:
        data = json.loads(response)
        if not isinstance(data, dict):
            raise TypeError("refinement response must be an object")
        refined_translation = data.get("refined_translation")
        if not isinstance(refined_translation, str) or not refined_translation.strip():
            raise TypeError("refinement response must contain a non-empty refined_translation")
        fixes = _parse_fixes(data.get("fixes_applied", []))
        review_status, review_reason = _review_outcome(entry, data, refined_translation)
        return RefineResult(
            entry_id=ai_entry_id(entry),
            original_translation=entry.translation or "",
            refined_translation=refined_translation,
            fixes_applied=fixes,
            confidence=data.get("confidence", 0.0),
            needs_arbitration=data.get("needs_arbitration", False),
            note=data.get("note", ""),
            **(
                {"review_status": review_status, "review_reason": review_reason}
                | review_result_fields(data, evidence, refined_translation)
            ),
        )
    except (AttributeError, TypeError, json.JSONDecodeError):
        return failed_refine_result(entry, f"响应解析失败: {str(response)[:200]}", "invalid_response")


def parse_batch_refinement_response(
    entries: list[TranslationEntry],
    response: str,
    evidence: dict[str, ReviewRequestEvidence] | None = None,
) -> dict[str, RefineResult]:
    """Parse a batch with exact, duplicate, missing, unknown, and ambiguous-ID checks."""

    entry_map = _unambiguous_entry_aliases(entries)
    results: dict[str, RefineResult] = {}
    duplicate_entry_ids: set[str] = set()
    unknown_entry_ids: set[str] = set()
    try:
        payload = json.loads(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise TypeError("refinement batch response must contain a results array")
        for item in payload["results"]:
            if not isinstance(item, dict):
                raise TypeError("refinement batch results must contain objects")
            response_id = str(item.get("entry_id", ""))
            entry = entry_map.get(response_id)
            if entry is None:
                unknown_entry_ids.add(response_id or "<missing>")
                continue
            entry_id = str(ai_entry_id(entry))
            if entry_id in results:
                duplicate_entry_ids.add(entry_id)
                continue
            results[entry_id] = _result_from_batch_item(entry, item, (evidence or {}).get(entry_id))

        if unknown_entry_ids:
            unknown_text = ", ".join(sorted(unknown_entry_ids))
            return {
                ai_entry_id(entry): failed_refine_result(
                    entry,
                    f"批量修复响应包含未知条目: {unknown_text}",
                    "invalid_response",
                )
                for entry in entries
            }
        for entry in entries:
            if ai_entry_id(entry) in duplicate_entry_ids:
                results[ai_entry_id(entry)] = failed_refine_result(
                    entry,
                    "批量修复响应重复返回该条目",
                    "invalid_response",
                )
            elif ai_entry_id(entry) not in results:
                results[ai_entry_id(entry)] = failed_refine_result(
                    entry,
                    "批量修复响应缺少该条目",
                    "invalid_response",
                )
    except (AttributeError, TypeError, json.JSONDecodeError):
        return {
            ai_entry_id(entry): failed_refine_result(
                entry,
                f"批量响应解析失败: {str(response)[:200]}",
                "invalid_response",
            )
            for entry in entries
        }
    return results


def _unambiguous_entry_aliases(entries: list[TranslationEntry]) -> dict[str, TranslationEntry]:
    aliases = {str(ai_entry_id(entry)): entry for entry in entries}
    key_counts: dict[str, int] = {}
    for entry in entries:
        key_counts[str(ai_entry_key(entry))] = key_counts.get(str(ai_entry_key(entry)), 0) + 1
    aliases.update({str(ai_entry_key(entry)): entry for entry in entries if key_counts[str(ai_entry_key(entry))] == 1})
    return aliases


def _result_from_batch_item(
    entry: TranslationEntry, item: dict, evidence: ReviewRequestEvidence | None = None
) -> RefineResult:
    refined_translation = item.get("refined_translation")
    if not isinstance(refined_translation, str) or not refined_translation.strip():
        return failed_refine_result(entry, "批量修复响应返回空值或缺少译文", "invalid_response")
    review_status, review_reason = _review_outcome(entry, item, refined_translation)
    return RefineResult(
        entry_id=str(ai_entry_id(entry)),
        original_translation=entry.translation or "",
        refined_translation=refined_translation,
        fixes_applied=_parse_fixes(item.get("fixes_applied", [])),
        confidence=item.get("confidence", 0.0),
        needs_arbitration=item.get("needs_arbitration", False),
        note=item.get("note", ""),
        **(
            {"review_status": review_status, "review_reason": review_reason}
            | review_result_fields(item, evidence, refined_translation)
        ),
    )


def _review_outcome(entry: TranslationEntry, data: dict, translation: str) -> tuple[str, str]:
    default_status = "revised" if translation != entry.translation else "retained"
    status = data.get("review_status") or ("needs_review" if data.get("needs_arbitration") else default_status)
    if status not in {"revised", "retained", "term_suspect", "needs_review"}:
        raise TypeError("refinement review_status is invalid")
    if status == "retained" and translation != entry.translation:
        raise TypeError("retained refinement must preserve the current translation")
    reason = data.get("review_reason", data.get("note", ""))
    if not isinstance(reason, str):
        raise TypeError("refinement review_reason must be a string")
    return status, reason


def _parse_fixes(value: object) -> list[FixApplied]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TypeError("refinement fixes_applied must be an array of objects")
    return [
        FixApplied(
            issue_type=item.get("issue_type", ""),
            original_problem=item.get("original_problem", ""),
            fix_description=item.get("fix_description", ""),
        )
        for item in value
    ]


__all__ = [
    "FixApplied",
    "RefineResult",
    "failed_refine_result",
    "parse_batch_refinement_response",
    "parse_refinement_response",
]
