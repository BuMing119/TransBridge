"""Application lifetime subscriptions persist outcomes into the original Session."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from threading import RLock

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobSnapshot, JobState

from .admission import get_effect, record_effect_outcome
from .dispatch import reconcile_dispatches, record_dispatch_outcome
from .journal import EventCause
from .models import EffectStatus, RequestStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Binding:
    gate: object
    effect_id: str
    ref: JobRef
    owner: object


class RequestTaskEventRouter:
    def __init__(self, service, runtime):
        self.service = service
        self.runtime = runtime
        self._bindings: dict[str, _Binding] = {}
        self._lock = RLock()
        self._pending: set[str] = set()
        self._outcomes: dict[str, JobSnapshot] = {}
        self._delivering: set[str] = set()
        self._retrying_sessions: set[str] = set()
        self._subscription = runtime.subscribe(self._observe)
        self._unsubscribe_requests = service.subscribe(self._request_changed)
        self.diagnostics: list[str] = []

    def bind(self, gate, effect_id: str, job_id: str, run_id: str, owner) -> None:
        ref = JobRef(job_id=job_id, run_id=run_id, owner_id=owner.owner_id)
        with self._lock:
            self._bindings[run_id] = _Binding(gate, effect_id, ref, owner)

    def has_pending(self, session_id: str, request_id: str) -> bool:
        with self._lock:
            return any(
                binding.gate.context.session_id == session_id and binding.gate.admission.request_id == request_id
                for run_id, binding in self._bindings.items()
                if run_id in self._pending
            )

    def retry_pending(self, session_id: str) -> None:
        """Retry receipts only, once per pending run; never restart a business worker."""
        with self._lock:
            if session_id in self._retrying_sessions:
                return
            self._retrying_sessions.add(session_id)
            bindings = tuple(
                binding
                for run_id, binding in self._bindings.items()
                if run_id in self._pending and binding.gate.context.session_id == session_id
            )
        try:
            for binding in bindings:
                self._deliver(binding, attempts=1)
        finally:
            with self._lock:
                self._retrying_sessions.discard(session_id)

    def _observe(self, event) -> None:
        with self._lock:
            binding = self._bindings.get(event.snapshot.ref.run_id)
            if binding is None or not event.snapshot.is_terminal:
                return
            # Keep the original terminal receipt even if task history is later cleaned.
            self._outcomes.setdefault(binding.ref.run_id, event.snapshot)
        self._deliver(binding, attempts=3)

    def _deliver(self, binding, *, attempts):
        run_id = binding.ref.run_id
        # Order with request commands; do not hold the bookkeeping lock across I/O.
        with self.service.serialized(binding.gate.context):
            with self._lock:
                if self._bindings.get(run_id) != binding or run_id in self._delivering:
                    return
                self._delivering.add(run_id)
            saved = False
            try:
                for _ in range(attempts):
                    try:
                        self._persist(binding)
                    except Exception as error:
                        with self._lock:
                            self._pending.add(run_id)
                            self.diagnostics.append(f"{run_id}: {error}")
                        logger.exception("Failed to persist request task result for %s", run_id)
                    else:
                        with self._lock:
                            self._bindings.pop(run_id, None)
                            self._pending.discard(run_id)
                            self._outcomes.pop(run_id, None)
                        saved = True
                        break
            finally:
                with self._lock:
                    self._delivering.discard(run_id)
        if saved:
            try:
                self.service.notify(binding.gate.context.session_id)
            except Exception:
                logger.exception("Request task result saved but notification failed for %s", run_id)

    def _persist(self, binding):
        gate = binding.gate
        with self._lock:
            snapshot = self._outcomes[binding.ref.run_id]
        if (
            snapshot.ref != binding.ref
            or snapshot.owner != binding.owner
            or snapshot.owner.session_id != gate.context.session_id
        ):
            raise ValueError("task result ownership differs from captured request context")
        if not binding.effect_id:
            gate.service.update_request(
                gate.context,
                gate.admission.request_id,
                lambda request: record_dispatch_outcome(
                    request, gate.dispatch_id, status=snapshot.state.value, sequence=snapshot.sequence
                ),
                cause=EventCause(
                    "task.result", references={"job_ids": [binding.ref.job_id], "run_ids": [binding.ref.run_id]}
                ),
            )
            return
        effect = get_effect(gate.current_request(), binding.effect_id)
        if snapshot.state == JobState.COMPLETED:
            # Runtime completion alone is not proof that a write actually committed.
            status = EffectStatus.SUCCEEDED if effect.receipt else EffectStatus.OUTCOME_UNKNOWN
        elif snapshot.state == JobState.CANCELLED:
            status = EffectStatus.CANCELLED
        else:
            status = EffectStatus.FAILED
        progress = dict(snapshot.progress)
        partial = bool(progress.get("partial") or progress.get("failed_count") or progress.get("failed"))
        receipt = json.dumps({"commit": effect.receipt, "progress": progress}, ensure_ascii=False, default=str)
        gate.service.update_request(
            gate.context,
            gate.admission.request_id,
            lambda request: reconcile_dispatches(
                record_effect_outcome(
                    request,
                    binding.effect_id,
                    status=status,
                    receipt=receipt if effect.receipt else "",
                    sequence=snapshot.sequence,
                    job_id=binding.ref.job_id,
                    run_id=binding.ref.run_id,
                    item_complete=not partial,
                    satisfy_item=False,
                )
            ),
            cause=EventCause(
                "task.result", references={"job_ids": [binding.ref.job_id], "run_ids": [binding.ref.run_id]}
            ),
        )
        if status != EffectStatus.OUTCOME_UNKNOWN:
            gate.resources.release(binding.effect_id)

    def _request_changed(self, session_id: str) -> None:
        self.retry_pending(session_id)
        with self._lock:
            bindings = tuple(self._bindings.values())
        for binding in bindings:
            gate = binding.gate
            if gate.context.session_id != session_id:
                continue
            request = gate.current_request()
            if request.status == RequestStatus.STOPPING or request.revision != gate.admission.request_revision:
                snapshot = self.runtime.get(binding.ref, binding.owner)
                if not snapshot.is_terminal and snapshot.state != JobState.CANCELLING:
                    self.runtime.cancel(binding.ref, binding.owner)

    def close(self) -> None:
        self._subscription.close()
        self._unsubscribe_requests()
        with self._lock:
            self._bindings.clear()
            self._pending.clear()
            self._outcomes.clear()
