"""History retrieval remains material and cannot resume a stale UI turn."""

from concurrent.futures import Future
import json
from types import SimpleNamespace

import pytest

from tests.application.assistant_context.test_history_queries import _request
from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.request_protocol import HISTORY_RETRIEVAL_TOOL
from transbridge.ui.tools.smart_assistant import request_history_binding

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _binding(composed, monkeypatch):
    _, service, context = composed
    request_id = _request(service, context)
    service.scheduler.activate(context.session_id, "view")
    admission = service.scheduler.select_next_turn(
        context.session_id, "view", service.requests(service.state(context)), allowed_tools=(HISTORY_RETRIEVAL_TOOL,)
    ).admission
    conversation = ConversationManager()
    conversation.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("query", HISTORY_RETRIEVAL_TOOL, {}),)))
    jobs, deliveries, resumes, failures = [], [], [], []

    def submit(work):
        future = Future()
        jobs.append((work, future))
        return future

    monkeypatch.setattr(request_history_binding.QTimer, "singleShot", lambda _, callback: resumes.append(callback))
    starts = []
    binding = SimpleNamespace(
        service=service,
        context=context,
        admission=admission,
        _closed=False,
        _active=True,
        _queue=SimpleNamespace(submit=submit),
        delivered=SimpleNamespace(emit=deliveries.append),
        fail=failures.append,
        facade=SimpleNamespace(
            _conversation=conversation, _orchestrator=SimpleNamespace(start_round=lambda: starts.append(True))
        ),
    )
    parsed = {"arguments": {"message_id": "input", "offset": 0, "limit": 2000}, "tool_call_id": "query"}
    return binding, parsed, jobs, deliveries, resumes, starts, failures, request_id


def test_query_appends_material_tool_receipt_without_new_request_or_evidence(composed, monkeypatch):
    binding, parsed, jobs, deliveries, resumes, starts, failures, _ = _binding(composed, monkeypatch)
    before = binding.service.state(binding.context)
    request_history_binding.retrieve_history(binding, parsed)
    assert not deliveries and len(jobs) == 1
    work, future = jobs.pop()
    future.set_result(work())
    deliveries.pop()()
    assert not failures
    receipt = json.loads(binding.facade._conversation.get_transcript()[-1]["content"])
    assert receipt["material_only"] and "取消任务" in receipt["text"]
    after = binding.service.state(binding.context)
    assert before["requests"] == after["requests"] and before["ingress"] == after["ingress"]
    resumes.pop()()
    assert starts == [True]


@pytest.mark.parametrize("change", ["close", "replace", "reassign"])
def test_completed_background_read_cannot_deliver_to_changed_request(composed, monkeypatch, change):
    binding, parsed, jobs, deliveries, resumes, _, failures, _ = _binding(composed, monkeypatch)
    request_history_binding.retrieve_history(binding, parsed)
    work, future = jobs.pop()
    result = work()
    if change == "close":
        binding._closed = True
    elif change == "replace":
        binding.admission = None
    else:
        binding.service.transact(
            binding.context, lambda state: state.setdefault("message_owners", {}).update(input="other-request")
        )
    future.set_result(result)
    deliveries.pop()()
    assert not resumes
    assert bool(failures) == (change == "reassign")
    assert all(
        "material_only" not in str(record["content"]) for record in binding.facade._conversation.get_transcript()
    )
