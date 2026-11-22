"""The actual ReAct confirm, worker and queued label commit share the original request."""

from dataclasses import replace
import json
from threading import Event, get_ident
from unittest.mock import Mock

from PyQt6.QtCore import QObject, QTimer
import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, composed
from tests.conftest import make_test_collection
from tests.routing_fixtures import apply_routing_fixture
from tests.ui.fixtures.current_editing import current_project_factory
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.application import workbench_filters
from transbridge.smart_assistant.guardrails.permission import PermissionGuard
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tools import register_all
from transbridge.ui.context import CollectionSlot
from transbridge.ui.tools.smart_assistant.react_execution_binding import ReactExecutionBinding

__all__ = ["composed", "current_project_factory"]


@pytest.mark.parametrize("outcome", ["complete", "deny", "scan_cancel", "commit_cancel", "revoke", "switch"])
def test_confirmed_label_scan_and_commit(composed, current_project_factory, monkeypatch, outcome):
    register_all()
    project = current_project_factory(make_test_collection(3), label_library={"review": {"name": "Review"}})
    app = project.context
    app.add_slot("other", CollectionSlot("Other", make_test_collection(1)))
    app.activate_slot(next(k for k in app.slots if k != "other"))
    services, service, context = composed
    project_id, variant_id = app.active_version_identity
    context = replace(context, session_id=None, project_id=project_id, variant_id=variant_id)
    assert services.gui_session_commands.create_and_activate("Label edits", context).is_success
    context = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    service.accept_input(context, "Mark filtered rows", selection={})
    batch = service.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"][0]["items"][0].update(item_id="label", kind="execution")
    routed = apply_routing_fixture(service, context, batch.batch_id, proposal)
    request_id = routed["requests"][-1]["request_id"]
    request = next(r for r in service.requests(service.state(context)) if r.request_id == request_id)
    service.undo.projects = project.harness.service
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.select_next_turn(
        context.session_id, "view", (request,), allowed_tools=("manage_entry_labels",)
    )
    gate = RequestExecutionGate(service, context, admission.admission)
    step = {"tool": "manage_entry_labels", "args": {"action": "batch_assign", "name": "Review"}, "tool_call_id": "mark"}
    gate.prepare_steps([step])
    main = get_ident()
    entered, release = Event(), Event()
    scans, confirmations, queued, writes, delivered = [], [], [], [], []
    key = workbench_filters.entry_label_key
    publish = app.replace_projected_labels

    def pause_scan(entry):
        scans.append(get_ident())
        if len(scans) == 1:
            entered.set()
            assert release.wait(4), "Qt could not process its heartbeat during label filtering"
        return key(entry)

    def confirm(*_):
        confirmations.append(get_ident())
        return outcome != "deny"

    def save(*args, **kwargs):
        writes.append(get_ident())
        return publish(*args, **kwargs)

    monkeypatch.setattr(workbench_filters, "entry_label_key", pause_scan)
    monkeypatch.setattr(app, "replace_projected_labels", save)
    dispatch = app.safe_mutate
    monkeypatch.setattr(app, "safe_mutate", queued.append)
    handler = ToolExecutionHandler(
        app,
        Mock(),
        request_context_provider=lambda: context,
        on_confirm_permission=confirm,
        on_tool_result=lambda _, result: delivered.append(result),
    )
    handler._middlewares = [PermissionGuard()]
    handler.assistant_gate_provider = lambda: gate
    parent = QObject()
    binding = ReactExecutionBinding(
        parent=parent, handler=handler, controller=lambda: controller, system_message=Mock()
    )
    controller = SessionController(tool_handler=handler, on_execute_react_async=binding.execute)
    timer = QTimer(parent)

    def heartbeat():
        if entered.is_set() and not release.is_set():
            if outcome == "scan_cancel":
                binding.abort()
            release.set()
        if queued:
            if outcome == "commit_cancel":
                binding.abort()
            elif outcome == "revoke":
                service.update_request(
                    context,
                    gate.admission.request_id,
                    lambda request: replace(request, lease_epoch=request.lease_epoch + 1),
                )
            elif outcome == "switch":
                app.activate_slot("other")
            dispatch(queued.pop())

    timer.timeout.connect(heartbeat)
    try:
        controller.restore_confirmation({"mode": "react", "steps": [step]})
        timer.start(2)
        assert controller.handle_user_confirmed([step], confirmation_id=controller.pending_confirmation_id)
        assert confirmations == [main]
        if outcome == "deny":
            assert not gate.current_request().effects and not scans and not writes
            return

        def receipt():
            return next((e.receipt for e in gate.current_request().effects if e.receipt.startswith("{")), None)

        _until(receipt, diagnostic=lambda: (gate.current_request().effects, delivered, scans, writes))
        result = json.loads(receipt())["result"]
        assert scans and main not in scans
        if outcome == "complete":
            assert result["success"] and result["data"]["assigned_count"] == 3, result["message"]
            assert writes == [main]
            assert all(state.labels == ("review",) for state in project.aggregate.snapshot().entries)
        else:
            assert not result["success"] and not writes
            assert all(not state.labels for state in project.aggregate.snapshot().entries)
            if outcome == "scan_cancel":
                assert len(scans) == 1
    finally:
        timer.stop()
        release.set()
        binding.close()
