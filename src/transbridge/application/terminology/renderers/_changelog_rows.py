"""Layout-neutral rows projected directly from a frozen changelog document."""

from __future__ import annotations

import json

from ..changelog_queries import ChangeLogQuerySource
from ..models import CanonicalChange, TermDecision

ChangeLogDocumentReader = ChangeLogQuerySource


def message_row(message: tuple[str, tuple[str, ...]]) -> tuple[object, ...]:
    key, arguments = message
    return key, len(arguments), _json(arguments)


def change_row(change: CanonicalChange) -> tuple[object, ...]:
    return (
        change.change_id,
        change.change_type.value,
        change.term_id,
        change.manual,
        change.before_digest or "",
        change.after_digest or "",
        _decision_json(change.before),
        _decision_json(change.after),
        _json(change.details),
    )


def change_payload(change: CanonicalChange) -> dict[str, object]:
    return {
        "change_id": change.change_id,
        "change_type": change.change_type.value,
        "term_id": change.term_id,
        "manual": change.manual,
        "before_digest": change.before_digest,
        "after_digest": change.after_digest,
        "before": _decision_payload(change.before),
        "after": _decision_payload(change.after),
        "details": change.details,
    }


def _decision_json(decision: TermDecision | None) -> str:
    return "" if decision is None else _json(_decision_payload(decision))


def _decision_payload(decision: TermDecision | None) -> dict[str, object] | None:
    if decision is None:
        return None
    return {
        "term_id": decision.term_id,
        "project_id": decision.project_id,
        "variant_id": decision.variant_id,
        "original": decision.original,
        "normalized_original": decision.normalized_original,
        "translation": decision.translation,
        "scope": decision.scope.canonical_key,
        "status": decision.status.value,
        "suppressed": decision.suppressed,
        "variants": decision.variants,
        "notes": decision.notes,
        "replacement_of": decision.replacement_of,
        "evidence_ids": decision.evidence_ids,
    }


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


__all__ = ["ChangeLogDocumentReader", "change_payload", "change_row", "message_row"]
