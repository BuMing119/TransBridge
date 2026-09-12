"""Qt projection and model-round wiring for the application request service."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import logging
from uuid import uuid4

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.scheduler import commit_answer
from transbridge.smart_assistant.native_tools import build_native_tool_definitions
from transbridge.smart_assistant.request_protocol import COVERAGE_TOOL, RETRIEVAL_TOOL, ROUTING_TOOL

from .message_bubble import MessageBubble
from .request_confirmation_binding import RequestConfirmationBinding
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
                tools = build_native_tool_definitions(
                    self.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
                )
                selection = self.service.scheduler.select_next_turn(
                    self.context.session_id,
                    self.view_id,
                    self.service.requests(state),
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
        from transbridge.application.assistant_requests.scheduler import ready_item_ids

        admission = selection.admission

        def accept(current):
            if (
                current.revision != admission.request_revision
                or current.lease_epoch != admission.request_lease_epoch
                or not set(admission.ready_item_ids) <= set(ready_item_ids(current))
            ):
                raise RequestError("TURN_LEASE_STALE", "请求在接纳前发生了变化，请重新继续。")
            return replace(current, automatic_turns=max(current.automatic_turns, selection.request.automatic_turns))

        try:
            self.service.update_request(self.context, selection.request.request_id, accept)
        except Exception:
            self.service.scheduler.release(admission)
            raise

    def prepare_model_input(self, history, max_tokens, *, context_window=32768):
        from transbridge.smart_assistant.context_budget import ContextBudget
        from transbridge.smart_assistant.request_context_assembler import RequestContextAssembler
        from transbridge.smart_assistant.request_router import routing_messages

        if self.admission is None:
            raise RequestError("TURN_LEASE_STALE", "no request admitted for this model turn")
        self.service.scheduler.validate(self.admission)
        state = self.service.state(self.context)
        if self.stage == "execution":
            current = next(r for r in self.service.requests(state) if r.request_id == self.admission.request_id)
            if current.terminal:
                self.release()
                self.wake()
                return [], ()
        tools = build_native_tool_definitions(
            self.facade._conversation.get_loaded_tool_namespaces(), request_stage=self.stage
        )
        if self.stage == "execution" and self._prepared_turn == self.admission.turn_id:
            request_id = self.admission.request_id
            self.service.scheduler.release(self.admission)
            selected = self.service.scheduler.select_next_turn(
                self.context.session_id,
                self.view_id,
                self.service.requests(state),
                priority_request_ids=(request_id,),
                allowed_tools=tuple(t.name for t in tools),
            )
            if selected is None or selected.request.request_id != request_id:
                raise RequestError("REQUEST_NOT_READY", "request is waiting or its automatic turn limit was reached")
            self.admission = selected.admission
            self._persist_selection(selected)
            self._set_gate()
        self._prepared_turn = self.admission.turn_id
        budget = ContextBudget(context_window=context_window, output_reserve=max_tokens)
        if self.stage == "routing":
            messages = routing_messages(self.batch, self.service.requests(state))
            budget.require(messages, tools)
            return messages, tools
        request = next(r for r in self.service.requests(state) if r.request_id == self.admission.request_id)
        constraints = {
            "request_id": request.request_id,
            "goal": request.goal,
            "revision": request.revision,
            "constraints": list(request.constraints),
            "items": [item.to_dict() for item in request.items],
            "ready_item_ids": list(self.admission.ready_item_ids),
            "answer_protocol": "Complete text plus report_answer_coverage for answer items; never claim execution.",
        }
        details = request.to_dict()
        constraints.update({key: details.get(key, []) for key in ("effects", "evidence", "dispatches")})
        constraints["selection"] = [
            i["selection"] for i in state.get("ingress", ()) if i["message_id"] in request.source_message_ids
        ]
        owners = state.get("message_owners", {})
        history = [
            {**m, "request_id": owners[m["message_id"]]} if m.get("message_id") in owners else m for m in history
        ]
        projection = RequestContextAssembler(budget).assemble(
            history,
            tools=tools,
            request_state=constraints,
            continuation={"reason": "continue admitted request", "request_id": request.request_id},
        )
        return projection.messages, tools

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
                arguments = parsed["arguments"]
                if set(arguments) != {"item_ids", "dispositions"} or set(arguments["item_ids"]) != set(
                    arguments["dispositions"]
                ):
                    raise RequestError("REQUEST_PROTOCOL_INVALID", "answer coverage fields disagree")
                message_id = conversation.get_transcript()[-1]["message_id"]
                request = next(
                    r
                    for r in self.service.requests(self.service.state(self.context))
                    if r.request_id == self.admission.request_id
                )
                commit_answer(
                    request,
                    self.admission,
                    turn.text,
                    arguments["dispositions"],
                    message_id=message_id,
                    finish_reason=turn.stop_reason,
                    scheduler=self.service.scheduler,
                )
                receipt_id = uuid4().hex
                receipt = {
                    "role": "tool",
                    "tool_call_id": parsed["tool_call_id"],
                    "name": control,
                    "content": json.dumps({"accepted": True}),
                    "display_summary": "",
                    "is_error": False,
                    "message_id": receipt_id,
                }
                self.service.update_request(
                    self.context,
                    self.admission.request_id,
                    lambda request: commit_answer(
                        request,
                        self.admission,
                        turn.text,
                        arguments["dispositions"],
                        message_id=message_id,
                        finish_reason=turn.stop_reason,
                        scheduler=self.service.scheduler,
                    ),
                    history=[*conversation.get_transcript(), receipt],
                )
                conversation.add_tool_result(parsed["tool_call_id"], control, {"accepted": True}, message_id=receipt_id)
                self.release()
                self.wake()
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

    def confirmation_validator(self):
        return self.confirmations.validator()

    def control(self, request_id, command):
        try:
            if command == "resume":
                self._user_stopped = False
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
            state = self.service.state(self.context)
            confirmation = state.get("confirmations", {}).get(request_id)
            if command == "resume" and confirmation and confirmation["revision"] == request.revision:
                self.service.command(
                    self.context,
                    request_id,
                    "unblock",
                    request.revision,
                    item_ids=[i.item_id for i in request.items],
                    reason="approval",
                )
                self.service.command(
                    self.context,
                    request_id,
                    "unblock",
                    request.revision,
                    item_ids=[i.item_id for i in request.items],
                    reason="approval_revalidation",
                )
                tools = build_native_tool_definitions(
                    self.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
                )
                selected = self.service.scheduler.select_next_turn(
                    self.context.session_id,
                    self.view_id,
                    self.service.requests(self.service.state(self.context)),
                    priority_request_ids=(request_id,),
                    allowed_tools=tuple(t.name for t in tools),
                    automatic=False,
                )
                if selected:
                    from .request_confirmation import renew_confirmation

                    self.admission = selected.admission
                    self._set_gate()
                    parsed = renew_confirmation(confirmation["parsed"], self.facade._conversation)
                    self.service.save_history(
                        self.context, self.facade._conversation.get_transcript(), request_id=request_id
                    )
                    if parsed.get("mode") != "plan":
                        self.gate.prepare_steps(parsed["steps"])
                    self.facade._controller.restore_confirmation(parsed)
            else:
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
        self.release()
        self.facade._confirmation_view.invalidate_pending()
        self.facade._react_execution.abort()
        self.facade._plan_execution.interrupt_round()

    def fail(self, message):
        logger.warning("Assistant request stopped: %s", message)
        if self.admission is not None and self.admission.request_id:
            try:
                self.service.command(
                    self.context,
                    self.admission.request_id,
                    "pause",
                    self.admission.request_revision,
                    reason="user_paused",
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
