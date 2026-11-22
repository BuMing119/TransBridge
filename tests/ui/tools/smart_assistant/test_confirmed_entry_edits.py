"""The edits actually displayed on a card survive a fresh ReAct or plan execution context."""

from dataclasses import replace
from unittest.mock import Mock

from PyQt6.QtWidgets import QWidget
import pytest

from tests.application.assistant_requests.test_request_repository import _proposal, composed
from tests.routing_fixtures import apply_routing_fixture
from tests.ui.fixtures.current_editing import current_project_factory
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.smart_assistant.guardrails.permission import PermissionGuard
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tools import register_all
from transbridge.ui.tools.smart_assistant.confirmation_view import ConfirmationView, PlanExecutionBinding

__all__ = ["composed", "current_project_factory"]


def _gate(composed, project):
    services, service, context = composed
    project_id, variant_id = project.context.active_version_identity
    context = replace(context, session_id=None, project_id=project_id, variant_id=variant_id)
    assert services.gui_session_commands.create_and_activate("Edits", context).is_success
    context = replace(context, session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
    service.accept_input(context, "Update the translation and stage", selection={})
    batch = service.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"][0]["items"][0].update(item_id="edit", kind="execution")
    routed = apply_routing_fixture(service, context, batch.batch_id, proposal)
    request_id = routed["requests"][-1]["request_id"]
    request = next(r for r in service.requests(service.state(context)) if r.request_id == request_id)
    service.undo.projects = project.harness.service
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.select_next_turn(
        context.session_id, "view", (request,), allowed_tools=("edit_translation", "set_stage")
    )
    return RequestExecutionGate(service, context, admission.admission)


@pytest.mark.parametrize("mode", ["react", "plan"])
@pytest.mark.parametrize("change", ["target", "other"])
def test_displayed_entry_edit_survives_confirmation(
    mode, change, composed, current_project_factory, tmp_path, monkeypatch
):
    register_all()
    entries = [TranslationEntry(key, key, f"Source {key}", "", 0, "BOOK:FULL") for key in ("target", "other")]
    project = current_project_factory(entries)
    app = project.context
    gate = _gate(composed, project)
    monkeypatch.setattr(
        "transbridge.smart_assistant.graph_executor.GraphExecutor._default_checkpoint_dir", lambda _: tmp_path
    )
    steps = [
        {"id": 1, "tool": "edit_translation", "args": {"entry_id": "target", "new_translation": "  approved  "}},
        {"id": 2, "tool": "set_stage", "args": {"entry_ids": ["target"], "stage": 3}, "depends_on": [1]},
    ]
    cards, results, messages = [], [], []
    parent = QWidget()
    conversation = Mock()
    handler = ToolExecutionHandler(
        app,
        conversation,
        request_context_provider=lambda: gate.context,
        edit_review_for=lambda step: view.edit_review_for(step),
        on_tool_result=lambda step, result: (results.append(result), view.complete_tool(step, result)),
    )
    handler.assistant_gate_provider = lambda: gate
    handler._middlewares = []
    view = ConfirmationView(
        parent=parent,
        add_widget=cards.append,
        plan_confirmed=lambda proposal: plan.confirm(proposal, view),
        plan_cancelled=Mock(),
        tool_executed=Mock(),
        tool_ignored=Mock(),
        batch_executed=lambda proposal: controller.handle_user_confirmed(proposal, "react"),
        batch_ignored=Mock(),
        engine=lambda: plan.engine,
        preview_context=handler.build_execution_context,
    )
    controller = SessionController(
        tool_handler=handler,
        on_present_plan_card=view.add_plan_card,
        on_present_batch_tool_card=view.add_batch_tool_card,
    )
    plan = PlanExecutionBinding(
        context=app,
        controller=lambda: controller,
        middlewares=list,
        observability=Mock(),
        conversation=conversation,
        hide_thinking=Mock(),
        system_message=messages.append,
        execution_context=handler.build_execution_context,
    )
    try:
        controller.restore_confirmation({"mode": mode, "steps": steps})
        (card,) = cards
        assert not card._exec_btn.isEnabled()
        # A queued intent cannot run a restored proposal before that card has any preview.
        (card.confirmed if mode == "plan" else card.all_executed).emit(steps)
        assert controller.pending_confirmation_id and not gate.current_request().effects
        _until(card._exec_btn.isEnabled, diagnostic=lambda: card._preview._summary.text())
        first, second = card._preview._rows
        assert (first.before, first.after, second.before, second.after) == ("", "approved", "approved", "approved")
        assert " → " in first.detail and " → " in second.detail

        assert app.update_projected_entry(
            app.collection.get(change).identity,
            translation="manual",
            stage=1,
            expected_project_revision=app.project_revision,
            expected_variant_revision=app.variant_revision,
            expected_variant_ref=project.variant_ref,
        ).is_success
        # Approving a restored card obtains a new admission/context for the same request.
        gate = RequestExecutionGate(gate.service, gate.context, gate.admission)
        if mode == "react":
            gate.prepare_steps(steps)
        card._exec_btn.click()
        if mode == "plan":
            _until(
                lambda: conversation.add_plan_result.called or card._exec_btn.text() == "执行失败",
                diagnostic=lambda: (messages, card._progress_label.text(), gate.current_request().effects),
            )
            assert conversation.add_plan_result.called, messages
        else:
            assert len(results) == 2
        effects = gate.current_request().effects
        assert effects, messages
        current = app.collection.get("target")
        if change == "target":
            assert all(effect.status == "failed" for effect in effects)
            assert (current.translation, current.stage) == ("manual", 1)
        else:
            assert len(effects) == 2 and all(effect.status == "succeeded" for effect in effects), (effects, messages)
            assert (current.translation, current.stage) == ("approved", 3)
            assert app.collection.get("other").translation == "manual"
    finally:
        plan.close()
        view.close()
        parent.close()


def test_automatic_stage_write_captures_target_before_permission_dialog(current_project_factory):
    register_all()
    project = current_project_factory([TranslationEntry("row", "row", "Original", "shown", 1, "BOOK:FULL")])
    app = project.context
    step = {"tool": "set_stage", "args": {"entry_ids": ["row"], "stage": 3}}

    def confirm(*_):
        assert app.update_projected_entry(
            app.collection.get("row").identity,
            translation="manual",
            expected_project_revision=app.project_revision,
            expected_variant_revision=app.variant_revision,
            expected_variant_ref=project.variant_ref,
        ).is_success
        return True

    handler = ToolExecutionHandler(app, Mock(), edit_review_for=lambda _: None, on_confirm_permission=confirm)
    handler._middlewares = [PermissionGuard(write_require_confirm=True)]
    result = handler.execute_step(step)
    assert not result.success and "已被其他操作修改" in result.message
    entry = app.collection.get("row")
    assert (entry.translation, entry.stage) == ("manual", 1)
