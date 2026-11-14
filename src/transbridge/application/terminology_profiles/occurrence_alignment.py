# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Immutable candidate-only bilingual judgments, reusable without model execution."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol


@dataclass(frozen=True, slots=True)
class AlignmentCandidate:
    start: int
    end: int
    expected_text: str
    translation: str
    term_key: str
    original: str


@dataclass(frozen=True, slots=True)
class TermOccurrenceCount:
    term_key: str
    original: str
    source_count: int
    old_count: int
    target_count: int


@dataclass(frozen=True, slots=True)
class AlignmentRequest:
    entry_key: str
    original: str
    translation: str
    plugin_id: str | None
    candidates: tuple[AlignmentCandidate, ...]
    counts: tuple[TermOccurrenceCount, ...]


@dataclass(frozen=True, slots=True)
class AlignmentDecision:
    selected_ids: tuple[int, ...] = ()
    certain: bool = False
    rationale: str = ""


class AlignmentResolver(Protocol):
    def resolve(self, request: AlignmentRequest) -> AlignmentDecision | None: ...


def validate_alignment_decision(payload: object, request: AlignmentRequest) -> AlignmentDecision:
    if not isinstance(payload, dict) or set(payload) != {"entry_key", "selected_ids", "certain", "rationale"}:
        raise ValueError("位置判断必须包含完整且唯一的响应字段")
    if payload["entry_key"] != request.entry_key:
        raise ValueError("位置判断的词条身份不匹配")
    ids, certain, rationale = payload["selected_ids"], payload["certain"], payload["rationale"]
    if type(certain) is not bool or not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("位置判断必须提供确定性与具体理由")
    if not isinstance(ids, list) or any(type(index) is not int for index in ids):
        raise ValueError("位置判断只能选择整数候选编号")
    if len(ids) != len(set(ids)) or any(index < 0 or index >= len(request.candidates) for index in ids):
        raise ValueError("位置判断包含重复或不存在的候选编号")
    if not certain and ids:
        raise ValueError("不确定的位置判断不能指定替换")
    selected = sorted((request.candidates[index] for index in ids), key=lambda item: (item.start, item.end))
    for candidate in selected:
        if (
            candidate.start < 0
            or candidate.end <= candidate.start
            or candidate.end > len(request.translation)
            or request.translation[candidate.start : candidate.end] != candidate.expected_text
        ):
            raise ValueError("位置判断候选与原始译文不一致")
    if any(left.end > right.start for left, right in zip(selected, selected[1:])):
        raise ValueError("位置判断选择了重叠的候选")
    return AlignmentDecision(tuple(sorted(ids)), certain, rationale.strip())


@dataclass(frozen=True, slots=True, init=False)
class FrozenAlignmentResolver:
    """Index by entry identity, then compare complete values; no content fingerprints."""

    records: tuple
    _by_entry: object

    def __init__(self, records=()):
        object.__setattr__(self, "records", tuple(records))
        by_entry = {}
        for request, decision in self.records:
            by_entry.setdefault(request.entry_key, []).append((request, decision))
        object.__setattr__(
            self, "_by_entry", MappingProxyType({key: tuple(values) for key, values in by_entry.items()})
        )

    def resolve(self, request):
        from .alignment_cache_codec import same_location_input

        for previous, decision in reversed(self._by_entry.get(request.entry_key, ())):
            if same_location_input(previous, request):
                return decision
        return None


def checked_decision(decision: AlignmentDecision, request: AlignmentRequest) -> AlignmentDecision:
    return validate_alignment_decision(
        {
            "entry_key": request.entry_key,
            "selected_ids": list(decision.selected_ids),
            "certain": decision.certain,
            "rationale": decision.rationale,
        },
        request,
    )
