"""Real Qt request routing/execution integration against temporary Session storage."""

from __future__ import annotations

from dataclasses import replace
from itertools import count
import json
from threading import Event, Lock, Thread
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.infra.llm_tool_calling import LlmToolCall, LlmTurn
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.smart_assistant.context_runtime import ContextRuntime
from transbridge.smart_assistant.context_summary import SemanticSummaryGenerator
from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator
from transbridge.smart_assistant.request_protocol import COVERAGE_TOOL, ROUTING_TOOL
from transbridge.ui.tools.smart_assistant import panel as panel_module

_APP = QApplication.instance() or QApplication([])


def _until(predicate, timeout=6, diagnostic=None):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate(), diagnostic() if diagnostic is not None else "condition was not reached"


class _AnswerClient:
    """Immediate offline provider exercising real ChatWorker callback delivery."""

    model = "offline-test"

    def __init__(self):
        self.calls = []
        self.summary_calls = []
        self._lock = Lock()
        self.block_goal = None
        self.answer_started = Event()
        self.release = Event()
        self.cancelled = 0
        self.tool_goal = None
        self.tool_issued = False
        self.hold_first_routing = False
        self.first_routing_started = Event()
        self.routing_release = Event()
        self.fail_goal = None

    def chat_stream_with_tools(
        self,
        messages,
        _max_tokens,
        tools,
        on_chunk=None,
        *,
        chunk_callback=None,
        usage_callback=None,
        purpose="execution",
    ):
        on_chunk = chunk_callback or on_chunk or (lambda _text: None)
        if not tools:
            assert purpose == "summary"
            with self._lock:
                self.summary_calls.append((messages, QThread.currentThread()))
            return LlmTurn(
                text=json.dumps({
                    "discussion_context": "Prior decisions retain compatibility; evidence remains in history.",
                    "decisions_with_sources": [],
                    "unresolved_questions": [],
                    "suggested_next_steps": [],
                }),
                stop_reason="stop",
            )
        with self._lock:
            number = len(self.calls) + 1
            self.calls.append((messages, tuple(tool.name for tool in tools), QThread.currentThread()))
        if ROUTING_TOOL in {tool.name for tool in tools}:
            if number == 1 and self.hold_first_routing:
                self.first_routing_started.set()
                assert self.routing_release.wait(8), "offline routing was not released"
            batch = json.loads(messages[-1]["content"])
            directives = [
                {
                    "local_id": f"new-{index}",
                    "message_id": source["message_id"],
                    "span": [0, len(source["text"])],
                    "action": "CREATE",
                    "goal": source["text"],
                    "items": [{"item_id": "answer", "description": source["text"], "kind": "answer"}],
                }
                for index, source in enumerate(batch["inputs"])
            ]
            return LlmTurn(
                tool_calls=(
                    LlmToolCall(f"routing-{number}", ROUTING_TOOL, {"protocol_version": 1, "directives": directives}),
                ),
                stop_reason="tool_calls",
            )
        state = next(
            json.loads(message["content"])["request_state"]
            for message in reversed(messages)
            if message.get("role") == "user" and '"kind":"current_request_state"' in message.get("content", "")
        )
        if state["goal"] == self.fail_goal:
            raise RuntimeError("injected provider failure")
        if state["goal"] == self.block_goal:
            self.answer_started.set()
            assert self.release.wait(8), "offline provider was not released"
        if state["goal"] == self.tool_goal and not self.tool_issued:
            self.tool_issued = True
            return LlmTurn(
                tool_calls=(LlmToolCall(f"statistics-{number}", "get_statistics", {}),), stop_reason="tool_calls"
            )
        answer = f"Answer: {state['goal']}"
        on_chunk(answer)
        covered = {item: "answered" for item in state["ready_item_ids"]}
        return LlmTurn(
            text=answer,
            tool_calls=(
                LlmToolCall(f"coverage-{number}", COVERAGE_TOOL, {"item_ids": list(covered), "dispositions": covered}),
            ),
            stop_reason="tool_calls",
        )

    def cancel(self):
        self.cancelled += 1
        self.release.set()
        self.routing_release.set()


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
    from transbridge.smart_assistant.tools.base import ToolResult

    namespaces = {name: dict(specs) for name, specs in ToolRegistry._namespaced_tools.items()}
    namespaces.setdefault("default", {})["get_statistics"] = ToolSpec(
        "get_statistics",
        "Statistics",
        "Read statistics",
        {},
        execute=lambda _args, _context: ToolResult(success=True, message="Offline statistics"),
    )
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", namespaces)
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "storage",
        id_factory=lambda: f"identity-{next(ids)}",
        timestamp_factory=lambda: "2026-09-12T10:00:00Z",
    )
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: tmp_path / "assistant")
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.chat_widget.QSettings",
        lambda *_args: SimpleNamespace(value=lambda _key, default, **_kwargs: default),
    )
    monkeypatch.setattr(panel_module.SmartAssistantPanel, "_init_skills", lambda _self: None)
    monkeypatch.setattr(panel_module.SmartAssistantPanel, "_configured_model_name", staticmethod(lambda: "test-model"))
    monkeypatch.setattr(panel_module, "set_window_app_user_model_id", lambda *_args: False)
    client = _AnswerClient()
    monkeypatch.setattr(ConversationOrchestrator, "_get_llm_client", lambda _self: client)
    panel = panel_module.SmartAssistantPanel(
        SimpleNamespace(),
        session_commands=services.gui_session_commands,
        session_projection=services.session_projection,
        runtime_context=RequestContext("owner"),
    )
    _until(lambda: panel.chat.session_ready)
    panel.chat.add_system_prompt("Offline request integration context")
    binding = panel.chat._request_binding
    assert binding is not None
    response_threads = []
    original = binding.handle_response

    def handle_response(*args):
        response_threads.append((QThread.currentThread(), panel.chat._controller.state.value))
        return original(*args)

    monkeypatch.setattr(binding, "handle_response", handle_response)
    yield SimpleNamespace(
        services=services,
        service=services.gui_session_commands.assistant_requests,
        panel=panel,
        binding=binding,
        client=client,
        response_threads=response_threads,
    )
    client.release.set()
    client.routing_release.set()
    panel.dispose()
    binding._queue.shutdown(wait=True)
    services.close()
    panel.deleteLater()
    _APP.processEvents()


