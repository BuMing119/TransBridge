"""Request admission adapter at the existing common tool/commit boundary."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from uuid import uuid4

from transbridge.application.assistant_requests.admission import (
    activate_effect,
    bind_job,
    get_effect,
    prepare_effect,
    record_effect_outcome,
    validate_effect,
    validate_turn,
)
from transbridge.application.assistant_requests.models import (
    AssistantExecutionRef,
    EffectStatus,
    ExecutionDispatch,
    ItemKind,
    RequestError,
    digest,
)
from transbridge.application.assistant_requests.reducer import require_open
from transbridge.application.assistant_requests.resource_admission import ResourceAdmission
from transbridge.application.io.publish import CommitDecision

from .request_scope import apply_captured_scope, execution_version


@dataclass(frozen=True, slots=True)
class EffectHandle:
    effect_id: str
    tool_name: str
    execution: AssistantExecutionRef | None = None
    is_write: bool = False
    step_id: str = ""


class RequestExecutionGate:
    """Capture one program-issued admission; tool arguments cannot change its owner."""

    def __init__(self, service, context, admission, *, background: bool = False, dispatch_id: str = ""):
        self.service = service
        self.context = context
        self.admission = admission
        self.background = background
        self.dispatch_id = dispatch_id or uuid4().hex
        self._handles: dict[str, EffectHandle] = {}
        self._contexts: dict[str, object] = {}
        self._synthetic_dispatch = False
        self._prepared_step_specs: tuple[tuple[str, str], ...] = ()
        with service.serialized(context):
            if not hasattr(service, "request_resources"):
                service.request_resources = ResourceAdmission()
        self.resources = service.request_resources

    def current_request(self):
        requests = self.service.requests(self.service.state(self.context))
        request = next((r for r in requests if r.request_id == self.admission.request_id), None)
        if request is None:
            raise RequestError("REQUEST_TARGET_AMBIGUOUS", "request was removed from the original session")
        return request

    def for_dispatch(self):
        self._validate(self.current_request(), "propose_plan", require_allowed=False)
        return RequestExecutionGate(
            self.service, self.context, self.admission, background=True, dispatch_id=self.dispatch_id
        )

    def _validate(self, request, tool_name: str, *, require_allowed: bool = True):
        router = getattr(self.service, "request_task_events", None)
        if router is not None and router.diagnostics:
            raise RequestError("ADMISSION_PERSIST_FAILED", "a task result must be reconciled before starting new work")
        if self.background:
            require_open(request, self.admission.request_revision)
            if request.pause_reasons or request.lease_epoch != self.admission.request_lease_epoch:
                raise RequestError("REQUEST_PAUSED", "background dispatch permission has been revoked")
            if request.scope != self.admission.scope or request.session_id != self.context.session_id:
                raise RequestError("REQUEST_SCOPE_MISMATCH", "background dispatch scope changed")
            if require_allowed and tool_name not in self.admission.allowed_tools:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "plan contains a tool outside its admitted set")
        elif require_allowed:
            validate_turn(request, self.admission, tool_name=tool_name, scheduler=self.service.scheduler)
        else:
            self.service.scheduler.validate(self.admission)
            require_open(request, self.admission.request_revision)

    def before(self, tool_name: str, args: dict, execution_context) -> EffectHandle:
        from .tool_registry import ToolRegistry

        if tool_name in {"submit_request_routing", "report_answer_coverage"}:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "control calls cannot execute as business tools")
        if set(args) & {"assistant_execution_ref", "assistant_effect_id", "request_revision", "request_id", "owner_id"}:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "model arguments cannot supply execution ownership")
        request = self.current_request()
        self._validate(request, tool_name)
        self._validate_selection(request, execution_context)
        captured = getattr(execution_context, "request_context", None)
        if captured is not None:
            for field in ("owner_id", "session_id", "project_id", "variant_id"):
                if getattr(captured, field, None) != getattr(self.context, field, None):
                    raise RequestError("REQUEST_SCOPE_MISMATCH", "execution context differs from accepted input")
        spec = ToolRegistry.get(tool_name)
        if spec is None:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "tool is not registered")
        self._begin_steps()
        is_write = spec.permission != "read"
        if not is_write:
            return EffectHandle("", tool_name, step_id=self._start_step(tool_name, args))
        items = tuple(
            i.item_id
            for i in request.items
            if i.item_id in self.admission.ready_item_ids and i.kind == ItemKind.EXECUTION
        )
        if not items:
            if is_write:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "question-only turn cannot perform writes")
            return EffectHandle("", tool_name)
        if len(items) != 1:
            raise RequestError("REQUEST_TARGET_AMBIGUOUS", "execute one acceptance item at a time")
        effect_id = uuid4().hex
        execution = AssistantExecutionRef(
            request.request_id,
            request.revision,
            items,
            uuid4().hex,
            self.dispatch_id,
            self.admission.turn_id,
            request.session_id,
        )
        handle = EffectHandle(effect_id, tool_name, execution, is_write)
        if is_write:
            # Coarse project exclusion is deliberate until an adapter declares finer actual resources.
            resource = f"project:{self.context.project_id}" if self.context.project_id else "external-writes"
            if not self.resources.acquire(effect_id, ("assistant-writes", resource)):
                raise RequestError("RESOURCE_BUSY", "another request owns this write resource")
        try:

            def prepare(request):
                self._validate(request, tool_name)
                # Background work carries a previously admitted plan lease, not a new foreground slot.
                scheduler = self.service.scheduler if not self.background else _DispatchLease(self)
                prepared = prepare_effect(
                    request,
                    self.admission,
                    execution,
                    effect_id=effect_id,
                    operation={"tool": tool_name, "arguments": args},
                    tool_name=tool_name,
                    scheduler=scheduler,
                )
                return activate_effect(prepared, effect_id)

            self.service.update_request(self.context, request.request_id, prepare)
        except Exception:
            self.resources.release(effect_id)
            raise
        self._handles[effect_id] = handle
        self._contexts[effect_id] = execution_context
        if self._synthetic_dispatch:
            handle = replace(handle, step_id=self._start_step(tool_name, args))
            self._handles[effect_id] = handle
        return handle

    def prepare_steps(self, steps: list[dict]) -> None:
        """Stage a batch; waiting for confirmation must not create a running dispatch."""
        if not steps:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "business step batch must not be empty")
        for step in steps:
            if step.get("tool") not in self.admission.allowed_tools or step.get("tool") in {
                "submit_request_routing",
                "report_answer_coverage",
                "propose_plan",
            }:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "control or unadmitted tool in business batch")
        self._prepared_step_specs = tuple((str(step["tool"]), digest(step.get("args", {}))) for step in steps)
        self._synthetic_dispatch = False

    def _begin_steps(self) -> None:
        with self.service.serialized(self.context):
            self._begin_steps_locked()

    def _begin_steps_locked(self) -> None:
        if not self._prepared_step_specs or self._synthetic_dispatch:
            return
        self.dispatch_id = uuid4().hex
        specs = self._prepared_step_specs

        def add_dispatch(request):
            self._validate(request, "propose_plan", require_allowed=False)
            items = tuple(
                i.item_id
                for i in request.items
                if i.item_id in self.admission.ready_item_ids and i.kind == ItemKind.EXECUTION
            )
            execution = AssistantExecutionRef(
                request.request_id,
                request.revision,
                items,
                uuid4().hex,
                self.dispatch_id,
                self.admission.turn_id,
                request.session_id,
            )
            return replace(
                request,
                dispatches=(
                    *request.dispatches,
                    ExecutionDispatch(self.dispatch_id, execution, "", "", step_specs=specs),
                ),
            )

        self.service.update_request(self.context, self.admission.request_id, add_dispatch)
        self._synthetic_dispatch = True

    def _start_step(self, tool_name: str, args: dict) -> str:
        if not self._synthetic_dispatch:
            return ""
        started = []

        def start(request):
            dispatch = next(d for d in request.dispatches if d.dispatch_id == self.dispatch_id)
            match = next(
                (
                    str(index)
                    for index, spec in enumerate(dispatch.step_specs)
                    if spec == (tool_name, digest(args)) and str(index) not in dispatch.started_steps
                ),
                None,
            )
            if match is None:
                raise RequestError("COMMAND_PAYLOAD_CONFLICT", "step was already started or differs from saved batch")
            started.append(match)
            updated = replace(dispatch, started_steps=(*dispatch.started_steps, match))
            return replace(
                request,
                dispatches=tuple(updated if d.dispatch_id == self.dispatch_id else d for d in request.dispatches),
            )

        self.service.update_request(self.context, self.admission.request_id, start)
        return started[0]

    def _finish_step(self, handle: EffectHandle, success: bool) -> None:
        if not handle.step_id:
            return
        from transbridge.application.assistant_requests.dispatch import reconcile_dispatches

        def finish(request):
            dispatch = next(d for d in request.dispatches if d.dispatch_id == self.dispatch_id)
            if handle.step_id in dispatch.completed_steps:
                return request
            completed = (*dispatch.completed_steps, handle.step_id)
            failed = dispatch.failed_steps if success else (*dispatch.failed_steps, handle.step_id)
            status = ("failed" if failed else "completed") if len(completed) == len(dispatch.step_specs) else "running"
            updated = replace(dispatch, completed_steps=completed, failed_steps=failed, status=status)
            return reconcile_dispatches(
                replace(
                    request,
                    dispatches=tuple(updated if d.dispatch_id == self.dispatch_id else d for d in request.dispatches),
                )
            )

        self.service.update_request(self.context, self.admission.request_id, finish)

    def _validate_selection(self, request, execution_context) -> None:
        ingress = self.service.state(self.context).get("ingress", ())
        apply_captured_scope(request, ingress, execution_context)

    def after(self, handle: EffectHandle, result) -> None:
        if not handle.effect_id:
            self._finish_step(handle, bool(getattr(result, "success", False)))
            return
        effect = get_effect(self.current_request(), handle.effect_id)
        if effect.job_id:
            self._finish_step(handle, bool(getattr(result, "success", False)))
            return  # Application TaskEventRouter owns asynchronous terminal delivery.
        success = bool(getattr(result, "success", False))
        partial = bool(getattr(result, "partial", False) or getattr(result, "truncated", False))
        receipt = json.dumps({"tool": handle.tool_name, "result": result.to_dict()}, ensure_ascii=False, sort_keys=True)
        status = EffectStatus.SUCCEEDED if success else EffectStatus.FAILED
        try:
            self.service.update_request(
                self.context,
                self.admission.request_id,
                lambda request: record_effect_outcome(
                    request,
                    handle.effect_id,
                    status=status,
                    receipt=receipt,
                    sequence=effect.last_sequence + 1,
                    item_complete=not partial,
                    satisfy_item=False,
                ),
            )
        finally:
            self.resources.release(handle.effect_id)
        self._finish_step(handle, success)

    def failed(self, handle: EffectHandle, error: Exception) -> None:
        if not handle.effect_id:
            self._finish_step(handle, False)
            return
        effect = get_effect(self.current_request(), handle.effect_id)
        if effect.job_id:
            return
        self.service.update_request(
            self.context,
            self.admission.request_id,
            lambda request: record_effect_outcome(
                request,
                handle.effect_id,
                status=EffectStatus.OUTCOME_UNKNOWN,
                receipt=f"{type(error).__name__}: {error}",
                sequence=effect.last_sequence + 1,
            ),
        )
        # Unknown writes deliberately retain resource ownership until reconciliation.

    def metadata(self, effect_id: str = "") -> dict:
        values = {
            "assistant_request_id": self.admission.request_id,
            "assistant_request_revision": str(self.admission.request_revision),
            "assistant_dispatch_id": self.dispatch_id,
            "_assistant_gate": self,
        }
        if effect_id:
            effect = get_effect(self.current_request(), effect_id)
            values.update(
                assistant_effect_id=effect_id,
                assistant_execution_ref=json.dumps(effect.execution.to_dict(), sort_keys=True),
            )
        return values

    def bind_job(self, effect_id: str, job_id: str, run_id: str, *, runtime=None, owner=None) -> None:
        if effect_id:
            self.service.update_request(
                self.context,
                self.admission.request_id,
                lambda request: bind_job(request, effect_id, job_id=job_id, run_id=run_id),
            )
        else:

            def bind_dispatch(request):
                self._validate(request, "propose_plan", require_allowed=False)
                items = tuple(
                    i.item_id
                    for i in request.items
                    if i.item_id in self.admission.ready_item_ids and i.kind == ItemKind.EXECUTION
                )
                execution = AssistantExecutionRef(
                    request.request_id,
                    request.revision,
                    items,
                    uuid4().hex,
                    self.dispatch_id,
                    self.admission.turn_id,
                    request.session_id,
                )
                dispatch = ExecutionDispatch(self.dispatch_id, execution, job_id, run_id)
                if any(d.dispatch_id == self.dispatch_id for d in request.dispatches):
                    raise RequestError("COMMAND_PAYLOAD_CONFLICT", "plan dispatch is already bound")
                return replace(request, dispatches=(*request.dispatches, dispatch))

            self.service.update_request(self.context, self.admission.request_id, bind_dispatch)
        if runtime is not None:
            from transbridge.application.assistant_requests.task_events import RequestTaskEventRouter

            with self.service.serialized(self.context):
                if not hasattr(self.service, "request_task_events"):
                    self.service.request_task_events = RequestTaskEventRouter(self.service, runtime)
                self.service.request_task_events.bind(self, effect_id, job_id, run_id, owner)

    def validate_start(self, effect_id: str) -> None:
        if effect_id:
            self.service.update_request(
                self.context, self.admission.request_id, lambda request: activate_effect(request, effect_id)
            )
        else:
            self._validate(self.current_request(), "propose_plan", require_allowed=False)

    def control_task(self, runtime, ref, actor, action):
        from transbridge.application.assistant_requests.task_controls import request_task_control

        return request_task_control(self.service, runtime)(ref, actor, action)

    def commit(self, effect_id: str, mutation, *, runtime_commit=None) -> CommitDecision:
        """Compose request and existing runtime guard; never retry an executed mutation."""
        try:
            with self.service.serialized(self.context):
                validate_effect(self.current_request(), effect_id)
                if runtime_commit is not None:
                    decision = runtime_commit(mutation)
                else:
                    mutation()
                    decision = CommitDecision(True)
                if decision.accepted:
                    receipt = f"local-commit:{effect_id}:{uuid4().hex}"

                    def save_receipt(request):
                        context = self._contexts.get(effect_id)
                        version_json = request.execution_version_json
                        if context is not None:
                            version_json = execution_version(context)
                        return replace(
                            request,
                            execution_version_json=version_json,
                            effects=tuple(
                                replace(e, receipt=receipt) if e.effect_id == effect_id else e for e in request.effects
                            ),
                        )

                    self.service.update_request(self.context, self.admission.request_id, save_receipt)
                return decision
        except RequestError as error:
            return CommitDecision(False, error.code)


class _DispatchLease:
    """Validate a scoped plan lease after its foreground turn has been released."""

    def __init__(self, gate):
        self.gate = gate

    def validate(self, admission):
        if admission != self.gate.admission:
            raise RequestError("TURN_LEASE_STALE", "plan admission identity changed")
        self.gate._validate(self.gate.current_request(), "propose_plan", require_allowed=False)
