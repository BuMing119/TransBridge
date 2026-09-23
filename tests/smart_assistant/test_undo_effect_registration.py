"""Effect preparation and undo ownership become visible in one Session commit."""

from dataclasses import replace

import pytest

from tests.smart_assistant.test_request_execution import register
from transbridge.application.assistant_requests.models import RequestError
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.types import ToolResult

pytest_plugins = ["tests.smart_assistant.test_request_execution"]


def _own_real_input(service, context):
    source = service.accept_input(context, "write", selection={})

    def link(state):
        request = service.requests(state)[0]
        state["requests"] = [
            replace(request, source_message_ids=(source["message_id"],), work_round_id=source["message_id"]).to_dict()
        ]
        next(i for i in state["ingress"] if i["message_id"] == source["message_id"])["status"] = "applied"

    service.transact(context, link)
    return source["message_id"]


def test_gate_prepares_effect_and_undo_ownership_in_one_persisted_state(setup):
    service, context, gate, ctx, _ = setup
    round_id = _own_real_input(service, context)
    register(lambda *_: ToolResult.ok("unused"))
    handle = gate.before("request_test_write", {}, ctx)
    state = service.state(context)
    effect = service.requests(state)[0].effects[0]
    assert effect.execution.work_round_id == round_id
    assert state["undo_rounds"][round_id]["effects"] == {
        effect.effect_id: {"request_id": effect.execution.request_id, "tool": "request_test_write"},
    }
    gate.after(handle, ToolResult.fail("test cleanup"))


def test_registration_failure_rolls_back_prepared_effect_and_releases_resource(setup, monkeypatch):
    from transbridge.application.assistant_requests import round_undo

    service, context, gate, ctx, _ = setup
    round_id = _own_real_input(service, context)
    register(lambda *_: ToolResult.ok("unused"))
    original = round_undo.register_in_state

    def fail(*args):
        original(*args)
        raise RequestError("UNDO_TEST_FAILURE", "reject prepared ownership")

    monkeypatch.setattr(round_undo, "register_in_state", fail)
    with pytest.raises(RequestError, match="UNDO_TEST_FAILURE"):
        gate.before("request_test_write", {}, ctx)
    state = service.state(context)
    assert not service.requests(state)[0].effects and round_id not in state.get("undo_rounds", {})
    monkeypatch.setattr(round_undo, "register_in_state", original)
    handle = gate.before("request_test_write", {}, ctx)
    assert handle.effect_id and service.requests(service.state(context))[0].effects
    gate.after(handle, ToolResult.fail("test cleanup"))


def test_gate_rejects_model_supplied_work_round_id_before_writing(setup):
    service, context, _, ctx, _ = setup
    writes = []
    spec = register(lambda *_: writes.append(True))
    result = execute_with_guardrails(spec, {"work_round_id": "forged"}, ctx, middlewares=[])
    assert result.error_code == "REQUEST_PROTOCOL_INVALID" and not writes
    assert not service.requests(service.state(context))[0].effects


def test_legacy_request_without_round_origin_rejects_writes_without_inventing_ownership(setup):
    service, context, gate, ctx, _ = setup
    service.update_request(context, "request", lambda request: replace(request, work_round_id=""))
    writes = []
    spec = register(lambda *_: writes.append(True))
    result = execute_with_guardrails(spec, {}, ctx, middlewares=[])
    assert result.error_code == "UNDO_ROUND_SOURCE_MISSING"
    assert not writes and not gate.current_request().effects
    assert not service.state(context).get("undo_rounds")


def test_missing_undo_service_rejects_write_before_effect_registration(setup):
    service, context, gate, ctx, _ = setup
    service.undo = None
    writes = []
    spec = register(lambda *_: writes.append(True))
    result = execute_with_guardrails(spec, {}, ctx, middlewares=[])
    assert result.error_code == "UNDO_STORAGE_UNAVAILABLE"
    assert not writes and not gate.current_request().effects
    assert not service.state(context).get("undo_rounds")


def test_legacy_request_can_still_read_without_undo_service(setup):
    from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    service, context, _, ctx, _ = setup
    service.update_request(context, "request", lambda request: replace(request, work_round_id=""))
    service.undo = None
    spec = ToolSpec(
        "request_test_read", "read", "read", {}, execute=lambda *_: ToolResult.ok("read"), permission="read"
    )
    ToolRegistry.register(spec)
    assert execute_with_guardrails(spec, {}, ctx, middlewares=[]).success