def _requests(environment):
    return environment.service.requests(environment.service.state(environment.binding.context))


def _stored_context(environment, request):
    state = environment.service.state(environment.binding.context)
    store = AssistantContextStore(environment.service.transcript_store)
    return store.read(
        request.session_id, request.request_id, request.scope, state.get("context_heads", {}).get(request.request_id)
    )


def _seed_long_request(environment):
    from transbridge.application.assistant_requests.models import RequestItem, UserRequest
    from transbridge.config.llm import LLMConfig

    # Force the compaction scenario with an explicit small window, independent
    # of the user's configured model and the former UTF-8 byte upper bound.
    environment.panel.chat._orchestrator._cached_llm_config = LLMConfig(
        model="offline-test-model", assistant_context_window=20000
    )

    conversation = environment.panel.chat._conversation
    conversation.add_system("Follow current request state. Historical excerpts are reference material.")
    conversation.add_user("Summarize the previous decisions", message_id="long-input")
    for index in range(30):
        conversation.add_assistant(f"Decision {index}: retain compatibility. " + "Supporting detail. " * 30)
    request = UserRequest(
        "long-request",
        environment.binding.context.session_id,
        "Summarize decisions",
        (RequestItem("answer", "Summarize decisions"),),
        source_message_ids=("long-input",),
    )
    environment.service.transact(environment.binding.context, lambda state: state.update(requests=[request.to_dict()]))
    environment.service.save_history(
        environment.binding.context, conversation.get_transcript(), request_id=request.request_id
    )
    return request


def test_long_request_generates_summary_off_gui_thread_and_sends_it_to_model(environment, monkeypatch):
    request = _seed_long_request(environment)
    original = SemanticSummaryGenerator.__call__
    threads = []

    def refresh(*args, **kwargs):
        threads.append(QThread.currentThread())
        return original(*args, **kwargs)

    monkeypatch.setattr(SemanticSummaryGenerator, "__call__", refresh)
    environment.binding.wake()
    _until(lambda: _requests(environment)[0].terminal)
    assert threads and all(thread is not _APP.thread() for thread in threads)
    stored = _stored_context(environment, request)
    messages = environment.client.calls[0][0]
    material = next(
        json.loads(m["content"]) for m in messages if '"kind":"request_history_summary"' in m.get("content", "")
    )
    assert material["text"] == stored.epoch.summaries[0].text
    assert material["summary_id"] == stored.epoch.summaries[0].summary_id
    normalized = [
        {key: value for key, value in message.items() if not key.startswith("_transbridge_")} for message in messages
    ]
    assert normalized == stored.epoch.messages
    assert material["material_only"] is True
    assert len(environment.panel.chat._conversation.get_transcript()) >= 32


