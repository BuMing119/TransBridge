# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Count unique source records per translation, independent of evidence variants."""

from collections.abc import Mapping
from dataclasses import dataclass
import json


@dataclass(frozen=True, slots=True)
class Usage:
    translation: str
    evidence: tuple
    count: int
    minority: bool


def evidence_record_identity(item):
    key = item.entry_key
    try:
        structured = json.loads(key)
    except (ValueError, TypeError):
        structured = None
    # Parsed plugin identities append the original text after the stable record key.
    if isinstance(structured, list) and len(structured) >= 2 and all(isinstance(v, str) for v in structured[:2]):
        key = tuple(structured[:2])
    return tuple(item.source_chain), getattr(item, "namespace", ""), key


def conflict_usage(conflict, evidence) -> tuple[Usage, ...]:
    by_id = evidence if isinstance(evidence, Mapping) else {item.evidence_id: item for item in evidence}
    groups = []
    for variant in conflict.variants:
        records = {}
        for evidence_id in variant.evidence_ids:
            item = by_id.get(evidence_id)
            if item is not None:
                records.setdefault(evidence_record_identity(item), item)
        groups.append((variant.normalized_translation, tuple(records.values())))
    maximum = max((len(items) for _name, items in groups), default=0)
    tied = all(len(items) == maximum for _name, items in groups)
    return tuple(Usage(name, items, len(items), tied or len(items) < maximum) for name, items in groups)
