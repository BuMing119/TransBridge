"""Logical batch steps, bounded attempts and idempotent terminal receipts."""

from dataclasses import dataclass, replace
import logging
from threading import RLock

from .journal import EventCause
from .models import UNSETTLED_EFFECTS, RequestError, StepAttempt, StepReceipt

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StepHandle:
    dispatch_id: str
    step_id: str
    tool_name: str
    retry_allowed: bool = False


def replace_dispatch(request, dispatch):
    return replace(
        request,
        dispatches=tuple(dispatch if d.dispatch_id == dispatch.dispatch_id else d for d in request.dispatches),
    )


def get_dispatch(request, handle):
    dispatch = next((d for d in request.dispatches if d.dispatch_id == handle.dispatch_id), None)
    if dispatch is None or handle.step_id not in {str(i) for i in range(len(dispatch.step_specs))}:
        raise RequestError("REQUEST_PROTOCOL_INVALID", "logical step does not belong to the admitted batch")
    if dispatch.step_specs[int(handle.step_id)][0] != handle.tool_name:
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "logical step tool changed")
    return dispatch


def start_step(request, dispatch_id, tool_name, arguments_digest, *, retry_allowed=False):
    dispatch = next(d for d in request.dispatches if d.dispatch_id == dispatch_id)
    step_id = next(
        (
            str(i)
            for i in range(len(dispatch.step_specs))
            if str(i) not in dispatch.started_steps and str(i) not in dispatch.completed_steps
        ),
        None,
    )
    if (
        dispatch.status != "running"
        or step_id is None
        or dispatch.step_specs[int(step_id)] != (tool_name, arguments_digest)
    ):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "step differs from the next admitted batch step")
    handle = StepHandle(dispatch_id, step_id, tool_name, retry_allowed)
    return replace_dispatch(request, replace(dispatch, started_steps=(*dispatch.started_steps, step_id))), handle


def start_attempt(request, handle, arguments_digest):
    dispatch = get_dispatch(request, handle)
    if dispatch.status != "running" or handle.step_id in dispatch.completed_steps:
        raise RequestError("TURN_LEASE_STALE", "logical step has already settled")
    attempts = tuple(a for a in dispatch.step_attempts if a.step_id == handle.step_id)
    if len(attempts) >= (4 if handle.retry_allowed else 1) or any(a.status == "running" for a in attempts):
        raise RequestError("COMMAND_PAYLOAD_CONFLICT", "attempt already running or retry limit reached")
    attempt = StepAttempt(handle.step_id, len(attempts) + 1, arguments_digest)
    return replace_dispatch(request, replace(dispatch, step_attempts=(*dispatch.step_attempts, attempt)))


def finish_attempt(request, handle, attempt, status, error_code):
    dispatch = get_dispatch(request, handle)
    attempts = tuple(
        replace(a, status=status, error_code=error_code)
        if a.step_id == handle.step_id and a.attempt == attempt and a.status == "running"
        else a
        for a in dispatch.step_attempts
    )
    return replace_dispatch(request, replace(dispatch, step_attempts=attempts))


def refresh_dispatch(request, dispatch):
    if not dispatch.step_specs or dispatch.status != "running":
        return dispatch
    if len(dispatch.completed_steps) != len(dispatch.step_specs):
        return dispatch
    effects = [e for e in request.effects if e.execution.dispatch_id == dispatch.dispatch_id]
    if any(e.status in UNSETTLED_EFFECTS for e in effects):
        return dispatch
    failed = dispatch.failed_steps or any(e.status != "succeeded" or not e.result_complete for e in effects)
    return replace(dispatch, status="failed" if failed else "completed")


def finish_step(request, handle, status, error_code=""):
    from .dispatch import reconcile_dispatches

    dispatch = get_dispatch(request, handle)
    if handle.step_id in dispatch.completed_steps:
        return request  # First terminal receipt wins, including after cancellation/amendment.
    receipt = StepReceipt(handle.step_id, status, error_code)
    dispatch = replace(
        dispatch,
        completed_steps=(*dispatch.completed_steps, handle.step_id),
        failed_steps=dispatch.failed_steps if status == "completed" else (*dispatch.failed_steps, handle.step_id),
        step_receipts=(*dispatch.step_receipts, receipt),
    )
    return reconcile_dispatches(replace_dispatch(request, refresh_dispatch(request, dispatch)))