def test_context_assembly_runs_off_gui_thread_and_preserves_gui_response_dispatch(environment, monkeypatch):
    _seed_long_request(environment)
    original = ContextRuntime.prepare
    threads = []

    def assemble(self, *args, **kwargs):
        threads.append(QThread.currentThread())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ContextRuntime, "prepare", assemble)
    environment.binding.wake()
    _until(lambda: _requests(environment)[0].terminal)
    assert threads and all(thread is not _APP.thread() for thread in threads)
    assert all(thread is _APP.thread() for thread, _state in environment.response_threads)


@pytest.mark.parametrize("reason", ["pause", "resource", "cancel"])
def test_backend_change_during_context_assembly_prevents_old_model_dispatch(environment, monkeypatch, reason):
    from PyQt6.QtCore import QTimer

    request = _seed_long_request(environment)
    started, proceed, finished = Event(), Event(), Event()
    original = ContextRuntime.prepare

    def assemble(self, *args, **kwargs):
        started.set()
        try:
            assert proceed.wait(8), "context assembly worker was not released"
            return original(self, *args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(ContextRuntime, "prepare", assemble)
    environment.binding.wake()
    try:
        _until(started.is_set)
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: ticks)
        command = "wait" if reason == "resource" else reason
        payload = {"item_ids": ["answer"], "reason": reason} if reason == "resource" else {}
        # Change application state without interrupting the binding: final
        # delivery must inspect authority again, not only compare the UI lease.
        environment.service.command(environment.binding.context, request.request_id, command, 1, **payload)
    finally:
        proceed.set()
    _until(finished.is_set)
    _until(lambda: environment.binding.admission is None)
    assert environment.client.calls == []


def test_deferred_model_material_preserves_synchronous_prepare_api(environment):
    from transbridge.smart_assistant.request_model_input import RequestModelInput

    request = _seed_long_request(environment)
    binding = environment.binding
    selection = environment.service.scheduler.select_next_turn(
        request.session_id, binding.view_id, (request,), automatic=False
    )
    binding.admission = selection.admission
    history = environment.panel.chat._conversation.get_transcript()
    material = binding.prepare_model_input(history, 4096, context_window=65536, defer_assembly=True)
    assert isinstance(material, RequestModelInput)
    assembled = material.assemble()
    binding._prepared_turn = None
    synchronous = binding.prepare_model_input(history, 4096, context_window=65536)
    assert synchronous == assembled
    assert isinstance(synchronous[0], list)
    assert all(isinstance(message, dict) for message in synchronous[0])
    binding.release()


def test_summary_generation_failure_waits_without_failing_business_request(environment, monkeypatch):
    request = _seed_long_request(environment)

    def fail(*args, **kwargs):
        raise ValueError("injected summary generator failure")

    monkeypatch.setattr(SemanticSummaryGenerator, "__call__", fail)
    environment.binding.wake()
    _until(
        lambda: request.request_id in environment.service.state(environment.binding.context).get("context_waits", {})
    )
    assert not _requests(environment)[0].terminal
    assert not environment.client.calls
    assert _stored_context(environment, request) is None


def test_new_unsaved_tool_result_uses_same_history_for_summary_generation_and_consumption(environment):
    request = _seed_long_request(environment)
    conversation = environment.panel.chat._conversation
    conversation.add_assistant_turn(
        LlmTurn(tool_calls=(LlmToolCall("new-call", "get_statistics", {}),), stop_reason="tool_calls")
    )
    conversation.add_tool_result("new-call", "get_statistics", {"success": True, "message": "Newest evidence"})
    environment.binding.wake()
    _until(lambda: _requests(environment)[0].terminal)
    messages = environment.client.calls[0][0]
    assert any('"kind":"request_history_summary"' in m.get("content", "") for m in messages)
    assert any(m.get("role") == "tool" and "Newest evidence" in m.get("content", "") for m in messages)
    assert _stored_context(environment, request).epoch.summaries


