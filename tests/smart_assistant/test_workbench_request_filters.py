"""A request's confirmed workbench filters survive subsequent tool calls and gate recreation."""

import pytest

from tests.conftest import make_test_collection
from tests.smart_assistant.test_request_execution import setup as request_setup
from transbridge.smart_assistant.operation_preview import build_operation_previews
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.tool_editor import _register_editor_tools
from transbridge.smart_assistant.tools.types import ExecutionContext

setup = request_setup


def test_set_scope_survives_gate_recreation_and_manual_scope_changes(setup, current_project_factory):
    from transbridge.smart_assistant.tools.tool_translator import _register_translator_tools

    service, owner, first_gate, _, _ = setup
    app = current_project_factory(make_test_collection(3)).context
    if ToolRegistry.get("set_scope") is None:
        _register_translator_tools()

    def capture(state):
        state["ingress"][0]["selection"] = {
            "active_version_identity": list(app.active_version_identity),
            "translation_scope": {"stages": [0]},
        }

    service.transact(owner, capture)
    service.scheduler.release(first_gate.admission)
    turn = service.scheduler.select_next_turn(
        owner.session_id, "view", (first_gate.current_request(),), allowed_tools=("set_scope", "get_scope_preview")
    )

    def call(tool, args):
        gate = RequestExecutionGate(service, owner, turn.admission)
        context = ExecutionContext(app_context=app, assistant_gate=gate, request_context=owner, assistant_required=True)
        return execute_with_guardrails(ToolRegistry.get(tool), args, context, middlewares=[])

    assert call("get_scope_preview", {}).data["matched"] == 1
    assert call("set_scope", {"labels": ["review"]}).success
    app.translation_scope = {"stages": [0]}
    result = call("get_scope_preview", {})
    assert result.success and result.data["matched"] == 0
    assert result.data["scope"]["labels"] == ["review"]
    assert call("set_scope", {}).success
    assert call("get_scope_preview", {}).data["matched"] == 1


def test_filter_query_preview_and_batch_write_use_the_same_request_scope(setup, current_project_factory, monkeypatch):
    service, owner, first_gate, _, _ = setup
    project = current_project_factory(
        make_test_collection(3), label_library={"review": {"name": "待校对"}, "ready": {"name": "已确认"}}
    )
    app = project.context
    names = ("set_filters", "get_visible_entries", "manage_entry_labels")
    _register_editor_tools()

    def initial_scope(state):
        state["ingress"][0]["selection"] = {
            "active_version_identity": list(app.active_version_identity),
            "workbench_source_key": app.active_key,
            "filter_state": {},
        }

    service.transact(owner, initial_scope)
    service.scheduler.release(first_gate.admission)
    selected = service.scheduler.select_next_turn(
        owner.session_id, "view", (first_gate.current_request(),), allowed_tools=names
    )

    def context():
        gate = RequestExecutionGate(service, owner, selected.admission)
        return ExecutionContext(app_context=app, assistant_gate=gate, request_context=owner, assistant_required=True)

    def call(name, args):
        return execute_with_guardrails(ToolRegistry.get(name), args, context(), middlewares=[])

    assert call("set_filters", {"labels": ["review"]}).success
    # A later manual edit to the screen must not broaden the accepted request.
    app.clear_filters()
    read = call("get_visible_entries", {})
    assert read.success and read.data["total_count"] == 0, read.message
    args = {"action": "batch_assign", "name": "已确认"}
    (preview,) = build_operation_previews([{"tool": "manage_entry_labels", "args": args}], context())
    assert preview.resolved and not preview.entries
    original = project.aggregate.snapshot()
    result = call("manage_entry_labels", args)
    assert result.success and result.data["assigned_count"] == result.data["filter_total"] == 0, result.message
    assert project.aggregate.snapshot() == original

    assert call("set_filters", {"clear": True, "stages": [1]}).success
    read = call("get_visible_entries", {})
    assert read.success and read.data["total_count"] == 1
    (preview,) = build_operation_previews([{"tool": "manage_entry_labels", "args": args}], context())
    assert [entry.entry_id for entry in preview.entries] == [read.data["entries"][0]["key"]]
    result = call("manage_entry_labels", args)
    assert result.success and result.data["assigned_count"] == 1, result.message
    assert [entry.labels for entry in project.aggregate.snapshot().entries].count(("ready",)) == 1
    assert call("get_visible_entries", {}).data["total_count"] == 1

    old_screen = app.filter_state
    with monkeypatch.context() as patch:

        def fail_save(*_, **__):
            raise OSError("request store unavailable")

        def clear_screen():
            app.clear_filters()
            return app.filter_state, False

        execution = context()
        patch.setattr(service, "update_request", fail_save)
        with pytest.raises(OSError, match="request store unavailable"):
            execution.assistant_gate.change_workbench_filter(execution, clear_screen)
    assert app.filter_state == old_screen
    assert call("get_visible_entries", {}).data["total_count"] == 1

    project.install(make_test_collection(3), key="another-source.esp")
    rejected = call("get_visible_entries", {})
    assert not rejected.success and rejected.error_code == "REQUEST_SCOPE_MISMATCH"
