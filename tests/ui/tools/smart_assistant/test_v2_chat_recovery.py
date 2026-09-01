from __future__ import annotations

from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.application.sessions.test_gui_session_management import build_session_services
from transbridge.application.contracts import RequestContext
from transbridge.application.sessions import ControllerSnapshot, ControllerState
from transbridge.infra.llm_tool_calling import LlmTurn
from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator
from transbridge.ui.tools.smart_assistant import panel as panel_module

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        _APP.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def chat_environment(tmp_path, monkeypatch):
    commands, lifecycle, repository, projection = build_session_services(tmp_path / "sessions")
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: tmp_path / "assistant")
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.chat_widget.QSettings",
        lambda *_args: SimpleNamespace(value=lambda _key, default, **_kwargs: default),
    )
    monkeypatch.setattr(panel_module.SmartAssistantPanel, "_init_skills", lambda _self: None)
    monkeypatch.setattr(panel_module.SmartAssistantPanel, "_configured_model_name", staticmethod(lambda: "test-model"))
    monkeypatch.setattr(panel_module, "set_window_app_user_model_id", lambda *_args: False)
    panels = []

    def panel():
        value = panel_module.SmartAssistantPanel(
            SimpleNamespace(),
            session_commands=commands,
            session_projection=projection,
            runtime_context=RequestContext("owner"),
        )
        panels.append(value)
        return value

    yield SimpleNamespace(
        commands=commands, lifecycle=lifecycle, repository=repository, projection=projection, panel=panel
    )
    for value in panels:
        value.dispose()
        value.deleteLater()
    projection.close()
    _APP.processEvents()


def _seed(environment, *, controller=ControllerSnapshot()):
    context = RequestContext("owner")
    assert environment.commands.create_and_activate("Saved", context).is_success
    ref = environment.lifecycle.active.aggregate.ref
    visible = [{"role": "user", "content": "Question"}, {"role": "assistant", "content": "Visible answer"}]
    backend = [
        {"role": "system", "content": "Retained system context"},
        {"role": "user", "content": "Question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "get_statistics", "arguments": {}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "get_statistics", "content": "Backend observation"},
        {"role": "assistant", "content": "Backend answer"},
    ]
    assert environment.commands.save_conversation(
        ref, visible, backend, context, controller=controller, backend_summary="Retained summary"
    ).is_success
    return ref, visible, backend


def test_real_panel_recovers_distinct_backend_and_visible_history_and_idle_controller(chat_environment):
    expected = ControllerSnapshot(ControllerState.IDLE, 4, True)
    ref, visible, backend = _seed(chat_environment, controller=expected)
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)

    assert panel.chat.recovery_snapshot() == (backend, expected)
    assert [widget.text for widget in panel.chat._message_list._owned_widgets] == [
        message["content"] for message in visible
    ]
    assert panel.chat._auto_cb.isChecked()
    assert panel.chat._orchestrator.auto_mode
    assert panel.chat._orchestrator.react_depth == 4
    assert panel.chat._orchestrator.worker is None
    assert panel._persist_authoritative_chat()
    saved = chat_environment.repository.load(ref).value.envelope.data
    assert saved["history"] == backend
    assert saved["messages"] == visible
    assert saved["backend_summary"] == "Retained summary"
    assert saved["controller"] == expected.to_dict()


@pytest.mark.parametrize("state", [ControllerState.THINKING, ControllerState.EXECUTING, ControllerState.AWAITING_TASK])
def test_real_panel_downgrades_in_flight_controller_without_starting_a_worker(chat_environment, state):
    _seed(
        chat_environment,
        controller=ControllerSnapshot(state, 4, True, False, "in_flight_controller_state_requires_job_reconciliation"),
    )
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)

    backend, restored = panel.chat.recovery_snapshot()
    assert restored.state is ControllerState.IDLE
    assert restored.react_depth == 0
    assert restored.auto_mode
    assert backend[-1]["content"] == "Backend answer"
    assert panel.chat._orchestrator.worker is None


def test_pending_session_load_keeps_latest_legacy_messages_only(chat_environment):
    panel = chat_environment.panel()
    panel.chat.load_session({"messages": [{"role": "user", "content": "old"}]})
    panel.chat.load_session({"messages": [{"role": "user", "content": "latest"}]})
    # The automatic default-session activation is separate from this direct legacy test.
    panel._session_subscription.close()
    _until(lambda: panel.chat.session_ready)

    assert panel.chat.recovery_snapshot()[0] == [{"role": "user", "content": "latest"}]
    QTest.qWait(100)
    assert panel.chat.recovery_snapshot()[0] == [{"role": "user", "content": "latest"}]


