"""Event-driven foreground turn leasing and evidence-backed answer completion."""

from dataclasses import asdict, dataclass, replace
from threading import RLock
from uuid import uuid4

from .models import Evidence, ItemKind, ItemStatus, RequestError, RequestStatus, UserRequest, digest
from .reducer import add_evidence, require_open


@dataclass(frozen=True, slots=True)
class TurnAdmission:
    session_id: str
    request_id: str
    request_revision: int
    ready_item_ids: tuple[str, ...]
    holder_view_id: str
    turn_id: str
    epoch: int
    request_lease_epoch: int
    stage: str = "execution"
    allowed_tools: tuple[str, ...] = ()
    scope: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TurnSelection:
    request: UserRequest
    admission: TurnAdmission

    @property
    def ready_item_ids(self) -> tuple[str, ...]:
        return self.admission.ready_item_ids


def ready_item_ids(request: UserRequest) -> tuple[str, ...]:
    if request.status != RequestStatus.OPEN or request.pause_reasons:
        return ()
    satisfied = {i.item_id for i in request.items if i.status == ItemStatus.SATISFIED}
    active_items = {
        i
        for e in request.effects
        if e.status in ("prepared", "bound", "running", "outcome_unknown")
        for i in e.execution.item_ids
    }
    active_items.update(
        i
        for dispatch in request.dispatches
        if dispatch.status in ("running", "outcome_unknown")
        for i in dispatch.execution.item_ids
    )
    return tuple(
        i.item_id
        for i in request.items
        if i.status in (ItemStatus.PENDING, ItemStatus.WAITING)
        and not i.waiting_reasons
        and set(i.dependencies) <= satisfied
        and i.item_id not in active_items
    )


class RequestScheduler:
    """Share one instance per storage root. No timers, threads or model calls."""

    def __init__(self, *, automatic_turn_limit: int = 20):
        self._lock = RLock()
        self._holders: dict[str, tuple[str, int]] = {}
        self._leases: dict[str, TurnAdmission] = {}
        self._last_selected: dict[str, str] = {}
        self._automatic_turn_limit = automatic_turn_limit
        self._closed = False

    def activate(self, session_id: str, holder_view_id: str) -> int:
        with self._lock:
            if self._closed:
                raise RequestError("ADMISSION_CLOSED", "scheduler is shutting down")
            current = self._holders.get(session_id)
            if current and current[0] == holder_view_id:
                return current[1]
            epoch = (current[1] if current else 0) + 1
            self._holders[session_id] = (holder_view_id, epoch)
            self._leases.pop(session_id, None)  # all old outputs are fenced before handoff
            return epoch

    def deactivate(self, session_id: str, holder_view_id: str) -> None:
        with self._lock:
            current = self._holders.get(session_id)
            if current and current[0] == holder_view_id:
                self._holders[session_id] = ("", current[1] + 1)
                self._leases.pop(session_id, None)

    def invalidate_session(self, session_id: str) -> None:
        """Fence every view after a persisted deletion/shutdown intent."""
        with self._lock:
            current = self._holders.get(session_id)
            self._holders[session_id] = ("", (current[1] if current else 0) + 1)
            self._leases.pop(session_id, None)

    def _acquire(self, session_id: str, holder_view_id: str, **values) -> TurnAdmission | None:
        if self._closed:
            raise RequestError("ADMISSION_CLOSED", "scheduler is shutting down")
        holder = self._holders.get(session_id)
        if not holder or holder[0] != holder_view_id or session_id in self._leases:
            return None
        lease = TurnAdmission(
            session_id=session_id, holder_view_id=holder_view_id, turn_id=str(uuid4()), epoch=holder[1], **values
        )
        self._leases[session_id] = lease
        return lease

    def acquire_routing(self, session_id: str, holder_view_id: str) -> TurnAdmission | None:
        with self._lock:
            return self._acquire(
                session_id,
                holder_view_id,
                request_id="",
                request_revision=0,
                ready_item_ids=(),
                request_lease_epoch=0,
                stage="routing",
                allowed_tools=("submit_request_routing",),
            )

    def select_next_turn(
        self,
        session_id: str,
        holder_view_id: str,
        requests: tuple[UserRequest, ...],
        *,
        priority_request_ids: tuple[str, ...] = (),
        allowed_tools: tuple[str, ...] = (),
        automatic: bool = True,
    ) -> TurnSelection | None:
        with self._lock:
            candidates = [
                r
                for r in requests
                if r.session_id == session_id
                and ready_item_ids(r)
                and (not automatic or r.automatic_turns < self._automatic_turn_limit)
            ]
            last_id = self._last_selected.get(session_id)
            if not priority_request_ids and last_id in [r.request_id for r in candidates]:
                offset = next(n for n, r in enumerate(candidates) if r.request_id == last_id) + 1
                candidates = candidates[offset:] + candidates[:offset]
            if priority_request_ids:
                priorities = {request_id: n for n, request_id in enumerate(priority_request_ids)}
                candidates.sort(key=lambda r: priorities.get(r.request_id, len(priorities)))
            if not candidates:
                return None
            request = candidates[0]
            ready = set(ready_item_ids(request))
            execution_items = [i.item_id for i in request.items if i.item_id in ready and i.kind == ItemKind.EXECUTION]
            admitted_items = tuple(
                i.item_id
                for i in request.items
                if i.item_id in ready and (i.kind == ItemKind.ANSWER or i.item_id == execution_items[0])
            )
            lease = self._acquire(
                session_id,
                holder_view_id,
                request_id=request.request_id,
                request_revision=request.revision,
                ready_item_ids=admitted_items,
                request_lease_epoch=request.lease_epoch,
                allowed_tools=allowed_tools,
                scope=request.scope,
            )
            if lease is None:
                return None
            self._last_selected[session_id] = request.request_id
            updated = replace(request, automatic_turns=request.automatic_turns + int(automatic))
            return TurnSelection(updated, lease)

    def validate(self, admission: TurnAdmission) -> None:
        with self._lock:
            if self._closed or self._leases.get(admission.session_id) != admission:
                raise RequestError("TURN_LEASE_STALE", "turn has ended or another view owns the session")

    def release(self, admission: TurnAdmission) -> bool:
        with self._lock:
            if self._leases.get(admission.session_id) != admission:
                return False
            del self._leases[admission.session_id]
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._leases.clear()


