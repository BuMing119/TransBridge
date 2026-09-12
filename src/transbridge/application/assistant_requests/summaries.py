"""Disposable, deterministic excerpts of a request's older public discussion.

No model is called and no instruction, approval or execution state is inferred.
Consumers must keep authoritative request state separate and validate before use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from typing import Any

from .models import ItemStatus, UserRequest, digest

GENERATION_METHOD = "deterministic_request_excerpts_v1"
_ROUTING_TOOL = "submit_request_routing"
_DECISION_MARKERS = ("决定", "确认", "约定", "结论", "原因", "采用", "保留", "修复", "完成", "decision", "because")


@dataclass(frozen=True, slots=True)
class RequestSummary:
    schema_version: int
    request_id: str
    session_id: str
    request_revision: int
    source_ids: tuple[str, ...]
    covered_sequence: int
    generation_method: str
    source_digest: str
    text: str
    keep_recent: int = 8
    min_old_messages: int = 8
    min_old_chars: int = 4000
    max_chars: int = 2400
    excerpt_chars: int = 240

    def __post_init__(self):
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or self.generation_method != GENERATION_METHOD
            or not self.request_id
            or not self.session_id
            or type(self.request_revision) is not int
            or self.request_revision < 1
            or type(self.covered_sequence) is not int
            or self.covered_sequence < 1
            or not self.source_ids
            or len(set(self.source_ids)) != len(self.source_ids)
            or len(self.source_digest) != 64
            or len(self.text) > self.max_chars
        ):
            raise ValueError("invalid derived request summary")
        _validate_options(
            self.keep_recent, self.min_old_messages, self.min_old_chars, self.max_chars, self.excerpt_chars
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "source_ids": list(self.source_ids)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RequestSummary:
        return cls(**dict(value))


def _validate_options(keep_recent, min_old_messages, min_old_chars, max_chars, excerpt_chars):
    values = (keep_recent, min_old_messages, min_old_chars, max_chars, excerpt_chars)
    if any(type(value) is not int or value <= 0 for value in values) or max_chars < 512:
        raise ValueError("summary options must be positive integers; max_chars must be at least 512")


def _public_text(record: Mapping) -> str:
    metadata = record.get("metadata") or {}
    if (
        record.get("role") not in {"user", "assistant", "tool"}
        or record.get("channel", metadata.get("channel")) in {"analysis", "reasoning"}
        or record.get("origin", metadata.get("origin")) in {"analysis", "reasoning"}
    ):
        return ""
    content = record.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, Mapping)
            and block.get("type") in {"text", "output_text", "input_text"}
            and isinstance(block.get("text"), str)
        )
    return ""


def _related(request: UserRequest, history: Sequence[Mapping]) -> list[dict]:
    sources = set(request.source_message_ids) | {revision.source_message_id for revision in request.revisions}
    routing_calls = {
        call.get("id")
        for record in history
        for call in record.get("tool_calls", ())
        if call.get("name") == _ROUTING_TOOL
    }
    records, seen = [], set()
    previous_sequence = 0
    for index, record in enumerate(history):
        identity = record.get("message_id")
        if not isinstance(identity, str) or not identity:
            continue  # Unidentified legacy material cannot become cited evidence.
        if identity in seen:
            raise ValueError("summary history contains duplicate message identities")
        seen.add(identity)
        sequence = record.get("sequence", index + 1)
        if type(sequence) is not int or sequence <= previous_sequence:
            raise ValueError("summary history must preserve increasing transcript sequence")
        previous_sequence = sequence
        if record.get("session_id", request.session_id) != request.session_id:
            continue
        if (
            any(call.get("name") == _ROUTING_TOOL for call in record.get("tool_calls", ()))
            or record.get("name") == _ROUTING_TOOL
            or (record.get("role") == "tool" and record.get("tool_call_id") in routing_calls)
        ):
            continue
        if "request_ids" in record:
            owners = record["request_ids"]
        elif record.get("request_id"):
            owners = [record["request_id"]]
        else:
            owners = [request.request_id] if identity in sources else []
        if not isinstance(owners, (list, tuple)) or request.request_id not in owners:
            continue
        text = _public_text(record)
        if not text.strip():
            continue
        records.append({
            "message_id": identity,
            "sequence": sequence,
            "role": record["role"],
            "text": text,
            "request_ids": sorted(set(owners)),
            "shared": len(set(owners)) > 1,
        })
    return records


def _completed_facts(request: UserRequest) -> list[dict]:
    evidence = {entry.evidence_id: entry for entry in request.evidence}
    facts = []
    for item in request.items:
        if item.status != ItemStatus.SATISFIED:
            continue
        ids = [
            identity
            for identity in item.evidence_ids
            if identity in evidence
            and evidence[identity].complete
            and evidence[identity].request_revision == request.revision
            and item.item_id in evidence[identity].item_ids
            and evidence[identity].kind == ("answer" if item.kind == "answer" else "execution")
            and evidence[identity].reference
        ]
        if ids:
            facts.append({"item_id": item.item_id, "evidence_ids": ids})
    return facts


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _excerpts(
    records: list[dict], facts: list[dict], max_chars: int, excerpt_chars: int
) -> tuple[str, tuple[str, ...]]:
    material = {
        "material_only": True,
        "not_authorization": True,
        "kind": "historical_source_excerpts",
        "excerpts": [],
        "completed_fact_references": [],
    }
    # Reserve a bounded part for evidence-backed completion references while
    # keeping enough room for the old discussion this derived material preserves.
    for fact in facts:
        material["completed_fact_references"].append(fact)
        if len(_json(material)) > max_chars // 3:
            material["completed_fact_references"].pop()
    # The latest explicit decision is preferred, then recent older discussion.
    ranked = sorted(
        records,
        key=lambda record: (any(marker in record["text"].lower() for marker in _DECISION_MARKERS), record["sequence"]),
        reverse=True,
    )
    for record in ranked:
        text = record["text"]
        lowered = text.lower()
        positions = [lowered.find(marker) for marker in _DECISION_MARKERS if marker in lowered]
        start = max(0, min(positions) - 40) if positions else 0
        end = min(len(text), start + excerpt_chars)
        entry = {
            "message_id": record["message_id"],
            "sequence": record["sequence"],
            "role": record["role"],
            "shared": record["shared"],
            "range": [start, end],
            "text": text[start:end],
        }
        material["excerpts"].append(entry)
        if len(_json(material)) > max_chars:
            material["excerpts"].pop()
            if not material["excerpts"]:
                # JSON escaping counts toward the budget. Keep one traceable
                # excerpt even when a full preview does not fit a small window.
                low, high, fitted = 1, len(entry["text"]), None
                while low <= high:
                    size = (low + high) // 2
                    candidate = {**entry, "text": entry["text"][:size], "range": [start, start + size]}
                    material["excerpts"] = [candidate]
                    if len(_json(material)) <= max_chars:
                        fitted, low = candidate, size + 1
                    else:
                        high = size - 1
                material["excerpts"] = [fitted] if fitted is not None else []
    material["excerpts"].sort(key=lambda entry: entry["sequence"])
    return _json(material), tuple(entry["message_id"] for entry in material["excerpts"])


def plan_summary(
    request: UserRequest,
    history: Sequence[Mapping],
    *,
    keep_recent: int = 8,
    min_old_messages: int = 8,
    min_old_chars: int = 4000,
    max_chars: int = 2400,
    excerpt_chars: int = 240,
) -> RequestSummary | None:
    """Summarize only explicit request-owned old material; retain recent text intact.

    ``history`` is an ordered, ownership-projected transcript. Explicit empty or
    foreign ``request_ids`` override even a request's original source association.
    Shared user input is quoted with ``shared=true`` and never split by guessing.
    ``source_ids`` references actually quoted messages; the digest covers all old
    candidate material so omitted source edits cannot silently preserve a cache.
    """
    _validate_options(keep_recent, min_old_messages, min_old_chars, max_chars, excerpt_chars)
    records = _related(request, history)
    old = records[:-keep_recent]
    if not old or (len(old) < min_old_messages and sum(len(record["text"]) for record in old) < min_old_chars):
        return None
    facts = _completed_facts(request)
    proof_ids = {identity for fact in facts for identity in fact["evidence_ids"]}
    proofs = [asdict(entry) for entry in request.evidence if entry.evidence_id in proof_ids]
    text, source_ids = _excerpts(old, facts, max_chars, excerpt_chars)
    if not source_ids:
        return None
    return RequestSummary(
        schema_version=1,
        request_id=request.request_id,
        session_id=request.session_id,
        request_revision=request.revision,
        source_ids=source_ids,
        covered_sequence=old[-1]["sequence"],
        generation_method=GENERATION_METHOD,
        source_digest=digest({"old_material": old, "completed_facts": facts, "completion_evidence": proofs}),
        text=text,
        keep_recent=keep_recent,
        min_old_messages=min_old_messages,
        min_old_chars=min_old_chars,
        max_chars=max_chars,
        excerpt_chars=excerpt_chars,
    )


def validate_summary(summary: RequestSummary, request: UserRequest, history: Sequence[Mapping]) -> bool:
    """Reject stale or altered derived material; it never repairs authoritative data."""
    if (
        summary.request_id != request.request_id
        or summary.session_id != request.session_id
        or summary.request_revision != request.revision
    ):
        return False
    try:
        current = plan_summary(
            request,
            history,
            keep_recent=summary.keep_recent,
            min_old_messages=summary.min_old_messages,
            min_old_chars=summary.min_old_chars,
            max_chars=summary.max_chars,
            excerpt_chars=summary.excerpt_chars,
        )
    except (ValueError, TypeError, KeyError):
        return False
    return current == summary
