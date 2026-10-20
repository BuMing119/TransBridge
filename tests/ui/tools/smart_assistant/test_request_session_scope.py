from __future__ import annotations

from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.application.assistant_requests.service import RequestService
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.smart_assistant import request_binding, request_turn_selection
from transbridge.ui.tools.smart_assistant.request_binding import RequestBinding
from transbridge.ui.tools.smart_assistant.request_context_preparation import RequestContextPreparation
from transbridge.ui.tools.smart_assistant.request_session_loading import RequestSessionLoading


class _Background:
    def __init__(self):
        self.calls = []

    def submit(self, work, received=None, **kwargs):
        self.calls.append((work, received, kwargs))


def _binding(*, ready=True):
    access = SimpleNamespace(ready=ready)
    current = RequestContext("owner", session_id="session-a", project_id="project-a", variant_id="variant-a")
    facade = SimpleNamespace(
        _navigation={"session_scope_ready": lambda: access.ready},
        _session_runtime=SimpleNamespace(request_context=lambda: current),
        _conversation=Mock(),
        _controller=Mock(),
        _orchestrator=SimpleNamespace(_cached_llm_config=None),
    )
    facade._conversation.get_transcript.return_value = []
    inputs = SimpleNamespace(generation=0, present_failures=Mock())

    def invalidate():
        inputs.generation += 1

    inputs.invalidate = invalidate
    service = Mock()
    service.requests = RequestService.requests
    binding = SimpleNamespace(
        facade=facade,
        service=service,
        inputs=inputs,
        view=Mock(),
        management=Mock(),
        interrupt=Mock(),
        background=_Background(),
        context=None,
        view_id="view",
        _closed=False,
        _active=True,
        _accepting=0,
        _user_stopped=False,
        _turn_generation=0,
        _priority=(),
        admission=None,
        batch=None,
        _set_gate=Mock(),
        _next=Mock(),
        refresh=Mock(),
    )
    binding.session_loading = RequestSessionLoading(binding)
    binding.control = lambda request_id, command: RequestBinding.control(binding, request_id, command)
    return binding, access


def _values(*, preview=True):
    request = UserRequest("request-b", "session-b", "Inspect project B", (RequestItem("item", "Inspect"),))
    return {
        "session_id": "session-b",
        "project_id": "project-b",
        "variant_id": "variant-b",
        "_preview": preview,
        "assistant_state": {"requests": [request.to_dict()]},
    }


def test_preview_displays_saved_requests_and_target_scope_without_recovery_or_writes():
    binding, _access = _binding(ready=False)
    values = _values()

    binding.session_loading.load(values)

    assert binding.context == RequestContext(
        "owner", session_id="session-b", project_id="project-b", variant_id="variant-b"
    )
    assert binding.session_loading.preview
    displayed = binding.view.display.call_args.args[0]
    assert len(displayed) == 1
    assert displayed[0].goal == "Inspect project B"
    assert binding.background.calls == []
    assert binding.service.mock_calls == []
    binding.view.show_undo.assert_called_once_with("", available=False)
    values["assistant_state"]["requests"][0]["goal"] = "changed outside viewer"
    RequestBinding.refresh(binding)
    assert binding.view.display.call_args.args[0][0].goal == "Inspect project B"
    binding.management.refresh.assert_not_called()


def test_preview_can_deactivate_old_view_but_never_activates_target():
    binding, _access = _binding(ready=False)
    binding.context = binding.facade._session_runtime.request_context()

    binding.session_loading.load(_values())

    assert len(binding.background.calls) == 1
    binding.background.calls[0][0]()
    binding.service.scheduler.deactivate.assert_called_once_with("session-a", "view")
    binding.service.scheduler.activate.assert_not_called()
    binding.service.ensure_recovered.assert_not_called()
    binding.service.save_history.assert_not_called()


@pytest.mark.parametrize("entry", ["wake", "_next", "set_active", "select_next"])
@pytest.mark.parametrize("reason", ["preview", "workspace"])
def test_automatic_entry_points_do_not_schedule_in_preview_or_wrong_workspace(monkeypatch, entry, reason):
    binding, _access = _binding(ready=reason != "workspace")
    binding.context = binding.facade._session_runtime.request_context()
    if reason == "preview":
        binding.session_loading._preview_state = {}
    timer = Mock()
    monkeypatch.setattr(request_binding.QTimer, "singleShot", timer)
    if entry == "select_next":
        request_turn_selection.select_next(binding)
    elif entry == "set_active":
        RequestBinding.set_active(binding, True)
    else:
        getattr(RequestBinding, entry)(binding)
    timer.assert_not_called()
    assert binding.background.calls == []
    assert binding.service.mock_calls == []