def commit_answer(
    request: UserRequest,
    admission: TurnAdmission,
    text: str,
    coverage: dict[str, str],
    *,
    message_id: str,
    finish_reason: str,
    scheduler: RequestScheduler,
) -> UserRequest:
    """Caller must save the returned request and the full answer in one manifest commit."""
    scheduler.validate(admission)
    require_open(request, admission.request_revision)
    if (
        admission.request_id != request.request_id
        or admission.session_id != request.session_id
        or admission.request_lease_epoch != request.lease_epoch
        or admission.stage != "execution"
    ):
        raise RequestError("TURN_LEASE_STALE", "answer belongs to an obsolete request turn")
    if (
        finish_reason not in ("stop", "tool_calls", "tool_use", "end_turn")
        or not text.strip()
        or not message_id
        or not coverage
    ):
        raise RequestError("ANSWER_INCOMPLETE", "complete final text and an explicit coverage declaration are required")
    if not set(coverage) <= set(admission.ready_item_ids):
        raise RequestError("REQUEST_PROTOCOL_INVALID", "answer coverage exceeds admitted items")
    items = {i.item_id: i for i in request.items}
    for item_id, disposition in coverage.items():
        if items[item_id].kind != ItemKind.ANSWER or disposition not in ("answered", "blocked"):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "coverage only supports answered/blocked question items")
    answered = tuple(i for i, disposition in coverage.items() if disposition == "answered")
    updated = request
    if answered:
        evidence = Evidence(f"answer:{message_id}", "answer", request.revision, answered, message_id, digest(text))
        updated = add_evidence(updated, evidence)
    blocked = {i for i, disposition in coverage.items() if disposition == "blocked"}
    if blocked:
        updated = replace(
            updated,
            items=tuple(
                replace(
                    i,
                    status=ItemStatus.WAITING,
                    waiting_reasons=tuple(dict.fromkeys((*i.waiting_reasons, "answer_blocked"))),
                )
                if i.item_id in blocked
                else i
                for i in updated.items
            ),
        )
    return updated
