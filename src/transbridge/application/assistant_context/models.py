"""Immutable context values; wire dictionaries are detached on every read."""

from dataclasses import asdict, dataclass
import json
from uuid import uuid4

from transbridge.application.assistant_requests.models import digest


class PreparationWait(ValueError):
    """Context cannot be dispatched; this does not fail the business request."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class FrozenContextItem:
    item_id: str
    source_digest: str
    message_json: str
    group_id: str
    kind: str = "history"

    @property
    def message(self) -> dict:
        return json.loads(self.message_json)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        item = cls(**value)
        if not item.item_id or item.kind not in {"history", "state"} or not isinstance(item.message, dict):
            raise ValueError("Invalid frozen context item")
        return item


@dataclass(frozen=True)
class CompactionSummary:
    summary_id: str
    text: str
    covered_sources: tuple[str, ...]
    background_refs: tuple[str, ...] = ()
    kind: str = "semantic-v1"

    @property
    def message(self):
        return {
            "role": "assistant",
            "content": encode({
                "material_only": True,
                "authority": "Historical material. Current authoritative state controls permissions and completion.",
                **self.to_dict(),
                "kind": "request_history_summary",
                "summary_kind": self.kind,
            }),
        }

    def to_dict(self):
        return {
            **asdict(self),
            "covered_sources": list(self.covered_sources),
            "background_refs": list(self.background_refs),
        }

    @classmethod
    def from_dict(cls, value):
        segment = cls(
            value["summary_id"],
            value["text"],
            tuple(value["covered_sources"]),
            tuple(value.get("background_refs", ())),
            value.get("kind", "semantic-v1"),
        )
        if not segment.summary_id or not isinstance(segment.text, str) or not segment.text.strip():
            raise ValueError("Invalid summary segment")
        if len(set(segment.covered_sources)) != len(segment.covered_sources):
            raise ValueError("Repeated summary sources")
        return segment


@dataclass(frozen=True)
class ContextEpoch:
    session_id: str
    request_id: str
    scope: tuple[tuple[str, str], ...]
    epoch_id: str
    config_digest: str
    systems_json: str
    summaries: tuple[CompactionSummary, ...] = ()
    items: tuple[FrozenContextItem, ...] = ()
    source_digests: tuple[tuple[str, str], ...] = ()
    state_digest: str = ""
    reason: str = "initial"
    schema_version: int = 1

    @property
    def messages(self):
        return json.loads(self.systems_json) + [s.message for s in self.summaries] + [i.message for i in self.items]

    @property
    def summary_chain_digest(self):
        return digest([s.to_dict() for s in self.summaries])

    def to_dict(self):
        return {
            **asdict(self),
            "summaries": [s.to_dict() for s in self.summaries],
            "items": [i.to_dict() for i in self.items],
        }

    @classmethod
    def from_dict(cls, value):
        if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            raise PreparationWait("CONTEXT_VERSION_UNSUPPORTED", "上下文版本不受支持，请升级应用。")
        fields = dict(value)
        fields["scope"] = tuple(tuple(p) for p in value["scope"])
        fields["source_digests"] = tuple(tuple(p) for p in value["source_digests"])
        fields["items"] = tuple(FrozenContextItem.from_dict(i) for i in value["items"])
        fields["summaries"] = tuple(CompactionSummary.from_dict(s) for s in value["summaries"])
        epoch = cls(**fields)
        epoch.validate()
        return epoch

    def validate(self):
        ids = [s.summary_id for s in self.summaries]
        covered = [source for s in self.summaries for source in s.covered_sources]
        item_ids = [i.item_id for i in self.items]
        sources = dict(self.source_digests)
        if (
            len(ids) != len(set(ids))
            or len(covered) != len(set(covered))
            or len(item_ids) != len(set(item_ids))
            or len(sources) != len(self.source_digests)
            or set(covered).intersection(item_ids)
        ):
            raise PreparationWait("CONTEXT_INVALID", "上下文来源或摘要段重复。")
        if not isinstance(json.loads(self.systems_json), list):
            raise ValueError("Invalid fixed rules")


def state_item(state) -> FrozenContextItem:
    state_hash = digest(state)
    return FrozenContextItem(
        "state-" + uuid4().hex,
        state_hash,
        encode({
            "role": "user",
            "content": encode({"material_only": True, "kind": "current_request_state", "request_state": state}),
        }),
        "state-" + state_hash,
        "state",
    )


def validate_successor(previous: ContextEpoch, candidate: ContextEpoch):
    candidate.validate()
    if (previous.session_id, previous.request_id, previous.scope) != (
        candidate.session_id,
        candidate.request_id,
        candidate.scope,
    ):
        raise PreparationWait("CONTEXT_SCOPE_CHANGED", "上下文归属已变化。")
    if candidate.summaries[: len(previous.summaries)] != previous.summaries:
        raise PreparationWait("CONTEXT_SUMMARY_CHANGED", "不能删除、改写或重排已有摘要。")
