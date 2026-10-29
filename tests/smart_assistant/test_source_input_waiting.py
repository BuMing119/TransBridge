from dataclasses import replace
import json

import pytest

from tests.smart_assistant.test_request_execution import register, setup  # noqa: F401
from transbridge.application.assistant_requests.dispatch import reconcile_dispatches
from transbridge.application.assistant_requests.models import StepReceipt
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.types import ExecutionContext, ToolResult


def _missing():
    return ToolResult.fail(
        "需要补充输入",
        error_code="SOURCE_INPUT_REQUIRED",
        data={
            "source_input_repair": {"kind": "source_input", "requirement": "originals", "source_keys": ["saved-source"]}
        },
    )


def _execute(gate, ctx, result):
    existing = ToolRegistry.get("request_test_write")
    spec = replace(existing, execute=lambda *_: result) if existing else register(lambda *_: result)
    gate.prepare_steps([{"tool": spec.name, "args": {}}])
    return execute_with_guardrails(spec, {}, ctx, middlewares=[])


def test_missing_source_retains_failed_receipt_and_can_retry_with_fresh_admission(setup):  # noqa: F811
    service, context, gate, ctx, _manager = setup
    result = _execute(gate, ctx, _missing())
    assert not result.success
    waiting = gate.current_request()
    assert not waiting.terminal
    assert waiting.items[0].status == "waiting"
    assert waiting.items[0].waiting_reasons == ("source_input_required",)
    assert waiting.effects[0].status == "failed"
    assert json.loads(waiting.effects[0].receipt)["result"]["data"] == result.data
    assert waiting.dispatches[0].status == "failed"

    ready = service.command(
        context,
        waiting.request_id,
        "unblock",
        waiting.revision,
        item_ids=["write"],
        reason="source_input_required",
    )
    assert reconcile_dispatches(ready).items[0].status == "pending"
    service.scheduler.release(gate.admission)
    selected = service.scheduler.select_next_turn(
        context.session_id,
        "view",
        (ready,),
        allowed_tools=("request_test_write",),
    )
    retry_gate = RequestExecutionGate(service, context, selected.admission)
    retry_ctx = ExecutionContext(request_context=context, assistant_gate=retry_gate, assistant_required=True)
    retried = _execute(retry_gate, retry_ctx, ToolResult.ok("done"))
    assert retried.success
    after = retry_gate.current_request()
    assert [effect.status for effect in after.effects] == ["failed", "succeeded"]
    assert after.items[0].status == "satisfied"


def test_input_repair_does_not_clear_other_waits_or_hide_another_failed_step(setup):  # noqa: F811
    service, context, gate, ctx, _manager = setup
    _execute(gate, ctx, _missing())
    waiting = gate.current_request()
    service.command(context, waiting.request_id, "wait", waiting.revision, item_ids=["write"], reason="other_input")
    after = service.command(
        context,
        waiting.request_id,
        "unblock",
        waiting.revision,
        item_ids=["write"],
        reason="source_input_required",
    )
    assert after.items[0].waiting_reasons == ("other_input",)
    assert after.items[0].status == "waiting"
    broken = replace(
        waiting,
        dispatches=(replace(waiting.dispatches[0], step_receipts=(StepReceipt("0", "failed", "UNRELATED_FAILURE"),)),),
    )
    assert reconcile_dispatches(broken).items[0].status == "failed"


@pytest.mark.parametrize("data", [None, {"source_input_repair": {"requirement": "originals", "source_keys": []}}])
def test_same_error_code_without_an_actionable_repair_keeps_existing_failure_behavior(setup, data):  # noqa: F811
    _service, _context, gate, ctx, _manager = setup
    result = ToolResult.fail("not repairable", error_code="SOURCE_INPUT_REQUIRED", data=data)
    _execute(gate, ctx, result)
    assert gate.current_request().items[0].status == "failed"