class _Client:
    model = "offline-test"

    def __init__(self):
        self.started = Event()
        self.release = Event()
        self.cancelled = False

    def chat_stream_with_tools(self, _messages, _max_tokens, _tools, on_chunk):
        self.started.set()
        assert self.release.wait(5)
        on_chunk("Persisted reply")
        return LlmTurn(text="Persisted reply")

    def cancel(self):
        self.cancelled = True
        self.release.set()


def test_real_worker_round_autosaves_v2_without_cancelling_or_reloading(chat_environment, monkeypatch):
    client = _Client()
    monkeypatch.setattr(ConversationOrchestrator, "_get_llm_client", lambda _self: client)
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    ref = chat_environment.lifecycle.active.aggregate.ref
    panel.chat.add_system_prompt("Offline test context")
    panel.chat.send_user_message("Please answer")
    _until(client.started.is_set)
    worker = panel.chat._orchestrator.worker
    from transbridge.smart_assistant.chat_worker import ChatWorker

    assert isinstance(worker, ChatWorker)
    generation = panel.chat._orchestrator._active_generation
    assert panel._persist_authoritative_chat()
    assert panel.chat._controller.state.value == "thinking"
    assert panel.chat._orchestrator._active_generation == generation
    assert not client.cancelled
    client.release.set()
    _until(lambda: panel.chat._controller.state.value == "idle" and not worker.is_alive())

    saved = chat_environment.repository.load(ref).value.envelope.data
    assert saved["history"][-1] == {"role": "assistant", "content": "Persisted reply"}
    assert saved["controller"]["state"] == "idle"
    assert not client.cancelled
    assert panel.chat._session_binding._generation == 1


def test_disabled_memory_initialization_never_reads_embedding_config_or_creates_client(chat_environment, monkeypatch):
    def unexpected(*_args, **_kwargs):
        pytest.fail("Disabled memory must not configure an embedding client")

    monkeypatch.setattr("transbridge.paratranz.config_manager.LLMConfig.load_from_file", unexpected)
    monkeypatch.setattr("transbridge.infra.create_llm_client", unexpected)
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    assert panel.chat._memory_store.count == 0
    assert panel.chat._memory_retriever is not None


def test_real_panel_switch_and_close_save_unsaved_history_without_losing_recovered_messages(chat_environment):
    first, visible, backend = _seed(chat_environment)
    context = RequestContext("owner")
    assert chat_environment.commands.create_and_activate("Other", context).is_success
    second = chat_environment.lifecycle.active.aggregate.ref
    assert chat_environment.commands.switch(first, context).is_success
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    extra = {"role": "user", "content": "Unsent first session change"}
    panel.chat._conversation.add_user(extra["content"])

    panel._session_list.switch_session.emit(second.identity.value)

    first_saved = chat_environment.repository.load(first).value.envelope.data
    assert first_saved["history"] == [*backend, extra]
    assert first_saved["messages"] == [*visible, extra]
    assert first_saved["backend_summary"] == "Retained summary"
    assert panel.chat.recovery_snapshot()[0] == []
    panel.chat._conversation.add_user("Close must save this")
    panel.close()
    assert not panel.is_disposed
    second_saved = chat_environment.repository.load(second).value.envelope.data
    assert second_saved["history"] == [{"role": "user", "content": "Close must save this"}]
    panel._session_list.switch_session.emit(first.identity.value)
    assert panel.chat.recovery_snapshot()[0] == [*backend, extra]


def test_invalid_recovery_controller_is_reset_without_starting_work(chat_environment):
    panel = chat_environment.panel()
    _until(lambda: panel.chat.session_ready)
    panel.chat.load_session({
        "messages": [{"role": "user", "content": "Legacy history"}],
        "controller": {"state": "invalid", "react_depth": -1},
    })
    backend, controller = panel.chat.recovery_snapshot()
    assert backend == [{"role": "user", "content": "Legacy history"}]
    assert controller.state is ControllerState.IDLE
    assert controller.react_depth == 0
    assert panel.chat._orchestrator.worker is None