def recover_steps(request, dispatch, live_job_ids):
    """Only persisted all-read authority proves an interrupted batch had no writes."""
    if dispatch.status != "running" or dispatch.job_id or not dispatch.step_specs:
        return dispatch
    effects = [e for e in request.effects if e.execution.dispatch_id == dispatch.dispatch_id]
    if any(e.job_id and e.job_id in live_job_ids for e in effects):
        return dispatch
    if any(e.status in UNSETTLED_EFFECTS for e in effects):
        return replace(dispatch, status="outcome_unknown")
    if len(dispatch.completed_steps) == len(dispatch.step_specs):
        return refresh_dispatch(request, dispatch)
    if not effects and dispatch.step_permissions == ("read",) * len(dispatch.step_specs):
        pending = tuple(str(i) for i in range(len(dispatch.step_specs)) if str(i) not in dispatch.completed_steps)
        return replace(
            dispatch,
            status="failed",
            completed_steps=(*dispatch.completed_steps, *pending),
            failed_steps=(*dispatch.failed_steps, *pending),
            step_receipts=(
                *dispatch.step_receipts,
                *(StepReceipt(i, "cancelled", "PROCESS_INTERRUPTED") for i in pending),
            ),
        )
    return replace(dispatch, status="outcome_unknown")


class StepReceiptWriter:
    """Retain failed local receipts and retry persistence, never business execution."""

    def __init__(self, service):
        self.service = service
        self.pending = {}
        self._lock = RLock()
        self._retrying = False
        self._queued_sessions: dict[str, None] = {}
        self._unsubscribe = service.subscribe(self.retry_pending)

    def has_pending(self, context, request_id):
        with self._lock:
            return any(key[:2] == (context.session_id, request_id) for key in self.pending)

    def save(self, context, request_id, handle, status, error_code=""):
        key = (context.session_id, request_id, handle.dispatch_id, handle.step_id)
        with self.service.serialized(context):
            with self._lock:
                self.pending.setdefault(key, (context, request_id, handle, status, error_code))
            for _ in range(2):
                try:
                    self._persist(key)
                    return
                except Exception as error:
                    logger.warning("Logical step receipt persistence failed: %s", type(error).__name__)
            raise RequestError("ADMISSION_PERSIST_FAILED", "步骤结果未保存，请恢复存储后继续；业务操作不会重放")

    def _persist(self, key):
        with self._lock:
            value = self.pending.get(key)
        if value is None:
            return
        context, request_id, handle, status, error_code = value
        self.service.update_request(
            context,
            request_id,
            lambda request: finish_step(request, handle, status, error_code),
            cause=EventCause("step.result", references={"dispatch_ids": [handle.dispatch_id]}),
        )
        with self._lock:
            self.pending.pop(key, None)

    def interrupt(self, context, request_id, dispatch):
        failure = None
        for index, (tool, _) in enumerate(dispatch.step_specs):
            if str(index) not in dispatch.started_steps and str(index) not in dispatch.completed_steps:
                try:
                    self.save(
                        context,
                        request_id,
                        StepHandle(dispatch.dispatch_id, str(index), tool),
                        "cancelled",
                        "TOOL_CALL_CANCELLED",
                    )
                except RequestError as error:
                    failure = error
        if failure is not None:
            raise failure

    def retry_pending(self, session_id):
        with self._lock:
            self._queued_sessions[session_id] = None
            if self._retrying:
                return
            self._retrying = True
        try:
            while True:
                with self._lock:
                    if not self._queued_sessions:
                        # Release ownership atomically with the empty-queue check.
                        self._retrying = False
                        return
                    queued = next(iter(self._queued_sessions))
                    del self._queued_sessions[queued]
                    pending = tuple((key, value[0]) for key, value in self.pending.items() if key[0] == queued)
                # Removing the queued session before I/O lets concurrent or
                # reentrant notifications request one further coalesced pass.
                # Failed writes never enqueue themselves, so storage outages
                # cost at most one attempt per receipt per notification pass.
                for key, context in pending:
                    try:
                        with self.service.serialized(context):
                            self._persist(key)
                    except Exception as error:
                        logger.warning("Logical step receipt still pending: %s", type(error).__name__)
        except BaseException:
            with self._lock:
                self._retrying = False
            raise