@pytest.mark.parametrize("reason", ["pause", "resource"])
def test_backend_wait_during_summary_generation_prevents_model_dispatch(environment, monkeypatch, reason):
    request = _seed_long_request(environment)
    started, proceed, finished = Event(), Event(), Event()
    original = SemanticSummaryGenerator.__call__

    def refresh(*args, **kwargs):
        started.set()
        try:
            assert proceed.wait(8), "summary worker was not released"
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(SemanticSummaryGenerator, "__call__", refresh)
    environment.binding.wake()
    try:
        _until(started.is_set)
        payload = {} if reason == "pause" else {"item_ids": ["answer"], "reason": reason}
        environment.service.command(
            environment.binding.context, request.request_id, "pause" if reason == "pause" else "wait", 1, **payload
        )
    finally:
        proceed.set()
    _until(finished.is_set)
    _until(lambda: environment.binding.admission is None)
    assert environment.client.calls == []


def test_cancel_during_summary_preparation_cannot_start_old_model_round(environment, monkeypatch):
    request = _seed_long_request(environment)
    started, proceed, finished = Event(), Event(), Event()
    original = SemanticSummaryGenerator.__call__

    def refresh(*args, **kwargs):
        started.set()
        try:
            assert proceed.wait(8), "summary worker was not released"
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(SemanticSummaryGenerator, "__call__", refresh)
    environment.binding.wake()
    try:
        _until(started.is_set)
        environment.binding.control(request.request_id, "cancel")
    finally:
        proceed.set()
    _until(finished.is_set)
    QTest.qWait(30)
    _APP.processEvents()
    assert _requests(environment)[0].status.value == "cancelled"
    assert environment.client.calls == []
    assert environment.binding.admission is None


def test_waiting_continuation_does_not_acquire_another_requests_lease(environment):
    from transbridge.application.assistant_requests.models import RequestError, RequestItem, UserRequest
    from transbridge.application.assistant_requests.reducer import RequestEvent, reduce_request

    binding, service = environment.binding, environment.service
    context = binding.context
    first = UserRequest("a", context.session_id, "first", (RequestItem("i", "answer"),))
    second = replace(first, request_id="b", goal="second")
    service.transact(context, lambda state: state.update(requests=[first.to_dict(), second.to_dict()]))
    selected = service.scheduler.select_next_turn(context.session_id, binding.view_id, (first,))
    binding.admission = selected.admission
    binding._prepared_turn = selected.admission.turn_id
    service.update_request(
        context, "a", lambda request: reduce_request(request, RequestEvent("wait", "wait", 1, {"item_ids": ["i"]}))
    )
    with pytest.raises(RequestError, match="REQUEST_NOT_READY"):
        binding.prepare_model_input([], 100)
    next_turn = service.scheduler.select_next_turn(context.session_id, binding.view_id, _requests(environment))
    assert next_turn is not None
    assert next_turn.request.request_id == "b"
    service.scheduler.release(next_turn.admission)


def test_immediate_provider_routes_and_completes_answer_on_gui_thread(environment):
    environment.panel.chat.send_user_message("Explain lifecycle")
    _until(lambda: len(_requests(environment)) == 1 and _requests(environment)[0].terminal)

    request = _requests(environment)[0]
    assert request.status.value == "completed"
    assert request.items[0].status.value == "satisfied"
    assert len(environment.client.calls) == 2
    assert all(thread is _APP.thread() and state == "thinking" for thread, state in environment.response_threads)
    assert all(thread is not _APP.thread() for _messages, _tools, thread in environment.client.calls)
    assert environment.panel.chat._controller.state.value == "idle"
    snapshot = environment.services.session_lifecycle.read_session(
        environment.services.session_lifecycle.active.aggregate.ref, environment.binding.context
    )
    messages = snapshot.backend_messages()
    assert any(message.get("content") == "Answer: Explain lifecycle" for message in messages)
    assert len([message for message in messages if message["role"] == "user"]) == 1


def test_auto_capacity_reaches_routing_and_execution_for_large_input(environment):
    from transbridge.config.llm import LLMConfig
    from transbridge.smart_assistant.context_budget import budget_for_config

    config = LLMConfig(model="deepseek-v4-flash", base_url="https://api.deepseek.com/v1")
    environment.panel.chat._orchestrator._cached_llm_config = config
    prompt = "Explain compatibility. " * 7000
    assert budget_for_config(config).measure([{"role": "user", "content": prompt}]).total > 32768
    environment.panel.chat.send_user_message(prompt)
    _until(lambda: len(_requests(environment)) == 1 and _requests(environment)[0].terminal)
    assert len(environment.client.calls) == 2
    assert ROUTING_TOOL in environment.client.calls[0][1]
    assert COVERAGE_TOOL in environment.client.calls[1][1]
    assert _requests(environment)[0].status.value == "completed"


