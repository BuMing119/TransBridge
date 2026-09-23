"""UI projects application receipts; cancellation cannot invent competing results."""

from concurrent.futures import Future
import json
from types import SimpleNamespace

import pytest

from tests.application.assistant_context.test_history_queries import _request
from tests.application.assistant_requests.test_request_repository import _snapshot
from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.ui.tools.smart_assistant import request_control_binding
from transbridge.ui.tools.smart_assistant.request_background import RequestBackground

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def make_binding(composed, monkeypatch, name="read_request_state", arguments=None):
    _, service, context = composed
    request_id = _request(service, context)
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.select_next_turn(
        context.session_id, "view", service.requests(service.state(context)), allowed_tools=(name,)
    ).admission
    conversation = ConversationManager()
    conversation.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("query", name, {}),)))
    jobs, deliveries, resumes, starts, failures = [], [], [], [], []

    def submit(work):
        future = Future()
        jobs.append((work, future))
        return future

    monkeypatch.setattr(request_control_binding.QTimer, "singleShot", lambda _, callback: resumes.append(callback))
    binding = SimpleNamespace(
        service=service,
        context=context,
        admission=admission,
        _closed=False,
        _active=True,
        _accepting=0,
        _queue=SimpleNamespace(submit=submit),
        delivered=SimpleNamespace(emit=deliveries.append),
        fail=failures.append,
        facade=SimpleNamespace(
            _conversation=conversation, _orchestrator=SimpleNamespace(start_round=lambda: starts.append(True))
        ),
        wake=lambda: None,
    )
    binding.background = RequestBackground(binding)
    binding.control_results = request_control_binding.RequestControlBinding(binding)
    parsed = {
        "control": name,
        "tool_call_id": "query",
        "arguments": arguments
        or {
            "section": "items",
            "offset": 0,
            "limit": 2000,
            "expected_digest": "",
        },
    }
    return SimpleNamespace(
        binding=binding,
        parsed=parsed,
        jobs=jobs,
        deliveries=deliveries,
        resumes=resumes,
        starts=starts,
        failures=failures,
        request_id=request_id,
    )


def complete_work(case):
    work, future = case.jobs.pop(0)
    try:
        future.set_result(work())
    except Exception as exc:
        future.set_exception(exc)


def test_success_is_persisted_before_gui_receives_it(composed, monkeypatch):
    case = make_binding(composed, monkeypatch)
    case.binding.control_results.start(case.parsed)
    complete_work(case)
    snapshot = _snapshot(composed[0], composed[2])
    saved = next(m for m in snapshot.backend_messages() if m["role"] == "tool")
    assert not any(m["role"] == "tool" for m in case.binding.facade._conversation.get_transcript())
    case.deliveries.pop(0)()
    assert case.binding.facade._conversation.get_transcript()[-1] == saved
    case.resumes.pop()()
    assert case.starts == [True] and not case.failures


@pytest.mark.parametrize("when", ["before_read", "after_commit"])
def test_cancel_retains_exactly_one_durable_receipt_and_never_resumes(composed, monkeypatch, when):
    case = make_binding(composed, monkeypatch)
    case.binding.control_results.start(case.parsed)
    if when == "after_commit":
        complete_work(case)
    case.binding.control_results.cancel()
    assert case.binding.facade._conversation.close_pending_tool_calls() == 0
    case.binding.admission = None
    if when == "before_read":
        complete_work(case)
    case.deliveries.pop(0)()
    saved = [m for m in _snapshot(composed[0], composed[2]).backend_messages() if m["role"] == "tool"]
    assert len(saved) == 1
    assert saved[0]["is_error"] == (when == "before_read")
    assert case.binding.facade._conversation.get_transcript()[-1] == saved[0]
    assert not case.resumes and not case.starts and not case.failures


def test_storage_failure_is_exposed_without_synthetic_success_or_retry(composed, monkeypatch):
    case = make_binding(composed, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(case.binding.service, "save_history", fail)
    case.binding.control_results.start(case.parsed)
    complete_work(case)
    case.deliveries.pop(0)()
    assert len(case.failures) == 1 and "disk unavailable" in case.failures[0]
    assert not case.jobs and not case.resumes
    assert case.binding.facade._conversation.close_pending_tool_calls() == 0


def test_changed_state_before_worker_read_is_returned_as_current_page(composed, monkeypatch):
    case = make_binding(composed, monkeypatch)
    case.binding.control_results.start(case.parsed)
    case.binding.service.transact(composed[2], lambda state: state["requests"][0]["items"][0].update(description="new"))
    complete_work(case)
    case.deliveries.pop(0)()
    content = json.loads(case.binding.facade._conversation.get_transcript()[-1]["content"])
    assert "new" in content["text"]
