"""Application lifetime subscriptions persist outcomes into the original Session."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobState

from .admission import get_effect, record_effect_outcome
from .dispatch import reconcile_dispatches, record_dispatch_outcome
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
        self._subscription = runtime.subscribe(self._observe)
        self._unsubscribe_requests = service.subscribe(self._request_changed)
        self.diagnostics: list[str] = []

    def bind(self, gate, effect_id: str, job_id: str, run_id: str, owner) -> None:
        ref = JobRef(job_id=job_id, run_id=run_id, owner_id=owner.owner_id)
        self._bindings[run_id] = _Binding(gate, effect_id, ref, owner)

    def _observe(self, event) -> None:
        binding = self._bindings.get(event.snapshot.ref.run_id)
        if binding is None or not event.snapshot.is_terminal:
            return
        gate = binding.gate
        try:
            snapshot = self.runtime.get(binding.ref, binding.owner)
            if snapshot.owner != binding.owner or snapshot.owner.session_id != gate.context.session_id:
                raise ValueError("task result ownership differs from captured request context")
            if not binding.effect_id:
                gate.service.update_request(
                    gate.context,
                    gate.admission.request_id,
                    lambda request: record_dispatch_outcome(
                        request, gate.dispatch_id, status=snapshot.state.value, sequence=snapshot.sequence
                    ),
                )
                self._bindings.pop(binding.ref.run_id, None)
                gate.service.notify(gate.context.session_id)
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
            )
            if status != EffectStatus.OUTCOME_UNKNOWN:
                gate.resources.release(binding.effect_id)
            self._bindings.pop(binding.ref.run_id, None)
            gate.service.notify(gate.context.session_id)
        except Exception as error:
            # Retain binding and stop new admissions until explicit reconciliation succeeds.
            self.diagnostics.append(f"{binding.ref.run_id}: {error}")
            logger.exception("Failed to persist request task result for %s", binding.ref.run_id)

    def _request_changed(self, session_id: str) -> None:
        for binding in tuple(self._bindings.values()):
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
        self._bindings.clear()