def test_three_rapid_inputs_are_all_saved_routed_and_answered(environment):
    for text in ("First question", "Second question", "Third question"):
        environment.panel.chat.send_user_message(text)
    _until(lambda: len(_requests(environment)) == 3 and all(request.terminal for request in _requests(environment)))

    assert {request.goal for request in _requests(environment)} == {
        "First question",
        "Second question",
        "Third question",
    }
    state = environment.service.state(environment.binding.context)
    assert [item["text"] for item in state["ingress"]] == ["First question", "Second question", "Third question"]
    assert all(item["status"] == "applied" for item in state["ingress"])
    snapshot = environment.services.session_lifecycle.read_session(
        environment.services.session_lifecycle.active.aggregate.ref, environment.binding.context
    )
    from transbridge.application.assistant_requests.transcript import TranscriptManifest

    transcript = environment.service.transcript_store.read(
        environment.binding.context.session_id, TranscriptManifest.from_dict(snapshot.transcript_data())
    )
    assert [message.content for message in transcript if message.role == "user"] == [
        "First question",
        "Second question",
        "Third question",
    ]


def test_new_inputs_interrupting_routing_do_not_drop_the_earlier_batch(environment):
    environment.client.hold_first_routing = True
    environment.panel.chat.send_user_message("First question")
    _until(environment.client.first_routing_started.is_set)
    environment.panel.chat.send_user_message("Second question")
    environment.panel.chat.send_user_message("Third question")
    _until(lambda: len(_requests(environment)) == 3 and all(request.terminal for request in _requests(environment)))
    state = environment.service.state(environment.binding.context)
    assert [item["text"] for item in state["ingress"]] == ["First question", "Second question", "Third question"]
    assert all(item["status"] == "applied" for item in state["ingress"])
    assert all(batch["status"] == "applied" for batch in state["batches"])
    assert environment.client.cancelled >= 1


def test_close_fences_pending_answer_and_stops_automatic_continuation(environment):
    environment.client.block_goal = "Held question"
    environment.panel.chat.send_user_message("Held question")
    _until(environment.client.answer_started.is_set)
    context = environment.binding.context
    environment.panel.dispose()
    environment.client.release.set()
    QTest.qWait(100)
    _APP.processEvents()

    request = environment.service.requests(environment.service.state(context))[0]
    assert not request.terminal
    assert len(environment.client.calls) == 2
    assert environment.binding.admission is None
    assert environment.binding._closed


def test_background_evidence_waits_for_current_answer_before_resuming_old_request(environment):
    from transbridge.application.assistant_requests.models import (
        Evidence,
        ItemKind,
        ItemStatus,
        RequestItem,
        UserRequest,
    )
    from transbridge.application.assistant_requests.reducer import add_evidence

    context = environment.binding.context
    old = UserRequest(
        "request-a",
        context.session_id,
        "Summarize A",
        (
            RequestItem(
                "work",
                "Existing background work",
                ItemKind.EXECUTION,
                status=ItemStatus.WAITING,
                waiting_reasons=("task",),
            ),
            RequestItem("summary", "Summarize the completed work", dependencies=("work",)),
        ),
        scope=(("owner_id", "owner"), ("session_id", context.session_id)),
    )
    environment.service.transact(context, lambda state: state.update(requests=[old.to_dict()]))
    environment.client.block_goal = "Question B"
    environment.panel.chat.send_user_message("Question B")
    _until(environment.client.answer_started.is_set)
    current = environment.binding.admission
    worker = environment.panel.chat._orchestrator.worker
    cancellations = environment.client.cancelled
    completed = Event()
    errors = []

    def record_background_result():
        try:
            environment.service.update_request(
                context,
                old.request_id,
                lambda request: add_evidence(request, Evidence("result-a", "execution", 1, ("work",), "receipt-a")),
            )
            environment.service.notify(context.session_id)
        except Exception as exc:
            errors.append(exc)
        finally:
            completed.set()

    thread = Thread(target=record_background_result)
    thread.start()
    _until(completed.is_set)
    thread.join()
    assert not errors
    QTest.qWait(50)
    assert environment.binding.admission == current
    assert environment.panel.chat._orchestrator.worker is worker
    assert environment.client.cancelled == cancellations
    assert len(environment.client.calls) == 2
    environment.client.release.set()
    _until(lambda: len(_requests(environment)) == 2 and all(request.terminal for request in _requests(environment)))
    assert len(environment.client.calls) == 3
    snapshot = environment.services.session_lifecycle.read_session(
        environment.services.session_lifecycle.active.aggregate.ref, context
    )
    answers = [
        message["content"]
        for message in snapshot.backend_messages()
        if message.get("content", "").startswith("Answer:")
    ]
    assert answers == ["Answer: Question B", "Answer: Summarize A"]
    old_request_input = environment.client.calls[-1][0]
    assert not any(message.get("content") in {"Question B", "Answer: Question B"} for message in old_request_input)


