"""Every mutating recovery or confirmation intent waits for the owning workspace."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import sip
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.ui.tools.smart_assistant.test_confirmation_lifecycle import _view
from tests.ui.tools.smart_assistant.test_request_undo_binding import _run, _setup
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.contracts import RequestContext
from transbridge.ui.tools.smart_assistant.request_confirmation_binding import RequestConfirmationBinding
from transbridge.ui.tools.smart_assistant.request_management_binding import RequestManagementBinding
from transbridge.ui.tools.smart_assistant.routing_recovery_binding import RoutingRecoveryBinding


def _access(facade, *, ready=False):
    state = SimpleNamespace(ready=ready, continuations=[], attempts=[])

    def ensure(action, resume):
        state.attempts.append(action)
        if state.ready:
            return True
        state.continuations.append(resume)
        return False

    facade._navigation = {"ensure_session_scope": ensure, "session_scope_ready": lambda: state.ready}
    return state


def _management(*, ready=False):
    jobs = []
    facade = SimpleNamespace(add_system_message=Mock())
    access = _access(facade, ready=ready)
    context = RequestContext("owner", session_id="session-b", project_id="b", variant_id="b-version")
    request = SimpleNamespace(
        request_id="request",
        revision=1,
        terminal=False,
        pause_reasons=("user_paused",),
        unsettled=False,
        status="failed",
        goal="翻译 B",
        items=[SimpleNamespace(item_id="item", description="检查术语")],
    )
    target = SimpleNamespace(request_id="target", revision=2, terminal=False, goal="继续校对 B")
    service = Mock()
    service.requests.return_value = [request, target]
    binding = SimpleNamespace(
        facade=facade,
        context=context,
        _closed=False,
        _active=True,
        _user_stopped=True,
        admission=None,
        service=service,
        _selection=Mock(side_effect=lambda: {"project_id": "b" if access.ready else "a"}),
        background=SimpleNamespace(submit=lambda work, received=None, **kwargs: jobs.append((work, received, kwargs))),
        interrupt=Mock(),
        control=Mock(),
        fail=Mock(),
        refresh=Mock(),
    )
    management = RequestManagementBinding(binding)
    management._failed_turn = (context, request.request_id)
    management.sync_inputs = Mock()
    binding.management = management
    return SimpleNamespace(binding=binding, access=access, request=request, jobs=jobs, management=management)


def _invoke(case, entry):
    if entry == "restart":
        case.management.restart(case.request, confirm=False)
    elif entry in {"retry_context", "reassign"}:
        getattr(case.management, entry)(case.request.request_id)
    else:
        getattr(case.management, entry)()


@pytest.mark.parametrize(
    "entry", ["retry_failed_turn", "retry_inputs", "restart", "retry_context", "clarify", "reassign"]
)
def test_recovery_intents_wait_without_capturing_the_wrong_selection(monkeypatch, entry):
    case = _management()
    case.request.terminal = entry == "restart"
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.request_management_binding.QInputDialog.getItem",
        lambda _parent, _title, _label, choices, **_kwargs: (choices[0], True),
    )
    _invoke(case, entry)
    assert len(case.access.continuations) == 1
    assert case.jobs == []
    assert case.binding._user_stopped
    assert case.management._failed_turn is not None
    case.binding._selection.assert_not_called()
    case.binding.interrupt.assert_not_called()
    case.binding.service.state.assert_not_called()

    case.access.ready = True
    case.access.continuations[0]()
    if entry == "retry_failed_turn":
        case.binding.control.assert_called_once_with("request", "resume")
    elif entry == "reassign":
        assert case.binding.service.reassign.call_args.kwargs["selection"] == {"project_id": "b"}
    else:
        assert len(case.jobs) == 1
        if entry == "restart":
            case.jobs[0][0]()
            assert case.binding.service.restart.call_args.kwargs["selection"] == {"project_id": "b"}


@pytest.mark.parametrize("entry", ["retry_inputs", "restart", "retry_context"])
def test_queued_recovery_rechecks_scope_before_persisting(monkeypatch, entry):
    case = _management(ready=True)
    case.request.terminal = entry == "restart"
    _invoke(case, entry)
    case.access.ready = False
    with pytest.raises(RequestError, match="工程已切换"):
        case.jobs[0][0]()
    case.binding.service.transact.assert_not_called()
    case.binding.service.restart.assert_not_called()


def test_retry_failed_turn_requests_workspace_switch_even_when_view_is_inactive():
    case = _management()
    case.binding._active = False
    failed_turn = case.management._failed_turn
    case.management.retry_failed_turn()
    assert len(case.access.continuations) == 1
    assert case.management._failed_turn == failed_turn
    assert case.binding.service.mock_calls == []
    case.binding.control.assert_not_called()
    case.access.ready = True
    case.access.continuations[0]()
    assert case.management._failed_turn == failed_turn
    case.binding.control.assert_not_called()
    case.binding._active = True
    case.access.continuations[0]()
    case.binding.control.assert_called_once_with("request", "resume")


def test_reassign_does_not_capture_selection_after_workspace_changes_in_dialog(monkeypatch):
    case = _management(ready=True)

    def choose(_parent, _title, _label, choices, **_kwargs):
        case.access.ready = False
        return choices[0], True

    monkeypatch.setattr("transbridge.ui.tools.smart_assistant.request_management_binding.QInputDialog.getItem", choose)
    case.management.reassign("request")
    case.binding._selection.assert_not_called()
    case.binding.interrupt.assert_not_called()
    case.binding.service.reassign.assert_not_called()


def test_restart_does_not_capture_selection_after_workspace_changes_in_confirmation(monkeypatch):
    case = _management(ready=True)

    def confirm(*_args):
        case.access.ready = False
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("transbridge.ui.tools.smart_assistant.request_management_binding.QMessageBox.question", confirm)
    case.management.restart(case.request)
    case.binding._selection.assert_not_called()
    assert case.jobs == []
    assert case.binding._user_stopped


def test_routing_recovery_blocks_stale_dialogs_and_queued_mutations(monkeypatch):
    case = _management()
    recovery = RoutingRecoveryBinding(case.binding)
    recovery.show()
    assert case.jobs == []
    assert not recovery._busy
    case.access.ready = True
    case.access.continuations[0]()
    assert len(case.jobs) == 1
    case.access.ready = False
    choose = Mock(side_effect=AssertionError("a stale recovery must not open a dialog"))
    monkeypatch.setattr("transbridge.ui.tools.smart_assistant.routing_recovery_binding.QInputDialog.getItem", choose)
    case.jobs[0][1]({})
    choose.assert_not_called()
    case.access.ready = True
    work = Mock()
    recovery._submit(case.binding.context, work)
    case.access.ready = False
    with pytest.raises(RequestError, match="工程已切换"):
        case.jobs[-1][0]()
    work.assert_not_called()


@pytest.mark.parametrize("kind", ["tool", "batch", "plan"])
def test_confirmation_switch_cancellation_preserves_proposal_and_resume_executes_once(kind):
    view, parent, intents, _cards = _view()
    access = _access(parent)
    validated = Mock(return_value=True)
    validated.defer = None
    view.intent_scope = lambda **_kwargs: validated
    step = {"id": 1, "tool": "edit_translation", "args": {"entry_id": "b-entry"}}
    factory = getattr(view, f"add_{'batch_tool' if kind == 'batch' else kind}_card")
    card = factory(step if kind == "tool" else [step])
    try:
        card._exec_btn.click()
        assert intents == []
        validated.assert_not_called()
        assert card in view._pending_cards
        assert card._exec_btn.isEnabled()
        assert card._pending
        assert "执行中" not in card._exec_btn.text()
        access.ready = True
        access.continuations[0]()
        access.continuations[0]()
        assert intents == [(kind, step if kind == "tool" else [step])]
        assert card not in view._pending_cards
        assert card in view._active_cards
        assert not card._exec_btn.isEnabled()
        assert card._exec_btn.text() == "执行中..."
        validated.assert_called_once()
    finally:
        view.close()
        parent.close()


@pytest.mark.parametrize("kind", ["tool", "batch", "plan"])
@pytest.mark.parametrize("change", ["invalidate", "destroy"])
def test_switch_does_not_restore_expired_or_destroyed_confirmation(kind, change):
    view, parent, intents, _cards = _view()
    access = _access(parent)
    step = {"id": 1, "tool": "edit_translation"}
    factory = getattr(view, f"add_{'batch_tool' if kind == 'batch' else kind}_card")
    card = factory(step if kind == "tool" else [step])

    def ensure(_action, resume):
        access.continuations.append(resume)
        if change == "destroy":
            sip.delete(card)
        else:
            view.invalidate_pending()
        return False

    parent._navigation["ensure_session_scope"] = ensure
    try:
        card._exec_btn.click()
        access.continuations[0]()
        assert intents == []
        if change == "invalidate":
            card.restore_pending()
            assert card._exec_btn.text() == "已失效"
            assert not card._exec_btn.isEnabled()
    finally:
        view.close()
        parent.close()


@pytest.mark.parametrize("kind", ["tool", "batch", "plan"])
def test_ignoring_confirmation_does_not_require_workspace_switch(kind):
    view, parent, intents, _cards = _view()
    access = _access(parent)
    step = {"id": 1, "tool": "edit_translation"}
    factory = getattr(view, f"add_{'batch_tool' if kind == 'batch' else kind}_card")
    card = factory(step if kind == "tool" else [step])
    button = card._ignore_btn if kind == "tool" else card._skip_btn if kind == "batch" else card._cancel_btn
    try:
        button.click()
        assert len(intents) == 1
        assert access.attempts == []
    finally:
        view.close()
        parent.close()


@pytest.mark.parametrize("accepted", [True, False])
def test_confirmation_validator_checks_workspace_before_acquiring_or_consuming_approval(accepted):
    facade = SimpleNamespace(_controller=Mock(pending_confirmation_id="confirmation"))
    _access(facade)
    service = Mock()
    service.state.return_value = {}
    binding = SimpleNamespace(
        facade=facade,
        service=service,
        admission=SimpleNamespace(session_id="session", request_id="request", turn_id="turn"),
        context=RequestContext("owner", session_id="session"),
        _closed=False,
        _active=True,
    )
    confirmations = RequestConfirmationBinding(binding)
    confirmations._request = Mock()
    assert not confirmations.validator(accepted=accepted)()
    if accepted:
        service.state.assert_not_called()
    else:
        service.state.assert_called_once_with(binding.context)
    service.transact.assert_not_called()
    service.scheduler.select_next_turn.assert_not_called()


def test_undo_requires_switch_before_preview_and_rechecks_before_applying():
    case = _setup()
    access = _access(case.binding.facade, ready=True)
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    access.ready = False
    case.adapter.request()
    assert not case.jobs
    assert not case.mutations
    access.ready = True
    access.continuations[0]()
    assert len(case.jobs) == 1
    access.ready = False
    _run(case)
    assert not case.jobs
    assert not case.mutations


def test_queued_undo_cannot_apply_after_another_workspace_is_opened():
    case = _setup()
    access = _access(case.binding.facade, ready=True)
    case.adapter.stopped("request")
    case.adapter.observe([case.request])
    _run(case)
    case.adapter.request()
    _run(case)
    access.ready = False
    _run(case)
    assert not case.mutations
    assert "已取消" in case.messages[-1][0]
