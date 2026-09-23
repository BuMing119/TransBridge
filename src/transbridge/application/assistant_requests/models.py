"""Immutable user-goal state; independent of Qt, model providers and storage."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any


class RequestError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class RequestStatus(StrEnum):
    OPEN = "open"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class ItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SATISFIED = "satisfied"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemKind(StrEnum):
    ANSWER = "answer"
    EXECUTION = "execution"


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    BOUND = "bound"
    RUNNING = "running"
    OUTCOME_UNKNOWN = "outcome_unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


UNSETTLED_EFFECTS = frozenset({
    EffectStatus.PREPARED,
    EffectStatus.BOUND,
    EffectStatus.RUNNING,
    EffectStatus.OUTCOME_UNKNOWN,
})


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    kind: str
    request_revision: int
    item_ids: tuple[str, ...]
    reference: str
    content_digest: str = ""
    complete: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> Evidence:
        return cls(**{**data, "item_ids": tuple(data["item_ids"])})


@dataclass(frozen=True, slots=True)
class RequestItem:
    item_id: str
    description: str
    kind: ItemKind = ItemKind.ANSWER
    required: bool = True
    dependencies: tuple[str, ...] = ()
    status: ItemStatus = ItemStatus.PENDING
    waiting_reasons: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> RequestItem:
        values = dict(data)
        for key in ("dependencies", "waiting_reasons", "evidence_ids"):
            values[key] = tuple(values.get(key, ()))
        values["kind"] = ItemKind(values.get("kind", "answer"))
        values["status"] = ItemStatus(values.get("status", "pending"))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class AssistantExecutionRef:
    request_id: str
    request_revision: int
    item_ids: tuple[str, ...]
    attempt_id: str
    dispatch_id: str
    turn_id: str
    session_id: str = ""
    work_round_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AssistantExecutionRef:
        return cls(**{**data, "item_ids": tuple(data["item_ids"])})


@dataclass(frozen=True, slots=True)
class EffectIntent:
    effect_id: str
    operation_hash: str
    execution: AssistantExecutionRef
    lease_epoch: int
    status: EffectStatus = EffectStatus.PREPARED
    job_id: str = ""
    run_id: str = ""
    receipt: str = ""
    last_sequence: int = -1
    approval_id: str = ""
    result_complete: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> EffectIntent:
        return cls(**{
            **data,
            "execution": AssistantExecutionRef.from_dict(data["execution"]),
            "status": EffectStatus(data["status"]),
        })


@dataclass(frozen=True, slots=True)
class RequestRevision:
    revision: int
    goal: str
    constraints: tuple[str, ...]
    items: tuple[RequestItem, ...]
    source_message_id: str

    @classmethod
    def from_dict(cls, data: dict) -> RequestRevision:
        return cls(**{
            **data,
            "constraints": tuple(data["constraints"]),
            "items": tuple(RequestItem.from_dict(i) for i in data["items"]),
        })


@dataclass(frozen=True, slots=True)
class ExecutionDispatch:
    dispatch_id: str
    execution: AssistantExecutionRef
    job_id: str
    run_id: str
    status: str = "running"
    last_sequence: int = -1
    step_specs: tuple[tuple[str, str], ...] = ()
    started_steps: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    failed_steps: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionDispatch:
        values = {**data, "execution": AssistantExecutionRef.from_dict(data["execution"])}
        values["step_specs"] = tuple(tuple(pair) for pair in data.get("step_specs", ()))
        for key in ("started_steps", "completed_steps", "failed_steps"):
            values[key] = tuple(data.get(key, ()))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class UserRequest:
    request_id: str
    session_id: str
    goal: str
    items: tuple[RequestItem, ...]
    revision: int = 1
    scope: tuple[tuple[str, str], ...] = ()
    constraints: tuple[str, ...] = ()
    source_message_ids: tuple[str, ...] = ()
    status: RequestStatus = RequestStatus.OPEN
    pause_reasons: tuple[str, ...] = ()
    stop_target: RequestStatus | None = None
    stop_reason: str = ""
    successor_id: str = ""
    related_to: str = ""
    lease_epoch: int = 0
    effects: tuple[EffectIntent, ...] = ()
    dispatches: tuple[ExecutionDispatch, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    revisions: tuple[RequestRevision, ...] = ()
    applied_events: tuple[tuple[str, str], ...] = ()
    automatic_turns: int = 0
    execution_version_json: str = ""
    work_round_id: str = ""

    def __post_init__(self) -> None:
        if not self.request_id or not self.session_id or not self.goal.strip() or not self.items or self.revision < 1:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "request identity, goal and items are required")
        ids = {item.item_id for item in self.items}
        if len(ids) != len(self.items) or "" in ids:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "item IDs must be distinct and nonempty")
        visited: set[str] = set()
        active: set[str] = set()
        by_id = {item.item_id: item for item in self.items}

        def visit(item_id: str) -> None:
            if item_id not in ids or item_id in active:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "item dependencies must exist and be acyclic")
            if item_id in visited:
                return
            active.add(item_id)
            for dependency in by_id[item_id].dependencies:
                visit(dependency)
            active.remove(item_id)
            visited.add(item_id)

        for item_id in ids:
            visit(item_id)

    @property
    def terminal(self) -> bool:
        return self.status not in (RequestStatus.OPEN, RequestStatus.STOPPING)

    @property
    def unsettled(self) -> bool:
        return any(effect.status in UNSETTLED_EFFECTS for effect in self.effects) or any(
            dispatch.status in ("running", "outcome_unknown") for dispatch in self.dispatches
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> UserRequest:
        values = dict(data)
        for key in ("constraints", "source_message_ids", "pause_reasons"):
            values[key] = tuple(values.get(key, ()))
        for key in ("scope", "applied_events"):
            values[key] = tuple(tuple(pair) for pair in values.get(key, ()))
        for key, factory in (
            ("items", RequestItem),
            ("effects", EffectIntent),
            ("dispatches", ExecutionDispatch),
            ("evidence", Evidence),
            ("revisions", RequestRevision),
        ):
            values[key] = tuple(factory.from_dict(i) for i in values.get(key, ()))
        values["status"] = RequestStatus(values.get("status", "open"))
        values["stop_target"] = RequestStatus(values["stop_target"]) if values.get("stop_target") else None
        return cls(**values)
