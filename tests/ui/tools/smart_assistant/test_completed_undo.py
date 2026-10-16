"""Completed tasks expose the same explicit, revalidated undo boundary."""

from tests.ui.tools.smart_assistant.test_request_undo_binding import _run, _setup


def _completed():
    case = _setup()
    case.request.terminal = True
    case.request.pause_reasons = ()
    case.request.session_id = case.binding.context.session_id
    case.request.goal = "统一术语"
    case.binding.service.state = lambda _: {}
    case.binding.service.requests = lambda _: [case.request]
    return case


def test_completed_task_preview_is_optional_and_explicit_click_revalidates():
    case = _completed()
    case.adapter.prepare("request")
    _run(case)
    _run(case)
    assert not case.mutations
    assert "统一术语" in case.messages[-1][0] and case.messages[-1][1]
    case.adapter.request("request")
    _run(case)
    _run(case)
    assert len(case.previews) == 2
    _run(case)
    assert case.mutations == [(case.binding.context, "round", False)]


def test_repeated_selection_of_same_request_fences_old_read_callback():
    case = _completed()
    case.adapter.request("request")
    case.adapter.prepare("request")
    _run(case)  # Old explicit-click callback must not start its inverse.
    _run(case)
    _run(case)
    assert not case.mutations and not case.jobs


def test_switch_before_completed_task_inverse_prevents_mutation():
    case = _completed()
    case.adapter.request("request")
    _run(case)
    _run(case)
    case.adapter.invalidate()
    case.binding.context = "another-session"
    _run(case)
    assert not case.mutations