def test_switching_session_fences_old_worker_and_does_not_resume_its_request(environment):
    environment.client.block_goal = "Old session question"
    environment.panel.chat.send_user_message("Old session question")
    _until(environment.client.answer_started.is_set)
    old_context = environment.binding.context
    assert environment.services.gui_session_commands.create_and_activate("Other", RequestContext("owner")).is_success
    _until(lambda: environment.binding.context.session_id != old_context.session_id)
    environment.client.release.set()
    QTest.qWait(100)
    _APP.processEvents()
    old = environment.service.requests(environment.service.state(old_context))[0]
    assert not old.terminal
    assert environment.binding.admission is None
    assert not _requests(environment)
    assert len(environment.client.calls) == 2


def test_confirmation_click_keeps_request_identity_and_does_not_create_ingress(environment, monkeypatch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(
        widget for widget in environment.panel.chat._message_list._owned_widgets if isinstance(widget, ToolCard)
    )
    request = _requests(environment)[0]
    before = environment.service.state(environment.binding.context)["ingress"]
    card._exec_btn.click()
    _APP.processEvents()
    assert len(calls) == 1
    assert environment.binding.admission.request_id == request.request_id
    assert environment.service.state(environment.binding.context)["ingress"] == before
    card.executed.emit(card._step)
    assert len(calls) == 1


def test_stale_confirmation_cannot_execute_after_request_revision_change(environment, monkeypatch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(
        widget for widget in environment.panel.chat._message_list._owned_widgets if isinstance(widget, ToolCard)
    )
    request = _requests(environment)[0]
    environment.service.update_request(
        environment.binding.context,
        request.request_id,
        lambda old: replace(old, revision=old.revision + 1),
    )
    card._exec_btn.click()
    assert calls == []


def test_confirmation_resume_cannot_restore_other_request_or_remove_resource_wait(environment):
    from transbridge.application.assistant_requests.models import RequestItem, UserRequest
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    request = _requests(environment)[0]
    environment.service.command(
        environment.binding.context,
        request.request_id,
        "wait",
        request.revision,
        item_ids=[request.items[0].item_id],
        reason="resource",
    )
    other = UserRequest("other-ready", request.session_id, "Independent B", (RequestItem("b", "B"),))
    environment.service.transact(environment.binding.context, lambda state: state["requests"].append(other.to_dict()))
    environment.binding.control(request.request_id, "resume")
    assert environment.binding.admission is None
    assert set(_requests(environment)[0].items[0].waiting_reasons) == {"approval", "resource"}
    assert len([w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard)]) == 1


def test_confirmation_validator_rejects_other_view_before_changing_waits(environment):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard))
    before = _requests(environment)[0]
    environment.service.scheduler.activate(before.session_id, "other-view")
    card._exec_btn.click()
    after = _requests(environment)[0]
    assert after == before
    assert environment.binding.admission is None


def test_old_confirmation_does_not_regain_authority_when_same_view_reactivates(environment, monkeypatch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard))
    request = _requests(environment)[0]
    environment.service.scheduler.activate(request.session_id, "other-view")
    environment.service.scheduler.activate(request.session_id, environment.binding.view_id)
    card._exec_btn.click()
    assert not calls
    assert _requests(environment)[0] == request
    assert environment.binding.admission is None
    lease = environment.service.scheduler.acquire_routing(request.session_id, environment.binding.view_id)
    assert lease is not None
    environment.service.scheduler.release(lease)


