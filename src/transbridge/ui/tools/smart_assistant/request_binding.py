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
)

from .message_bubble import MessageBubble
from .request_confirmation_binding import RequestConfirmationBinding
from .request_context_preparation import RequestContextPreparation
from .request_list_view import RequestListView
from .request_management_binding import RequestManagementBinding

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
        self._queue = ThreadPoolExecutor(max_workers=1, thread_name_prefix="assistant-input")
        self.delivered.connect(lambda callback: callback())
        self._unsubscribe = service.subscribe(lambda sid: self.delivered.emit(lambda: self.wake(sid)))
        self.view = RequestListView(facade)
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
            self.service.scheduler.deactivate(self.context.session_id, self.view_id)
        self.context = self.facade._session_runtime.request_context()
        if not self.context.session_id:
            return
        self.service.scheduler.activate(self.context.session_id, self.view_id)
        self.service.ensure_recovered(self.context)
        self.service.save_history(self.context, self.facade._conversation.get_transcript())
        self.refresh()
        self.wake()

    def submit(self, text):
        if self._closed or not text.strip():
            return
        context = self.facade._session_runtime.request_context()
        if not context.session_id:
            self.fail("请先创建或选择一个会话。")
            return
        self.context = context
        self._user_stopped = False
        self.service.scheduler.activate(context.session_id, self.view_id)
        self.interrupt()
        selection = self._selection()
        self._accepting += 1
        future = self._queue.submit(self.service.accept_input, context, text.strip(), selection=selection)

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
        try:
            batch = self.service.prepare_batch(self.context)
            if batch is not None:
                admission = self.service.scheduler.acquire_routing(self.context.session_id, self.view_id)
                if admission is None:
                    return
                self.batch, self.admission = batch, admission
            else:
                state = self.service.state(self.context)
                from transbridge.application.assistant_context.admission import available_requests
                from transbridge.smart_assistant.context_runtime import configuration_digest

                cfg = getattr(self.facade._orchestrator, "_cached_llm_config", None)
                requests = available_requests(state, self.service.requests(state), configuration_digest(cfg))
                tools = build_native_tool_definitions(
                    self.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
                )
                selection = self.service.scheduler.select_next_turn(
                    self.context.session_id,
                    self.view_id,
                    requests,
                    priority_request_ids=self._priority,
                    allowed_tools=tuple(tool.name for tool in tools),
                )
                if selection is None:
                    self.refresh()
                    return
                self._persist_selection(selection)
                self.admission = selection.admission
                self._priority = ()
                self._set_gate()
            self.facade._controller.handle_round_interrupted()
            self.facade._controller.handle_user_message("")
            self.refresh()
        except Exception as exc:
            self.fail(str(exc))

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
            messages = routing_messages(self.batch, self.service.requests(state))
            prepared = RequestModelInput(tuple(messages), tools, budget)
            return prepared if defer_assembly else prepared.assemble()
        request = next(r for r in self.service.requests(state) if r.request_id == self.admission.request_id)
        from transbridge.application.assistant_context.state_projection import required_state

        constraints = required_state(request, self.admission, state)
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
            self.service.scheduler.validate(self.admission)
            control = parsed.get("control")
            conversation = self.facade._conversation
            if control == ROUTING_TOOL:
                state = self.service.apply_routing(self.context, self.batch.batch_id, parsed["arguments"])
                receipt = next(b["batch"] for b in state["batches"] if b["batch"]["batch_id"] == self.batch.batch_id)
                self._priority = tuple(r["request_id"] for r in receipt.get("receipts", ()) if r["status"] == "applied")
                conversation.add_tool_result(parsed["tool_call_id"], control, receipt)
                self.service.save_history(self.context, conversation.get_transcript())
                self.release()
                self.wake()
                return True
            if control == COVERAGE_TOOL:
                from .request_answer_binding import accept_answer

                accept_answer(self, parsed, turn)
                return True
            if control == HISTORY_RETRIEVAL_TOOL:
                from .request_history_binding import retrieve_history

                retrieve_history(self, parsed)
                return True
            if control == RETRIEVAL_TOOL:
                self._retrieve(parsed)
                self.service.save_history(self.context, conversation.get_transcript())
                QTimer.singleShot(0, self.facade._orchestrator.start_round)
                return True
            if not parsed.get("steps"):
                raise RequestError("ANSWER_INCOMPLETE", "回答未附完成声明，请点击继续重试。")
            self.service.save_history(self.context, conversation.get_transcript(), request_id=self.admission.request_id)
            if parsed.get("mode") != "plan":
                self.gate.prepare_steps(parsed["steps"])
            return False
        except Exception as exc:
            self.facade._conversation.close_pending_tool_calls(f"请求控制未接纳：{exc}")
            self.fail(str(exc))
            return True

    def _retrieve(self, parsed):
        args = parsed["arguments"]
        if (
            set(args) != {"message_id", "offset", "limit"}
            or type(args["offset"]) is not int
            or type(args["limit"]) is not int
        ):
            raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid retrieval arguments")
        if args["offset"] < 0 or not 1 <= args["limit"] <= 8000:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid retrieval range")
        result = self.service.read_result(
            self.context, self.admission.request_id, args["message_id"], offset=args["offset"], limit=args["limit"]
        )
        self.facade._conversation.add_tool_result(parsed["tool_call_id"], RETRIEVAL_TOOL, result)

    def after_business_response(self, parsed):
        self.confirmations.capture(parsed)

    def confirmation_validator(self, *, accepted=True):
        return self.confirmations.validator(accepted=accepted)

    def control(self, request_id, command):
        try:
            if command == "resume":
                self._user_stopped = False
                from transbridge.application.assistant_context.admission import clear_wait

                clear_wait(self.service, self.context, request_id)
            if command == "reconcile":
                from .request_outcome_binding import reconcile_outcome

                reconcile_outcome(self, request_id)
                return
            if command == "reassign":
                self.management.reassign(request_id)
                return
            self.interrupt()
            request = next(
                r for r in self.service.requests(self.service.state(self.context)) if r.request_id == request_id
            )
            if command == "resume" and request.terminal:
                self.management.restart(request)
                return
            self.service.command(self.context, request_id, command, request.revision)
            if command != "resume" or not self.confirmations.restore(request_id):
                self.wake()
            self.refresh()
        except Exception as exc:
            self.fail(str(exc))

    def release(self, *, interrupt=True):
        if self.admission is not None:
            self.service.scheduler.release(self.admission)
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
        if self.admission is not None:
            try:
                record_turn_failure(
                    self.service,
                    self.context,
                    self.admission,
                    batch=self.batch,
                    history=self.facade._conversation.get_transcript(),
                )
            except Exception:
                logger.exception("Could not persist request interruption")
        self.release()
        self.facade.add_system_message(message)

    def close(self):
        if self._closed:
            return
        self.interrupt()
        self._closed = True
        if self.context is not None:
            self.service.scheduler.deactivate(self.context.session_id, self.view_id)
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
                self.service.scheduler.deactivate(self.context.session_id, self.view_id)
        elif self.context is not None and not self._closed:
            self.service.scheduler.activate(self.context.session_id, self.view_id)
            self.wake()
