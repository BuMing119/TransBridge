"""Persisted request approvals and the admission needed to execute them."""

from copy import deepcopy

from transbridge.smart_assistant.native_tools import build_native_tool_definitions


class RequestConfirmationBinding:
    def __init__(self, binding):
        self.binding = binding

    def capture(self, parsed):
        if self.binding.admission is None:
            return
        controller = self.binding.facade._controller
        self.binding.service.save_history(
            self.binding.context,
            self.binding.facade._conversation.get_transcript(),
            request_id=self.binding.admission.request_id,
        )
        if controller.state.value == "awaiting":
            admission = self.binding.admission

            def save(state):
                state.setdefault("confirmations", {})[admission.request_id] = {
                    "revision": admission.request_revision,
                    "parsed": deepcopy(parsed),
                }

            self.binding.service.transact(self.binding.context, save)
            self.binding.service.command(
                self.binding.context,
                admission.request_id,
                "wait",
                admission.request_revision,
                item_ids=self._approval_items(admission),
                reason="approval",
            )
            self.binding.release(interrupt=False)
            self.binding.wake()
        elif controller.state.value == "awaiting_task":
            self.binding.release(interrupt=False)
            self.binding.wake()

    def validator(self):
        admission = self.binding.admission
        if admission is None:
            return lambda: False

        def validate():
            if self.binding._closed or self.binding.context.session_id != admission.session_id:
                return False
            try:
                request = next(
                    r
                    for r in self.binding.service.requests(self.binding.service.state(self.binding.context))
                    if r.request_id == admission.request_id
                )
                if request.revision != admission.request_revision or request.terminal or request.pause_reasons:
                    return False
                self.binding.service.command(
                    self.binding.context,
                    request.request_id,
                    "unblock",
                    request.revision,
                    item_ids=list(admission.ready_item_ids),
                    reason="approval",
                )
                if self.binding.admission is None:
                    tools = build_native_tool_definitions(
                        self.binding.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
                    )
                    selection = self.binding.service.scheduler.select_next_turn(
                        admission.session_id,
                        self.binding.view_id,
                        self.binding.service.requests(self.binding.service.state(self.binding.context)),
                        priority_request_ids=(request.request_id,),
                        allowed_tools=tuple(t.name for t in tools),
                        automatic=False,
                    )
                    if selection is None or selection.request.request_id != request.request_id:
                        return False
                    self.binding.admission = selection.admission
                    self.binding._set_gate()
                    saved = (
                        self.binding.service
                        .state(self.binding.context)
                        .get("confirmations", {})
                        .get(request.request_id)
                    )
                    if saved and saved["parsed"].get("mode") != "plan":
                        self.binding.gate.prepare_steps(saved["parsed"]["steps"])
                return self.binding.admission.request_id == request.request_id
            except Exception as exc:
                self.binding.fail(str(exc))
                return False

        return validate

    def _approval_items(self, admission):
        request = next(
            r
            for r in self.binding.service.requests(self.binding.service.state(self.binding.context))
            if r.request_id == admission.request_id
        )
        execution = [
            i.item_id for i in request.items if i.kind == "execution" and i.item_id in admission.ready_item_ids
        ]
        return execution or list(admission.ready_item_ids)