def test_restored_confirmation_keeps_wait_until_approval_and_preserves_unrelated_failed_item(environment):
    from transbridge.application.assistant_requests.models import ItemStatus, RequestItem
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    request = _requests(environment)[0]
    environment.service.update_request(
        environment.binding.context,
        request.request_id,
        lambda old: replace(
            old, items=(*old.items, RequestItem("failed", "Another failed item", status=ItemStatus.FAILED))
        ),
    )
    environment.binding.control(request.request_id, "resume")
    current = _requests(environment)[0]
    assert current.items[0].waiting_reasons == ("approval",)
    assert current.items[1].status == ItemStatus.FAILED
    assert environment.binding.admission is None


def test_approval_consumes_saved_confirmation_before_business_callback(environment, monkeypatch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard))
    request = _requests(environment)[0]
    saved = environment.service.state(environment.binding.context)["confirmations"][request.request_id]
    assert saved["item_ids"] == [request.items[0].item_id]
    assert saved["confirmation_id"]
    card._exec_btn.click()
    assert len(calls) == 1
    assert request.request_id not in environment.service.state(environment.binding.context)["confirmations"]


def test_ignoring_confirmation_consumes_it_and_releases_lease_without_business_execution(environment, monkeypatch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.client.tool_goal = "Inspect A"
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard))
    request = _requests(environment)[0]
    card._ignore_btn.click()
    _until(lambda: environment.binding.admission is None)
    assert request.request_id not in environment.service.state(environment.binding.context)["confirmations"]
    current = _requests(environment)[0]
    assert current.pause_reasons == ("user_paused",)
    assert not current.items[0].waiting_reasons
    assert not calls
    assert environment.panel.chat._controller.state.value == "idle"


