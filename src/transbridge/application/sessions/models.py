"""Immutable Session persistence and recovery contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any

from transbridge.application.contracts import JobRef
from transbridge.application.tasks.models import JobState, OwnerRef
from transbridge.persistence.v2.ids import ProjectId, SessionId, SessionRef, VariantId, VariantRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, SchemaEnvelope, SessionDto


class RecoveryStatus(StrEnum):
    COMPLETE = "complete"
    DEGRADED = "degraded"


class ControllerState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    AWAITING_CONFIRM = "awaiting"
    EXECUTING = "executing"
    AWAITING_TASK = "awaiting_task"


@dataclass(frozen=True, slots=True)
class ControllerSnapshot:
    state: ControllerState = ControllerState.IDLE
    react_depth: int = 0
    auto_mode: bool = False
    recoverable: bool = True
    reason: str | None = None
    active_task_id: str | None = None
    active_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.react_depth < 0:
            raise ValueError("controller react_depth must not be negative")
        if self.recoverable and self.reason is not None:
            raise ValueError("recoverable controller state cannot carry a failure reason")
        if not self.recoverable and not self.reason:
            raise ValueError("unrecoverable controller state requires a reason")
        if self.active_run_id and not self.active_task_id:
            raise ValueError("controller task run requires a task identity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "react_depth": self.react_depth,
            "auto_mode": self.auto_mode,
            "recoverable": self.recoverable,
            "reason": self.reason,
            "active_task_id": self.active_task_id,
            "active_run_id": self.active_run_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ControllerSnapshot:
        return cls(
            ControllerState(str(data.get("state", ControllerState.IDLE.value))),
            int(data.get("react_depth", 0)),
            bool(data.get("auto_mode", False)),
            bool(data.get("recoverable", True)),
            None if data.get("reason") is None else str(data["reason"]),
            None if data.get("active_task_id") is None else str(data["active_task_id"]),
            None if data.get("active_run_id") is None else str(data["active_run_id"]),
        )


class ApprovalState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class PendingApproval:
    approval_id: str
    owner_id: str
    session_id: str
    run_id: str
    request_hash: str
    aggregate_revision: int
    state: ApprovalState = ApprovalState.PENDING

    def __post_init__(self) -> None:
        if not all((self.approval_id, self.owner_id, self.session_id, self.run_id, self.request_hash)):
            raise ValueError("pending approval identities must not be empty")
        if self.aggregate_revision < 0:
            raise ValueError("approval aggregate revision must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_hash": self.request_hash,
            "aggregate_revision": self.aggregate_revision,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PendingApproval:
        return cls(
            str(data["approval_id"]),
            str(data["owner_id"]),
            str(data["session_id"]),
            str(data["run_id"]),
            str(data["request_hash"]),
            int(data["aggregate_revision"]),
            ApprovalState(str(data.get("state", ApprovalState.PENDING.value))),
        )


@dataclass(frozen=True, slots=True)
class SessionJobRef:
    ref: JobRef
    state: JobState
    last_sequence: int = 0
    recoverable: bool = True
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.last_sequence < 0:
            raise ValueError("job observation sequence must not be negative")
        if self.recoverable and self.reason is not None:
            raise ValueError("recoverable job ref cannot carry a failure reason")
        if not self.recoverable and not self.reason:
            raise ValueError("unrecoverable job ref requires a reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref.to_dict(),
            "state": self.state.value,
            "last_sequence": self.last_sequence,
            "recoverable": self.recoverable,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionJobRef:
        ref = data.get("ref")
        if not isinstance(ref, dict):
            raise ValueError("session job ref payload must be an object")
        return cls(
            JobRef.from_dict(ref),
            JobState(str(data["state"])),
            int(data.get("last_sequence", 0)),
            bool(data.get("recoverable", True)),
            None if data.get("reason") is None else str(data["reason"]),
        )


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    ref: SessionRef
    name: str
    owner: OwnerRef
    messages: tuple[Any, ...]
    backend_history: tuple[Any, ...]
    backend_summary: str | None
    controller: ControllerSnapshot
    project_id: ProjectId | None
    variant_id: VariantId | None
    approvals: tuple[PendingApproval, ...]
    jobs: tuple[SessionJobRef, ...]
    revision: int
    created_at: str
    last_active_at: str
    recovery: RecoveryStatus = RecoveryStatus.COMPLETE
    degradation_reasons: tuple[str, ...] = ()
    assistant_state: Any = None
    transcript_manifest: Any = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Session name must not be empty")
        if self.owner.session_id != self.ref.identity.value:
            raise ValueError("Session OwnerRef must name the Session identity")
        if self.variant_id is not None and self.project_id is None:
            raise ValueError("Session Variant reference requires a Project reference")
        if self.owner.project_id != (None if self.project_id is None else self.project_id.value):
            raise ValueError("Session OwnerRef Project scope does not match the aggregate")
        if self.owner.variant_id != (None if self.variant_id is None else self.variant_id.value):
            raise ValueError("Session OwnerRef Variant scope does not match the aggregate")
        if self.revision < 0:
            raise ValueError("Session revision must not be negative")
        if not self.created_at or not self.last_active_at:
            raise ValueError("Session timestamps must not be empty")
        messages = tuple(_freeze_json(value) for value in self.messages)
        history = (
            messages
            if self.backend_history is self.messages
            else tuple(_freeze_json(value) for value in self.backend_history)
        )
        approvals = tuple(sorted(self.approvals, key=lambda value: value.approval_id))
        jobs = tuple(sorted(self.jobs, key=lambda value: value.ref.job_id))
        if len({item.approval_id for item in approvals}) != len(approvals):
            raise ValueError("Session approvals must have unique identities")
        if len({item.ref.job_id for item in jobs}) != len(jobs):
            raise ValueError("Session jobs must have unique identities")
        if any(
            item.owner_id != self.owner.owner_id or item.session_id != self.ref.identity.value for item in approvals
        ):
            raise ValueError("Session approval owner scope does not match the aggregate")
        if any(item.ref.owner_id != self.owner.owner_id for item in jobs):
            raise ValueError("Session JobRef owner does not match the aggregate")
        reasons = tuple(sorted(set(self.degradation_reasons)))
        recovery = self.recovery
        if reasons or not self.controller.recoverable or any(not item.recoverable for item in jobs):
            recovery = RecoveryStatus.DEGRADED
        if recovery is RecoveryStatus.COMPLETE and reasons:
            raise ValueError("complete Session recovery cannot contain degradation reasons")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "backend_history", history)
        object.__setattr__(self, "approvals", approvals)
        object.__setattr__(self, "jobs", jobs)
        object.__setattr__(self, "recovery", recovery)
        object.__setattr__(self, "degradation_reasons", reasons)
        for field_name in ("assistant_state", "transcript_manifest"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, (dict, _FrozenObject)):
                raise ValueError(f"Session {field_name} must be an object")
            object.__setattr__(self, field_name, _freeze_json(value or {}))

    @property
    def variant_ref(self) -> VariantRef | None:
        if self.project_id is None or self.variant_id is None:
            return None
        return VariantRef(self.variant_id, self.project_id)

    def visible_messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(_thaw_json(value) for value in self.messages)

    def backend_messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(_thaw_json(value) for value in self.backend_history)

    def freeze_history(self, records) -> tuple[Any, ...]:
        """Reuse unchanged immutable records when a live history gains messages."""
        previous = {
            dict(value.items).get("message_id"): value
            for value in self.backend_history
            if isinstance(value, _FrozenObject)
        }
        result = []
        for record in records:
            old = previous.get(record.get("message_id"))
            if old is not None and tuple(sorted(record.items())) == old.items:
                result.append(old)
            else:
                result.append(_freeze_json(record))
        return tuple(result)

    def assistant_data(self) -> dict[str, Any]:
        return _thaw_json(self.assistant_state)

    def transcript_data(self) -> dict[str, Any]:
        return _thaw_json(self.transcript_manifest)

    def to_dto(self) -> SessionDto:
        data = {
            "name": self.name,
            "messages": [_thaw_json(value) for value in self.messages],
            "history": [_thaw_json(value) for value in self.backend_history],
            "backend_summary": self.backend_summary,
            "owner": _owner_to_dict(self.owner),
            "controller": self.controller.to_dict(),
            "project_id": None if self.project_id is None else self.project_id.value,
            "variant_id": None if self.variant_id is None else self.variant_id.value,
            "pending_approvals": [item.to_dict() for item in self.approvals],
            "job_refs": [item.to_dict() for item in self.jobs],
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "recovery": self.recovery.value,
            "degradation_reasons": list(self.degradation_reasons),
            "assistant_state": self.assistant_data(),
            "transcript_manifest": self.transcript_data(),
        }
        return SessionDto(SchemaEnvelope(SCHEMA_VERSION, self.ref.kind, self.ref.identity.value, self.revision, data))

    @classmethod
    def from_dto(cls, dto: SessionDto, ref: SessionRef | None = None) -> SessionSnapshot:
        envelope = dto.envelope
        resolved_ref = ref or SessionRef(SessionId(envelope.identity))
        if resolved_ref.identity.value != envelope.identity:
            raise ValueError("Session DTO identity does not match its reference")
        data = envelope.data
        reasons = set(str(value) for value in data.get("degradation_reasons", ()))
        legacy = data.get("legacy") or {}
        if isinstance(legacy, dict) and legacy.get("recovery") == "degraded-history-unavailable":
            reasons.add("backend_history_unavailable")
        project_id = None if data.get("project_id") is None else ProjectId(str(data["project_id"]))
        variant_id = None if data.get("variant_id") is None else VariantId(str(data["variant_id"]))
        owner_data = data.get("owner")
        if isinstance(owner_data, dict):
            owner = _owner_from_dict(owner_data)
        else:
            owner = OwnerRef(
                owner_id=f"session:{resolved_ref.identity.value}",
                entrypoint="legacy-session",
                project_id=None if project_id is None else project_id.value,
                variant_id=None if variant_id is None else variant_id.value,
                session_id=resolved_ref.identity.value,
            )
            reasons.add("owner_scope_missing")
        history = data.get("history")
        if "history" not in data or not isinstance(history, list):
            history = []
            reasons.add("backend_history_missing")
        controller_data = data.get("controller")
        if isinstance(controller_data, dict):
            try:
                controller = ControllerSnapshot.from_dict(controller_data)
            except (KeyError, TypeError, ValueError):
                controller = ControllerSnapshot(recoverable=False, reason="controller_state_invalid")
                reasons.add("controller_state_invalid")
        else:
            controller = ControllerSnapshot(recoverable=False, reason="controller_state_missing")
            reasons.add("controller_state_missing")
        approvals = _read_approvals(data.get("pending_approvals"), owner, resolved_ref, reasons)
        jobs = _read_jobs(data.get("job_refs"), owner, reasons)
        return cls(
            resolved_ref,
            str(data["name"]),
            owner,
            tuple(data.get("messages", ())),
            tuple(history),
            None if data.get("backend_summary") is None else str(data["backend_summary"]),
            controller,
            project_id,
            variant_id,
            approvals,
            jobs,
            envelope.revision,
            str(data.get("created_at") or data.get("legacy", {}).get("created_at") or "unknown"),
            str(data.get("last_active_at") or data.get("legacy", {}).get("last_active_at") or "unknown"),
            RecoveryStatus.DEGRADED if reasons else RecoveryStatus.COMPLETE,
            tuple(reasons),
            data.get("assistant_state"),
            data.get("transcript_manifest"),
        )


@dataclass(frozen=True, slots=True)
class _FrozenObject:
    items: tuple[tuple[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _FrozenArray:
    items: tuple[Any, ...]


def _freeze_json(value: Any) -> Any:
    if isinstance(value, (_FrozenObject, _FrozenArray)):
        return value
    # Validate each value once. Serializing every nested container repeatedly
    # made long immutable histories quadratic in their nesting/size.
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Session message/history must contain finite JSON values")
        return value
    if isinstance(value, dict):
        if any(not isinstance(key, (str, int, float, bool, type(None))) for key in value):
            raise ValueError("Session message/history must contain JSON object keys")
        return _FrozenObject(tuple(sorted((str(key), _freeze_json(item)) for key, item in value.items())))
    if isinstance(value, (list, tuple)):
        return _FrozenArray(tuple(_freeze_json(item) for item in value))
    raise ValueError("Session message/history must contain finite JSON values")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, _FrozenObject):
        return {key: _thaw_json(item) for key, item in value.items}
    if isinstance(value, _FrozenArray):
        return [_thaw_json(item) for item in value.items]
    return value


def _owner_to_dict(owner: OwnerRef) -> dict[str, Any]:
    return {
        "owner_id": owner.owner_id,
        "entrypoint": owner.entrypoint,
        "project_id": owner.project_id,
        "variant_id": owner.variant_id,
        "session_id": owner.session_id,
        "permissions": sorted(owner.permissions),
    }


def _owner_from_dict(data: Mapping[str, Any]) -> OwnerRef:
    return OwnerRef(
        owner_id=str(data["owner_id"]),
        entrypoint=str(data["entrypoint"]),
        project_id=None if data.get("project_id") is None else str(data["project_id"]),
        variant_id=None if data.get("variant_id") is None else str(data["variant_id"]),
        session_id=None if data.get("session_id") is None else str(data["session_id"]),
        permissions=frozenset(str(value) for value in data.get("permissions", ())),
    )


def _read_approvals(
    raw: Any,
    owner: OwnerRef,
    ref: SessionRef,
    reasons: set[str],
) -> tuple[PendingApproval, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        reasons.add("pending_approvals_invalid")
        return ()
    values: list[PendingApproval] = []
    for item in raw:
        try:
            approval = PendingApproval.from_dict(item)
            if approval.owner_id != owner.owner_id or approval.session_id != ref.identity.value:
                raise ValueError("approval owner mismatch")
            values.append(approval)
        except (KeyError, TypeError, ValueError):
            reasons.add("pending_approval_dropped")
    return tuple(values)


def _read_jobs(raw: Any, owner: OwnerRef, reasons: set[str]) -> tuple[SessionJobRef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        reasons.add("job_refs_invalid")
        return ()
    values: list[SessionJobRef] = []
    for item in raw:
        try:
            job = SessionJobRef.from_dict(item)
            if job.ref.owner_id != owner.owner_id:
                raise ValueError("job owner mismatch")
            values.append(job)
        except (KeyError, TypeError, ValueError):
            reasons.add("job_ref_dropped")
    return tuple(values)


__all__ = [
    "ApprovalState",
    "ControllerSnapshot",
    "ControllerState",
    "PendingApproval",
    "RecoveryStatus",
    "SessionJobRef",
    "SessionSnapshot",
]
