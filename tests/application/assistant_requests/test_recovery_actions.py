"""Recovery UI commands tested against actual storage without creating Qt windows."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox
import pytest
import test_request_repository as repository_tests
from test_request_repository import _proposal

from tests.routing_fixtures import apply_routing_fixture
from transbridge.application.assistant_requests.models import AssistantExecutionRef, EffectIntent
from transbridge.ui.tools.smart_assistant.request_management_binding import RequestManagementBinding

composed = repository_tests.composed


def _case(composed):
    _, service, context = composed
    service.accept_input(context, "翻译原范围", selection={"selected_entry_keys": ["original"]})
    batch = service.prepare_batch(context)
    request = service.requests(apply_routing_fixture(service, context, batch.batch_id, _proposal(batch)))[0]
    jobs, messages = [], []
    binding = SimpleNamespace(
        service=service,
        context=context,
        _closed=False,
        _user_stopped=True,
        facade=None,
        _selection=lambda: {"selected_entry_keys": ["now-different"]},
        background=SimpleNamespace(submit=lambda work, received=None, **kw: jobs.append((work, received, kw))),
        refresh=lambda: messages.append("refresh"),
    )
    management = RequestManagementBinding(binding)
    management.sync_inputs = lambda: messages.append("sync")
    return SimpleNamespace(
        service=service,
        context=context,
        request=request,
        jobs=jobs,
        binding=binding,
        management=management,
        messages=messages,
    )


def _finish(case):
    work, received, kwargs = case.jobs.pop(0)
    result = work()
    if received:
        received(result)
    if kwargs.get("settled"):
        kwargs["settled"]()


def test_context_retry_clears_only_context_wait_and_coalesces_duplicate_clicks(composed):
    case = _case(composed)
    paused = case.service.command(case.context, case.request.request_id, "pause", 1)
    case.service.transact(
        case.context, lambda state: state.update(context_waits={paused.request_id: {"message": "摘要文件损坏"}})
    )
    case.management.retry_context(paused.request_id)
    case.management.retry_context(paused.request_id)
    assert len(case.jobs) == 1
    _finish(case)
    state = case.service.state(case.context)
    assert state["context_waits"] == {}
    assert case.service.requests(state)[0].pause_reasons == paused.pause_reasons
    assert case.messages == ["refresh"]


def test_restart_explains_full_redo_preserves_selection_and_is_idempotent(composed, monkeypatch):
    case = _case(composed)
    cancelled = case.service.command(case.context, case.request.request_id, "cancel", 1)
    confirmations = []

    def confirm(_parent, _title, text, *_args):
        confirmations.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    case.management.restart(cancelled)
    case.management.restart(cancelled)
    assert len(case.jobs) == 1 and len(confirmations) == 1
    assert "包括之前已完成" in confirmations[0] and "可能再次产生修改" in confirmations[0]
    _finish(case)
    state = case.service.state(case.context)
    assert len(state["requests"]) == 2
    successor = case.service.requests(state)[-1]
    assert successor.related_to == cancelled.request_id
    assert state["ingress"][-1]["selection"] == {"selected_entry_keys": ["original"]}
    assert successor.status == "open" and not successor.effects
    case.management.restart(cancelled)
    _finish(case)
    assert len(case.service.state(case.context)["requests"]) == 2


@pytest.mark.parametrize("state", ["open", "stopping", "completed"])
def test_stale_terminal_button_cannot_restart_other_states(composed, monkeypatch, state):
    case = _case(composed)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: pytest.fail("must not offer restart"))
    case.management.restart(replace(case.request, status=state))
    assert not case.jobs


def test_unknown_effect_blocks_restart_until_reconciled(composed, monkeypatch):
    case = _case(composed)
    execution = AssistantExecutionRef(case.request.request_id, 1, (case.request.items[0].item_id,), "a", "d", "t")
    unknown = replace(
        case.request, status="failed", effects=(EffectIntent("effect", "hash", execution, 1, status="outcome_unknown"),)
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: pytest.fail("must reconcile first"))
    case.management.restart(unknown)
    assert not case.jobs


def test_legacy_terminal_continue_keeps_explicit_successor_contract(composed, monkeypatch):
    case = _case(composed)
    completed = replace(case.request, status="completed")
    case.service.update_request(case.context, completed.request_id, lambda _: completed)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: pytest.fail("legacy command already expresses intent"))
    case.management.restart(completed, confirm=False)
    _finish(case)
    requests = case.service.requests(case.service.state(case.context))
    assert requests[0].status == "completed"
    assert requests[1].related_to == requests[0].request_id and requests[1].status == "open"
