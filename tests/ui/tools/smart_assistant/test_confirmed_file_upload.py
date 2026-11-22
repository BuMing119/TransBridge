"""A confirmed ReAct file upload keeps Qt responsive and its receipt in the original request."""

from dataclasses import replace
import json
from threading import Event, get_ident
from unittest.mock import Mock

from PyQt6.QtCore import QObject, QTimer
import pytest

from tests.smart_assistant.test_request_execution import setup as request_setup
from tests.smart_assistant.tools.test_project_file_upload import bound, harness, project_fixture
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.smart_assistant.guardrails.permission import PermissionGuard
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.ui.tools.smart_assistant.react_execution_binding import ReactExecutionBinding

__all__ = ["bound", "harness", "project_fixture"]
setup = request_setup


@pytest.mark.parametrize("outcome", ["complete", "uncertain", "cancel", "revoke", "deny", "switch"])
def test_confirmed_upload_returns_file_receipt_to_admitted_request(setup, bound, monkeypatch, outcome):
    service, context, previous, _, _ = setup
    ctx, args, remote = bound
    args = {**args, "source_ids": list(ctx.app_context.slots)}
    preview = ToolRegistry.get("compare_with_remote").execute(args, ctx)
    keys = [file["file_key"] for file in preview.data["files"]]
    assert len(keys) >= 2
    remote.reads.clear()
    step = {"tool": "upload_entries", "args": {**args, "file_keys": keys}, "tool_call_id": "publish"}
    steps = [step]
    if outcome == "complete":
        steps = [
            {"tool": "compare_with_remote", "args": args, "tool_call_id": "before"},
            step,
            {"tool": "compare_with_remote", "args": args, "tool_call_id": "after"},
        ]
    service.scheduler.release(previous.admission)
    selected = service.scheduler.select_next_turn(
        context.session_id,
        "view",
        (previous.current_request(),),
        allowed_tools=("upload_entries", "compare_with_remote"),
    )
    gate = RequestExecutionGate(service, context, selected.admission)
    gate.prepare_steps(steps)
    main_thread = get_ident()
    confirmations, delivered, worker_threads = [], [], []
    entered, release = Event(), Event()
    create = remote.create

    def wait_for_ui(*args):
        worker_threads.append(get_ident())
        result = create(*args)
        entered.set()
        assert release.wait(4), "Qt could not process a timer during file publication"
        return result

    def uncertain(*args, **kwargs):
        raise TimeoutError("translation receipt lost")

    monkeypatch.setattr(remote, "create", wait_for_ui)
    if outcome == "uncertain":
        monkeypatch.setattr(remote, "translate", uncertain)

    def confirm(*_):
        confirmations.append(get_ident())
        if outcome == "switch":
            ctx.app_context.resolve_paratranz_target().account_user_id += 1
        return outcome != "deny"

    handler = ToolExecutionHandler(
        ctx.app_context,
        Mock(),
        request_context_provider=lambda: context,
        on_confirm_permission=confirm,
        on_tool_result=lambda original, result: delivered.append((original, result, get_ident())),
    )
    handler._middlewares = [PermissionGuard()]
    handler.assistant_gate_provider = lambda: gate
    parent = QObject()
    binding = ReactExecutionBinding(
        parent=parent, handler=handler, controller=lambda: controller, system_message=Mock()
    )
    controller = SessionController(tool_handler=handler, on_execute_react_async=binding.execute)
    timer = QTimer(parent)

    def while_uploading():
        if not entered.is_set():
            return
        timer.stop()
        if outcome == "cancel":
            binding.abort()
            assert remote.cancellation.is_set()
        elif outcome == "revoke":
            service.update_request(
                context,
                gate.admission.request_id,
                lambda request: replace(request, lease_epoch=request.lease_epoch + 1),
            )
        release.set()

    timer.timeout.connect(while_uploading)
    try:
        controller.restore_confirmation({"mode": "react", "steps": steps})
        assert not remote.reads and not remote.writes
        timer.start(2)
        assert controller.handle_user_confirmed(steps, confirmation_id=controller.pending_confirmation_id)
        _until(lambda: confirmations == [main_thread])
        if outcome == "deny":
            assert delivered[0][1].error_code == "CONFIRMATION_DENIED"
            assert not remote.reads and not remote.writes and not gate.current_request().effects
            return
        _until(lambda: any(effect.receipt for effect in gate.current_request().effects))
        record = json.loads(gate.current_request().effects[0].receipt)
        assert record["tool"] == "upload_entries"
        result = record["result"]
        if outcome == "switch":
            assert not result["success"] and "重新确认" in result["message"] and not remote.reads and not remote.writes
            return
        files = result["data"]["files"]
        assert len(files) == len(keys) and worker_threads and main_thread not in worker_threads
        assert result["success"] == (outcome == "complete")
        if outcome == "cancel":
            assert result["partial"] and result["data"]["cancelled_count"] == len(keys)
            assert len(remote.writes) == 1 and not delivered
        else:
            _until(lambda: len(delivered) == len(steps))
            assert all(
                original is item and thread == main_thread
                for (original, _, thread), item in zip(delivered, steps, strict=True)
            )
            upload = next(feedback for original, feedback, _ in delivered if original is step)
            assert upload.data["files"] == files
            if outcome == "complete":
                assert not any(file["exists"] for file in delivered[0][1].data["files"])
                assert all(file["exists"] for file in delivered[-1][1].data["files"])
            if outcome == "uncertain":
                assert result["partial"] and all(file["status"] == "uncertain" for file in files)
            if outcome == "revoke":
                assert result["partial"] and len(remote.writes) == 1
        assert files[0]["source_applied"]
    finally:
        timer.stop()
        release.set()
        binding.close()
