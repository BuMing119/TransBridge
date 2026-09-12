"""Nonblocking Session deletion preflight; tombstones keep late events from reopening work."""

from copy import deepcopy
from dataclasses import dataclass

from transbridge.application.tasks import JobState, OwnerRef

from .journal import EventCause
from .models import RequestError, RequestStatus, UserRequest
from .reducer import RequestEvent, reduce_request


def prepare_session_deletion(state: dict, session_id: str, command_id: str) -> dict:
    if not session_id or not command_id:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "deletion requires a Session and stable command ID")
    updated = deepcopy(state)
    tombstone = updated.get("session_tombstone")
    if tombstone and tombstone.get("session_id") != session_id:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "deletion tombstone belongs to another Session")
    command_id = tombstone["command_id"] if tombstone else command_id
    updated["session_tombstone"] = {"session_id": session_id, "command_id": command_id, "status": "stopping"}
    requests = []
    for data in updated.get("requests", ()):
        request = UserRequest.from_dict(data)
        if request.session_id != session_id:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "request belongs to another Session")
        if request.status == RequestStatus.OPEN:
            request = reduce_request(
                request,
                RequestEvent(
                    f"{command_id}:{request.request_id}", "cancel", request.revision, {"reason": "session_deleted"}
                ),
            )
        requests.append(request.to_dict())
    updated["requests"] = requests
    for item in updated.get("ingress", ()):
        if item.get("status") in {"pending", "routing"}:
            item["status"] = "cancelled"
    for batch in updated.get("batches", ()):
        if batch.get("status") in {"routing", "user_paused"}:
            batch["status"] = "cancelled"
    return updated


def assert_session_deletable(state: dict) -> None:
    if not state.get("session_tombstone"):
        raise RequestError("SESSION_DELETE_NOT_PREPARED", "record deletion intent before deleting a Session")
    requests = tuple(UserRequest.from_dict(data) for data in state.get("requests", ()))
    if any(request.unsettled or not request.terminal for request in requests):
        raise RequestError("SESSION_DELETE_PENDING", "任务仍在收敛或有待核对结果，会话尚不能删除")


@dataclass(frozen=True, slots=True)
class DeletionReadiness:
    ready: bool
    pending_run_ids: tuple[str, ...] = ()
    diagnostic: str = ""


class RequestDeletionCoordinator:
    """Called before lifecycle.delete, never blocks the UI waiting for workers."""

    def __init__(self, service, runtime=None):
        self.service = service
        self.runtime = runtime

    def prepare(self, context, *, command_id: str) -> DeletionReadiness:
        def prepare(state):
            updated = prepare_session_deletion(state, context.session_id, command_id)
            state.clear()
            state.update(updated)

        self.service.transact(
            context, prepare, cause=EventCause("session.deletion", "user", {"command_id": command_id})
        )
        self.service.scheduler.invalidate_session(context.session_id)
        self.service.notify(context.session_id)
        state = self.service.state(context)
        scopes = {(context.project_id, context.variant_id)}
        for request in self.service.requests(state):
            values = dict(request.scope)
            scopes.add((values.get("project_id"), values.get("variant_id")))
        for ingress in state.get("ingress", ()):
            source = ingress.get("context", {})
            if source.get("owner_id") == context.owner_id:
                scopes.add((source.get("project_id"), source.get("variant_id")))
        jobs = {}
        for project_id, variant_id in scopes:
            actor = OwnerRef(
                context.owner_id, "smart-assistant", project_id, variant_id, context.session_id, context.permissions
            )
            for snapshot in self.runtime.list(actor) if self.runtime is not None else ():
                if snapshot.owner.owner_id == context.owner_id and snapshot.owner.session_id == context.session_id:
                    jobs[snapshot.ref.run_id] = snapshot
        pending = []
        for snapshot in jobs.values():
            current = self.runtime.get(snapshot.ref, snapshot.owner)
            if not current.is_terminal and current.state != JobState.CANCELLING:
                current = self.runtime.cancel(snapshot.ref, snapshot.owner)
            if not current.is_terminal:
                pending.append(current.ref.run_id)
        if pending:
            return DeletionReadiness(False, tuple(pending), "取消信号已发送，等待任务安全结束后再删除")
        try:
            assert_session_deletable(self.service.state(context))
        except RequestError as error:
            return DeletionReadiness(False, diagnostic=str(error))
        return DeletionReadiness(True)


def deletion_preflight(service, runtime=None):
    from uuid import uuid4

    from transbridge.application.contracts import DomainError, ErrorCategory

    coordinator = RequestDeletionCoordinator(service, runtime)

    def check(ref, context):
        result = coordinator.prepare(context, command_id=uuid4().hex)
        if not result.ready:
            raise DomainError(ErrorCategory.PREREQUISITE, "SESSION_DELETE_PENDING", result.diagnostic)

    return check
