"""Qt projection and model-round wiring for the application request service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.turns import accept_turn, record_turn_failure
from transbridge.smart_assistant.native_tools import build_native_tool_definitions
from transbridge.smart_assistant.request_protocol import (
    COVERAGE_TOOL,
    HISTORY_RETRIEVAL_TOOL,
    RETRIEVAL_TOOL,
    ROUTING_TOOL,
    STATE_RETRIEVAL_TOOL,
)

from .message_bubble import MessageBubble
from .request_background import RequestBackground
from .request_confirmation_binding import RequestConfirmationBinding
from .request_context_preparation import RequestContextPreparation
from .request_control_binding import RequestControlBinding
from .request_list_view import RequestListView
from .request_management_binding import RequestManagementBinding
from .request_undo_binding import RequestUndoBinding

logger = logging.getLogger(__name__)


class RequestBinding(QObject):
    delivered = pyqtSignal(object)

    def __init__(self, facade, service):
        super().__init__(facade)
        self.facade = facade
        self.service = service
        self.confirmations = RequestConfirmationBinding(self)
        self.context_preparation = RequestContextPreparation(self)
        self.management = RequestManagementBinding(self)
        self.view_id = uuid4().hex
        self.context = None
        self.admission = None
        self.batch = None
        self.gate = None
        self._closed = False
        self._accepting = 0
        self._prepared_turn = None
        self._active = True
        self._user_stopped = False
        self._priority = ()
        self._turn_generation = 0
        self._queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assistant-input")
        self.background = RequestBackground(self)
        self.control_results = RequestControlBinding(self)
        self.delivered.connect(lambda callback: callback())
        self._unsubscribe = service.subscribe(lambda sid: self.delivered.emit(lambda: self._changed(sid)))
        self.view = RequestListView(facade)
        self.undo = RequestUndoBinding(self)
        self.view.undo_round.connect(self.undo.request)
        self.view.control.connect(self.control)
        self.view.clarify.connect(self.management.clarify)
        self.view.stop_generation.connect(self.management.stop_generation)
        self.view.retry_input.connect(self.management.retry_inputs)
        self.view.timeline.connect(self.management.show_timeline)
        facade._main_layout.insertWidget(0, self.view)
        self.view.hide()

    @property
    def stage(self):
        return None if self.admission is None else self.admission.stage

    def load(self, values):
        self.interrupt()
        if self.context is not None:
            old_context = self.context
            self.background.submit(lambda: self.service.scheduler.deactivate(old_context.session_id, self.view_id))
        self.context = self.facade._session_runtime.request_context()
        if not self.context.session_id:
            return
        context = self.context
        history = self.facade._conversation.get_transcript()

        def restore():
            from transbridge.application.assistant_requests.control_operations import recover_control_calls
            from transbridge.persistence.v2.ids import SessionId, SessionRef

            self.service.scheduler.activate(context.session_id, self.view_id)
            self.service.ensure_recovered(context)
            recover_control_calls(self.service, context)
            self.service.save_history(context, history)
            snapshot = self.service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
            return snapshot.backend_messages()

        def restored(records):
            self.facade._conversation.merge_saved_records(records)
            self.refresh()

        self.background.submit(restore, restored, context=context, wake=True)

    def submit(self, text):
        if self._closed or not text.strip():
            return
        context = self.facade._session_runtime.request_context()
        if not context.session_id:
            self.fail("请先创建或选择一个会话。")
            return
        self.context = context
        self._user_stopped = False
        self.interrupt()
        selection = self._selection()
        self._accepting += 1

        def accept():
            self.service.scheduler.activate(context.session_id, self.view_id)
            return self.service.accept_input(context, text.strip(), selection=selection)

        future = self._queue.submit(accept)

        def ready(completed):
            def display():
                self._accepting -= 1
                try:
                    accepted = completed.result()
                except Exception as exc:
                    self.facade.set_input(text)
                    self.fail(f"输入尚未接纳，请重试：{exc}")
                    return
                if self._closed or self.context.session_id != context.session_id:
                    return  # ingress is safely saved for its original Session
                self.facade._conversation.add_user(text.strip(), message_id=accepted["message_id"])
                self.facade._message_list.add_bubble(MessageBubble(text.strip(), "user", theme=self.facade._theme))
                self.wake()

            try:
                self.delivered.emit(display)
            except RuntimeError:
                logger.info("Input persisted after the chat view was destroyed")

        future.add_done_callback(ready)

    def _selection(self):
        ctx = self.facade._ctx
        selected = getattr(ctx, "selected_entries", ()) or ()
        return {
            "project_revision": getattr(ctx, "project_revision", None),
            "variant_revision": getattr(ctx, "variant_revision", None),
            "active_version_identity": (
                list(ctx.active_version_identity) if getattr(ctx, "active_version_identity", None) is not None else None
            ),
            "selected_entry_ids": [str(getattr(entry, "key", entry)) for entry in selected],
            "filter_state": deepcopy(getattr(ctx, "filter_state", {}) or {}),
            "translation_scope": deepcopy(getattr(ctx, "translation_scope", {}) or {}),
        }

    def refresh(self):
        if self.context is not None and self.context.session_id and not self._closed:
            self.management.refresh()

    def _changed(self, session_id):
        if self.context is not None and self.context.session_id == session_id:
            self.refresh()
            self.wake(session_id)

    def wake(self, session_id=None):
        if (
            self._closed
            or self._user_stopped
            or not self._active
            or self.context is None
            or self._accepting
            or self.admission is not None
        ):
            return
        if session_id is not None and session_id != self.context.session_id:
            return
        QTimer.singleShot(0, self._next)

    def _next(self):
        if (
            self._closed
            or self._user_stopped
            or not self._active
            or self.context is None
            or self._accepting
            or self.admission is not None
        ):
            return
        from .request_turn_selection import select_next

        select_next(self)

    def _set_gate(self):
        from transbridge.smart_assistant.request_execution import RequestExecutionGate

        self.gate = RequestExecutionGate(self.service, self.context, self.admission)

    def _persist_selection(self, selection):
        accept_turn(self.service, self.context, selection)

    def prepare_model_input(
        self, history, max_tokens, *, context_window=None, prepared_summary=None, defer_assembly=False
    ):
        from transbridge.smart_assistant.context_budget import budget_for_config
        from transbridge.smart_assistant.request_model_input import RequestModelInput
        from transbridge.smart_assistant.request_router import routing_messages

        if self.admission is None:
            raise RequestError("TURN_LEASE_STALE", "no request admitted for this model turn")
        self.service.scheduler.validate(self.admission)
        state = self.service.state(self.context)
        if self.stage == "execution":
            from transbridge.application.assistant_requests.scheduler import ready_item_ids

            current = next(r for r in self.service.requests(state) if r.request_id == self.admission.request_id)
            if current.terminal:
                self.release()
                self.wake()
                return [], ()
            if (
                current.revision != self.admission.request_revision
                or current.lease_epoch != self.admission.request_lease_epoch
            ):
                raise RequestError("TURN_LEASE_STALE", "request changed while preparing model context")
            if current.pause_reasons or (
                self._prepared_turn != self.admission.turn_id
                and not set(self.admission.ready_item_ids) <= set(ready_item_ids(current))
            ):
                self.release()
                self.wake()
                return [], ()
        tools = build_native_tool_definitions(
            self.facade._conversation.get_loaded_tool_namespaces(), request_stage=self.stage
        )
        if self.stage == "execution" and self._prepared_turn == self.admission.turn_id:
            request_id = self.admission.request_id
            self.service.scheduler.release(self.admission)
            self.admission = None
            selected = self.service.scheduler.select_next_turn(
                self.context.session_id,
                self.view_id,
                tuple(request for request in self.service.requests(state) if request.request_id == request_id),
                allowed_tools=tuple(t.name for t in tools),
            )
            if selected is None:
                raise RequestError("REQUEST_NOT_READY", "request is waiting or its automatic turn limit was reached")
            self.admission = selected.admission
            self._persist_selection(selected)
            self._set_gate()
        self._prepared_turn = self.admission.turn_id
        budget = budget_for_config(
            self.facade._orchestrator._cached_llm_config, max_tokens, context_window=context_window
        )
        if self.stage == "routing":
            messages = routing_messages(self.batch, self.service.requests(state), history=history)
            prepared = RequestModelInput(tuple(messages), tools, budget)
            return prepared if defer_assembly else prepared.assemble()
        request = next(r for r in self.service.requests(state) if r.request_id == self.admission.request_id)
        from transbridge.application.assistant_context.state_projection import decision_context

        constraints = decision_context(request, self.admission, state)
        prepared = RequestModelInput(
            tuple(history),
            tools,
            budget,
            request=request,
            request_state=constraints,
            requests=self.service.requests(state),
            message_owners={**state.get("result_owners", {}), **state.get("message_owners", {})},
            prepared_summary=prepared_summary,
        )
        return prepared if defer_assembly else prepared.assemble()

    def handle_response(self, parsed, turn):
        """Return true for independently handled control; business response uses controller."""
        try:
            control = parsed.get("control")
            conversation = self.facade._conversation
            if control == ROUTING_TOOL:
                self.control_results.start(parsed)
                return True
            if control == COVERAGE_TOOL:
                from .request_answer_binding import accept_answer

                accept_answer(self, parsed, turn)
                return True
            if control == HISTORY_RETRIEVAL_TOOL:
                from .request_history_binding import retrieve_history

                retrieve_history(self, parsed)
                return True
            if control == STATE_RETRIEVAL_TOOL:
                from .request_state_binding import retrieve_state

                retrieve_state(self, parsed)
                return True
            if control == RETRIEVAL_TOOL:
                self.control_results.start(parsed)
                return True
            if not parsed.get("steps"):
                raise RequestError("ANSWER_INCOMPLETE", "回答未附完成声明，请点击继续重试。")
            self.service.scheduler.validate(self.admission)
            self.service.save_history(self.context, conversation.get_transcript(), request_id=self.admission.request_id)
            if parsed.get("mode") != "plan":
                self.gate.prepare_steps(parsed["steps"])
            return False
        except Exception as exc:
            self.facade._conversation.close_pending_tool_calls(f"请求控制未接纳：{exc}")
            self.fail(str(exc))
            return True

    def after_business_response(self, parsed):
        self.confirmations.capture(parsed)

    def confirmation_validator(self, *, accepted=True):
        return self.confirmations.validator(accepted=accepted)

    def control(self, request_id, command):
        context = self.context
        if command == "resume":
            self._user_stopped = False
        self.interrupt()

        def apply():
            if command == "resume":
                from transbridge.application.assistant_context.admission import clear_wait

                clear_wait(self.service, context, request_id)
            request = next(r for r in self.service.requests(self.service.state(context)) if r.request_id == request_id)
            if command not in {"reconcile", "reassign"} and not (command == "resume" and request.terminal):
                self.service.command(context, request_id, command, request.revision)
            return request

        def received(request):
            if command == "reconcile":
                from .request_outcome_binding import reconcile_outcome

                reconcile_outcome(self, request_id)
            elif command == "reassign":
                self.management.reassign(request_id)
            elif command == "resume" and request.terminal:
                self.management.restart(request)
            elif command != "resume" or not self.confirmations.restore(request_id):
                self.wake()
            if command == "cancel":
                self.undo.stopped(request_id)
            self.refresh()

        self.background.submit(apply, received, context=context)

    def release(self, *, interrupt=True):
        self._turn_generation += 1
        self.control_results.cancel()
        self.undo.invalidate()
        if self.admission is not None:
            admission = self.admission
            self.background.submit(lambda: self.service.scheduler.release(admission), wake=True)
        self.admission = self.batch = self.gate = None
        if interrupt:
            self.facade._controller.handle_round_interrupted()
        self.refresh()

    def interrupt(self):
        self.context_preparation.cancel()
        self.release()
        self.facade._confirmation_view.invalidate_pending()
        self.facade._react_execution.abort()
        self.facade._plan_execution.interrupt_round()

    def fail(self, message):
        logger.warning("Assistant request stopped: %s", message)
        self.management.capture_failed_turn()
        context, admission, batch = self.context, self.admission, self.batch
        history = self.facade._conversation.get_transcript()
        self._user_stopped = True
        self.release()
        if admission is not None:

            def save_failure():
                record_turn_failure(self.service, context, admission, batch=batch, history=history)

            self.background.submit(
                save_failure,
                context=context,
                failed=lambda exc: self.facade.add_system_message(f"请求中断状态未能保存：{exc}"),
            )
        self.facade.add_system_message(str(message))

    def close(self):
        if self._closed:
            return
        self.interrupt()
        self._closed = True
        if self.context is not None:
            context = self.context
            self._queue.submit(self.service.scheduler.deactivate, context.session_id, self.view_id)
        self._unsubscribe()
        self.context_preparation.close()
        # Application teardown closes the persistence service next. Drain inputs
        # already submitted to this queue before relinquishing that writer.
        self._queue.shutdown(wait=True, cancel_futures=False)

    def set_active(self, active):
        self._active = active
        if not active:
            self.interrupt()
            if self.context is not None:
                context = self.context
                self.background.submit(lambda: self.service.scheduler.deactivate(context.session_id, self.view_id))
        elif self.context is not None and not self._closed:
            context = self.context
            self.background.submit(lambda: self.service.scheduler.activate(context.session_id, self.view_id), wake=True)