def test_confirmation_persistence_failure_keeps_wait_and_releases_temporary_lease(environment, monkeypatch):
    from transbridge.application.assistant_requests.models import RequestError
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    calls = []
    monkeypatch.setattr(environment.panel.chat._confirmation_view, "_tool_executed", lambda step: calls.append(step))
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    card = next(w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard))
    before = environment.service.state(environment.binding.context)
    original = environment.service.transact

    def transact(*args, **kwargs):
        if getattr(kwargs.get("cause"), "operation", "") == "confirmation.approved":
            raise RequestError("ADMISSION_PERSIST_FAILED", "injected approval write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(environment.service, "transact", transact)
    card._exec_btn.click()
    assert not calls
    assert environment.binding.admission is None
    assert environment.binding.gate is None
    assert environment.service.state(environment.binding.context) == before
    lease = environment.service.scheduler.acquire_routing(
        environment.binding.context.session_id, environment.binding.view_id
    )
    assert lease is not None
    environment.service.scheduler.release(lease)


@pytest.mark.parametrize("expired_epoch", [False, True])
def test_legacy_or_expired_confirmation_is_reproposed_without_replaying_parsed_steps(environment, expired_epoch):
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    request = _requests(environment)[0]

    def expire(state):
        saved = state["confirmations"][request.request_id]
        if expired_epoch:
            saved["lease_epoch"] -= 1
        else:
            del saved["item_ids"]

    environment.service.transact(environment.binding.context, expire)
    environment.binding.control(request.request_id, "resume")
    assert request.request_id not in environment.service.state(environment.binding.context)["confirmations"]
    assert environment.binding.admission is None
    assert not _requests(environment)[0].items[0].waiting_reasons
    assert len([w for w in environment.panel.chat._message_list._owned_widgets if isinstance(w, ToolCard)]) == 1


def test_hidden_panel_does_not_restart_for_late_answer_or_new_persisted_input(environment):
    environment.panel.show()
    _until(environment.panel.isVisible)
    environment.client.block_goal = "Held while hidden"
    environment.panel.chat.send_user_message("Held while hidden")
    _until(environment.client.answer_started.is_set)
    context = environment.binding.context
    environment.panel.hide()
    _until(lambda: not environment.binding._active)
    environment.service.accept_input(context, "Queued while hidden", selection={})
    environment.service.notify(context.session_id)
    environment.client.release.set()
    QTest.qWait(100)
    _APP.processEvents()
    assert len(environment.client.calls) == 2
    assert environment.binding.admission is None
    assert not environment.service.requests(environment.service.state(context))[0].terminal
    environment.panel.show()
    _until(lambda: len(_requests(environment)) == 2 and all(request.terminal for request in _requests(environment)))


def test_provider_failure_releases_foreground_lease_without_completing_request(environment):
    environment.client.fail_goal = "Fail this answer"
    environment.panel.chat.send_user_message("Fail this answer")
    _until(lambda: len(environment.client.calls) == 2 and environment.panel.chat._orchestrator.worker is None)
    assert environment.binding.admission is None
    assert environment.panel.chat._controller.state.value == "idle"
    assert not _requests(environment)[0].terminal
    assert _requests(environment)[0].pause_reasons


def test_request_list_restores_confirmation_after_new_question_and_retains_real_tool_result(environment, monkeypatch):
    from transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec
    from transbridge.smart_assistant.tools.base import ToolResult
    from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

    environment.client.tool_goal = "Inspect A"
    executed = []

    def execute(_args, _context):
        executed.append(True)
        return ToolResult(success=True, message="Recovered inspection result", data={"count": 7})

    namespaces = {name: dict(specs) for name, specs in ToolRegistry._namespaced_tools.items()}
    namespaces.setdefault("default", {})["get_statistics"] = ToolSpec(
        "get_statistics", "Statistics", "Read statistics", {}, execute=execute
    )
    monkeypatch.setattr(ToolRegistry, "_namespaced_tools", namespaces)
    environment.panel.chat.send_user_message("Inspect A")
    _until(lambda: any(isinstance(widget, ToolCard) for widget in environment.panel.chat._message_list._owned_widgets))
    old_card = next(
        widget for widget in environment.panel.chat._message_list._owned_widgets if isinstance(widget, ToolCard)
    )
    old_request = _requests(environment)[0]
    environment.panel.chat.send_user_message("Question B")
    _until(lambda: any(request.goal == "Question B" and request.terminal for request in _requests(environment)))
    old_card.executed.emit(old_card._step)
    assert not executed
    environment.binding.view.control.emit(old_request.request_id, "resume")
    _until(
        lambda: (
            len([
                widget for widget in environment.panel.chat._message_list._owned_widgets if isinstance(widget, ToolCard)
            ])
            == 2
        )
    )
    new_card = [
        widget for widget in environment.panel.chat._message_list._owned_widgets if isinstance(widget, ToolCard)
    ][-1]
    assert new_card is not old_card
    new_card._exec_btn.click()
    _until(
        lambda: all(request.terminal for request in _requests(environment)),
        diagnostic=lambda: repr((
            [(r.goal, r.status, r.pause_reasons, r.items) for r in _requests(environment)],
            environment.panel.chat._controller.state,
            [getattr(widget, "text", "") for widget in environment.panel.chat._message_list._owned_widgets],
        )),
    )
    assert executed == [True]
    snapshot = environment.services.session_lifecycle.read_session(
        environment.services.session_lifecycle.active.aggregate.ref, environment.binding.context
    )
    results = [message for message in snapshot.backend_messages() if message.get("role") == "tool"]
    assert any("Recovered inspection result" in message["content"] for message in results), repr(results)
    assert len(environment.service.state(environment.binding.context)["ingress"]) == 2


def test_terminal_continue_creates_successor_and_preserves_old_evidence(environment):
    environment.panel.chat.send_user_message("First question")
    _until(lambda: _requests(environment) and _requests(environment)[0].terminal)
    old = _requests(environment)[0]
    environment.binding.view.control.emit(old.request_id, "resume")
    _until(lambda: len(_requests(environment)) == 2 and all(r.terminal for r in _requests(environment)))
    first, successor = _requests(environment)
    assert first == old
    assert successor.request_id != old.request_id
    assert successor.related_to == old.request_id
    assert any(e.kind == "retry_of" for e in successor.evidence)


def test_stop_generation_requires_explicit_resume_and_retains_goal(environment):
    environment.client.block_goal = "Pause generation"
    environment.panel.chat.send_user_message("Pause generation")
    _until(environment.client.answer_started.is_set)
    request_id = _requests(environment)[0].request_id
    environment.binding.view.stop_generation.emit()
    environment.client.release.set()
    QTest.qWait(60)
    assert environment.binding.admission is None
    assert not _requests(environment)[0].terminal
    environment.binding.view.control.emit(request_id, "resume")
    _until(lambda: _requests(environment)[0].terminal)
    assert len(environment.service.state(environment.binding.context)["ingress"]) == 1
