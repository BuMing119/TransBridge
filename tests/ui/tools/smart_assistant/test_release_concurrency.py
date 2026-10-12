"""Late release receipts may clean up their turn, never a replacement view."""

from dataclasses import replace
from threading import Event

import pytest

from transbridge.application.assistant_requests.models import ItemKind, RequestError
from transbridge.application.contracts import RequestContext

from . import test_request_lifecycle_panel as panel_tests

environment = panel_tests.environment
_APP = panel_tests._APP
_seed_long_request = panel_tests._seed_long_request
_until = panel_tests._until


@pytest.mark.parametrize("replacement", ["session", "turn", "generation", "closed", "current"])
@pytest.mark.parametrize("receipt_failure", [False, True])
def test_delayed_release_cleans_original_waiters_without_touching_replacement(
    environment, monkeypatch, replacement, receipt_failure
):
    binding, service = environment.binding, environment.service
    request = _seed_long_request(environment)
    selected = service.scheduler.select_next_turn(request.session_id, binding.view_id, (request,), automatic=False)
    admission = selected.admission
    binding.admission = admission
    binding._set_gate()
    entered, proceed, finished = Event(), Event(), Event()
    callbacks, messages = [], []
    original_release = service.scheduler.release

    def release(value, *, wait=True):
        return original_release(value, wait=wait) if wait else False

    def receipt():
        entered.set()
        try:
            assert proceed.wait(5)
            if receipt_failure:
                raise RequestError("ADMISSION_PERSIST_FAILED", "delayed receipt failure")
        finally:
            finished.set()

    monkeypatch.setattr(service.scheduler, "release", release)
    monkeypatch.setattr(binding.gate, "interrupt_steps", receipt)
    monkeypatch.setattr(binding, "wake", lambda *_args: None)
    monkeypatch.setattr(binding.facade, "add_system_message", messages.append)
    binding.release(interrupt=False)
    assert entered.wait(2)
    assert binding.confirmations._defer(admission, lambda: callbacks.append("old confirmation"))
    next_admission = None
    try:
        if replacement == "session":
            result = environment.services.gui_session_commands.create_and_activate("Other", RequestContext("owner"))
            assert result.is_success
            assert binding.context.session_id != admission.session_id
        elif replacement == "turn":
            next_admission = service.scheduler.select_next_turn(
                request.session_id, binding.view_id, (request,), automatic=False
            ).admission
            binding.admission = next_admission
            binding._set_gate()
        elif replacement == "generation":
            binding.interrupt()
        proceed.set()
        assert finished.wait(2)
        if replacement == "closed":
            binding.close()
        _until(lambda: binding._accepting == 0)
        _APP.processEvents()
        assert admission.turn_id not in binding.confirmations._release_waiters
        assert callbacks == (["old confirmation"] if replacement == "current" and not receipt_failure else [])
        assert binding._user_stopped is (replacement == "current" and receipt_failure)
        assert bool(messages) is (replacement == "current" and receipt_failure)
        if next_admission is not None:
            assert binding.admission == next_admission
            service.scheduler.validate(next_admission)
    finally:
        proceed.set()
        if next_admission is not None:
            original_release(next_admission)


def test_real_interrupted_step_receipt_storage_failure_stays_with_original_session(environment, monkeypatch):
    binding, service = environment.binding, environment.service
    request = _seed_long_request(environment)
    request = replace(request, items=tuple(replace(item, kind=ItemKind.EXECUTION) for item in request.items))
    service.update_request(binding.context, request.request_id, lambda _old: request)
    selected = service.scheduler.select_next_turn(
        request.session_id, binding.view_id, (request,), allowed_tools=("get_statistics",), automatic=False
    )
    binding.admission = selected.admission
    binding._set_gate()
    gate = binding.gate
    gate.prepare_steps([{"tool": "get_statistics", "args": {}}])
    gate._begin_steps()
    original_context = binding.context
    entered, proceed = Event(), Event()
    attempts, messages, callbacks = [], [], []
    original_update = service.update_request
    original_release = service.scheduler.release

    def update(context, request_id, mutation, **kwargs):
        cause = kwargs.get("cause")
        if cause is not None and cause.operation == "step.result":
            attempts.append((context.session_id, request_id))
            if len(attempts) == 1:
                entered.set()
                assert proceed.wait(5)
            raise OSError("receipt storage unavailable")
        return original_update(context, request_id, mutation, **kwargs)

    monkeypatch.setattr(service, "update_request", update)
    monkeypatch.setattr(
        service.scheduler, "release", lambda value, wait=True: original_release(value) if wait else False
    )
    monkeypatch.setattr(binding, "wake", lambda *_args: None)
    monkeypatch.setattr(binding.facade, "add_system_message", messages.append)
    binding.release(interrupt=False)
    try:
        assert entered.wait(2)
        assert binding.confirmations._defer(selected.admission, lambda: callbacks.append("old approval"))
        result = environment.services.gui_session_commands.create_and_activate("Other", RequestContext("owner"))
        assert result.is_success
        assert binding.context.session_id != original_context.session_id
    finally:
        proceed.set()
    _until(lambda: binding._accepting == 0)
    assert attempts == [(original_context.session_id, request.request_id)] * 2
    assert service.step_receipts.has_pending(original_context, request.request_id)
    assert not service.step_receipts.has_pending(binding.context, request.request_id)
    assert not binding._user_stopped and not messages and not callbacks
    assert selected.admission.turn_id not in binding.confirmations._release_waiters