@pytest.mark.parametrize("command", ["resume", "restart", "retry_context", "reassign", "reconcile"])
def test_request_execution_controls_wait_for_switch_confirmation(command):
    binding, access = _binding(ready=False)
    binding.context = binding.facade._session_runtime.request_context()
    continuations = []

    def ensure(_action, resume):
        if access.ready:
            return True
        continuations.append(resume)
        return False

    binding.facade._navigation["ensure_session_scope"] = ensure
    binding.control("request-b", command)
    assert len(continuations) == 1
    assert binding.background.calls == []
    binding.interrupt.assert_not_called()
    binding.management.retry_context.assert_not_called()

    access.ready = True
    continuations[0]()
    if command == "retry_context":
        binding.management.retry_context.assert_called_once_with("request-b")
    else:
        assert len(binding.background.calls) == 1


@pytest.mark.parametrize("command", ["pause", "cancel"])
def test_pause_and_cancel_remain_available_without_switching(command):
    binding, _access = _binding(ready=False)
    binding.context = binding.facade._session_runtime.request_context()
    ensure = Mock(return_value=False)
    binding.facade._navigation["ensure_session_scope"] = ensure

    binding.control("request-b", command)

    ensure.assert_not_called()
    assert len(binding.background.calls) == 1


def test_queued_control_rechecks_workspace_before_mutation():
    binding, access = _binding()
    binding.context = binding.facade._session_runtime.request_context()
    binding.control("request-b", "restart")
    access.ready = False

    with pytest.raises(ValueError, match="工程已切换"):
        binding.background.calls[0][0]()

    assert binding.service.mock_calls == []


@pytest.mark.parametrize("stale", ["workspace", "input_generation"])
def test_queued_restore_does_not_activate_or_save_after_scope_or_generation_changes(stale):
    binding, access = _binding()
    binding.session_loading.load(_values(preview=False))
    assert len(binding.background.calls) == 1
    if stale == "workspace":
        access.ready = False
    else:
        binding.inputs.invalidate()

    work, received, _options = binding.background.calls[0]
    result = work()
    received(result)

    assert result is None
    assert binding.service.mock_calls == []
    binding.facade._conversation.merge_saved_records.assert_not_called()


def test_queued_activation_rechecks_workspace_before_scheduler_activation():
    binding, access = _binding()
    binding.context = binding.facade._session_runtime.request_context()
    RequestBinding.set_active(binding, True)
    access.ready = False
    binding.background.calls[0][0]()
    binding.service.scheduler.activate.assert_not_called()


def test_queued_turn_selection_rechecks_scope_and_releases_lease_before_model_dispatch(monkeypatch):
    binding, access = _binding()
    binding.context = binding.facade._session_runtime.request_context()
    monkeypatch.setattr(request_turn_selection, "build_native_tool_definitions", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(request_turn_selection, "configuration_digest", lambda _cfg: "configuration")
    batch = object()
    admission = object()
    binding.service.prepare_batch.return_value = batch
    binding.service.scheduler.acquire_routing.return_value = admission
    request_turn_selection.select_next(binding)
    work, received, _options = binding.background.calls[0]
    result = work()
    access.ready = False

    received(result)

    assert binding.admission is None
    binding.facade._controller.handle_user_message.assert_not_called()
    assert len(binding.background.calls) == 2
    binding.background.calls[1][0]()
    binding.service.scheduler.release.assert_called_once_with(admission)


def test_queued_turn_selection_does_not_read_or_mutate_after_workspace_switch(monkeypatch):
    binding, access = _binding()
    binding.context = binding.facade._session_runtime.request_context()
    monkeypatch.setattr(request_turn_selection, "build_native_tool_definitions", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(request_turn_selection, "configuration_digest", lambda _cfg: "configuration")
    request_turn_selection.select_next(binding)
    access.ready = False

    assert binding.background.calls[0][0]() == (None, None)
    assert binding.service.mock_calls == []


def test_context_preparation_delivery_rejects_wrong_workspace_before_inspecting_authority():
    binding, _access = _binding(ready=False)
    preparation = object.__new__(RequestContextPreparation)
    preparation.binding = binding

    assert not preparation._still_admitted(object(), binding.context)
    binding.service.scheduler.validate.assert_not_called()
    binding.service.state.assert_not_called()


def test_completed_context_material_is_not_delivered_after_workspace_change():
    binding, access = _binding()
    binding.context = binding.facade._session_runtime.request_context()
    binding.admission = SimpleNamespace(stage="execution")
    binding.prepare_model_input = Mock()
    preparation = object.__new__(RequestContextPreparation)
    preparation.binding = binding
    preparation._cancelled = Event()
    preparation._submit = Mock()
    on_ready, on_error = Mock(), Mock()
    preparation.prepare([], 1000, context_window=10000, on_ready=on_ready, on_error=on_error)
    completion = preparation._submit.call_args.args[1]
    access.ready = False

    completion(Mock())

    binding.prepare_model_input.assert_not_called()
    on_ready.assert_not_called()
    on_error.assert_not_called()
