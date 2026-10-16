"""Offline real-widget evidence for material management and simultaneous send/stop."""

import json
import sys
from threading import Event

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _requests, _until
from transbridge.application.assistant_requests.attachments import attachment_snapshot
from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.file_parser import FileParser, ParsedDocument
from transbridge.ui.tools.smart_assistant.upload_binding import UploadBinding

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def _add(chat, path, body):
    path.write_text(body, encoding="utf-8")
    chat._upload_binding.add_paths([path])
    _until(lambda: not chat._upload_binding.busy)


def test_provider_receives_first_and_later_material_in_routing_and_execution(environment, tmp_path):
    chat, client = environment.panel.chat, environment.client
    _add(chat, tmp_path / "first.txt", "FIRST_ATTACHMENT_BODY_72193")
    chat.send_user_message("First attachment question")
    _until(lambda: bool(_requests(environment)) and all(r.terminal for r in _requests(environment)))
    assert len(client.calls) == 2
    for messages, _, _ in client.calls:
        assert "FIRST_ATTACHMENT_BODY_72193" in json.dumps(messages)
        assert not any("FIRST_ATTACHMENT_BODY_72193" in m.get("content", "") for m in messages if m["role"] == "system")
    chat._upload_binding.remove("first.txt")
    _add(chat, tmp_path / "second.txt", "SECOND_ATTACHMENT_BODY_58124")
    chat.send_user_message("Second attachment question")
    _until(lambda: len(_requests(environment)) == 2 and all(r.terminal for r in _requests(environment)))
    for messages, _, _ in client.calls[2:]:
        serialized = json.dumps(messages)
        assert "SECOND_ATTACHMENT_BODY_58124" in serialized
        assert "FIRST_ATTACHMENT_BODY_72193" not in serialized
    user_bubbles = [w for w in chat._message_list._owned_widgets if getattr(w, "_role", None) == "user"]
    assert "second.txt" in user_bubbles[-1].accessibleDescription()
    assert "SECOND_ATTACHMENT_BODY_58124" not in user_bubbles[-1].accessibleDescription()


def test_preview_remove_clear_and_session_isolation(environment, tmp_path):
    panel, chat = environment.panel, environment.panel.chat
    first_session = panel._current_session_id()
    _add(chat, tmp_path / "reference.txt", "Full original text")
    chat._upload_binding.open_manager()
    preview = chat._upload_binding._dialog
    assert preview.preview.toPlainText() == "Full original text"
    panel._on_create_session("Second")
    _until(lambda: panel._current_session_id() != first_session)
    assert chat._upload_binding.snapshot() is None
    assert chat._upload_label.isHidden()
    assert preview.isHidden()
    panel._switch_to(first_session)
    _until(lambda: bool(chat._uploaded_docs))
    chat._upload_binding.open_manager()
    preview.remove.click()
    assert chat._upload_binding.snapshot() == ()
    assert chat._upload_label.isHidden()
    _add(chat, tmp_path / "another.txt", "Another file")
    chat._clear_conversation()
    _until(lambda: not chat._request_binding.inputs.clearing)
    assert not chat._uploaded_docs
    assert chat._upload_label.full_text == "" and chat._upload_label.isHidden()
    assert chat._upload_binding.snapshot() == ()


def test_parsing_keeps_qt_responsive_and_late_result_cannot_cross_session(environment, tmp_path, monkeypatch):
    chat = environment.panel.chat
    path = tmp_path / "slow.txt"
    path.write_text("slow material", encoding="utf-8")
    entered, release = Event(), Event()
    threads, ticks = [], []

    class Parser:
        def parse(self, source):
            threads.append(QThread.currentThread())
            entered.set()
            assert release.wait(5)
            return ParsedDocument(source, "text", raw_text="late material")

    monkeypatch.setattr(FileParser, "get_parser", lambda _path: Parser())
    chat.set_input("Draft remains")
    chat._upload_binding.add_paths([path])
    try:
        _until(entered.is_set)
        QTimer.singleShot(0, lambda: ticks.append(True))
        _until(lambda: bool(ticks))
        assert threads[0] != QApplication.instance().thread()
        assert not chat._send_btn.isEnabled()
        chat._on_send()
        assert chat._input.toPlainText() == "Draft remains"
        assert not environment.client.calls
        environment.panel._on_create_session("Elsewhere")
        release.set()
        chat._upload_binding._executor.submit(lambda: None).result(2)
        QApplication.processEvents()
        assert not chat._uploaded_docs
        assert chat._upload_label.isHidden()
    finally:
        release.set()


def test_clear_fences_pending_parse_and_fresh_window_does_not_restore_unsent_drafts(environment, tmp_path, monkeypatch):
    chat = environment.panel.chat
    path = tmp_path / "pending.txt"
    path.write_text("pending", encoding="utf-8")
    entered, release = Event(), Event()

    class Parser:
        def parse(self, source):
            entered.set()
            assert release.wait(5)
            return ParsedDocument(source, "text", raw_text="never admitted")

    monkeypatch.setattr(FileParser, "get_parser", lambda _path: Parser())
    chat._upload_binding.add_paths([path])
    try:
        _until(entered.is_set)
        chat._upload_binding.clear()
        release.set()
        chat._upload_binding._executor.shutdown(wait=True)
        QApplication.processEvents()
        assert chat._upload_binding.snapshot() is None
        assert not chat._uploaded_docs
        parent = QWidget()
        fresh = UploadBinding(parent=parent, documents={}, notify=lambda _: None, max_bytes=1000)
        fresh.activate(environment.panel._current_session_id())
        assert fresh.snapshot() is None
        fresh.close()
        parent.close()
    finally:
        release.set()


def test_empty_or_oversize_text_fails_without_silent_truncation(environment, tmp_path, monkeypatch):
    chat = environment.panel.chat
    notices = []
    monkeypatch.setattr(chat._upload_binding, "_notify", notices.append)
    _add(chat, tmp_path / "blank.txt", "   ")
    _add(chat, tmp_path / "huge.txt", "X" * 200001)
    assert not chat._uploaded_docs
    assert len(notices) == 2
    assert "未提取" in notices[0] and "未截断" in notices[1]


def test_csv_reference_retains_column_headings_in_preview_and_submitted_material(environment, tmp_path):
    chat = environment.panel.chat
    _add(chat, tmp_path / "terms.csv", "Original,Preferred translation\nCastle,城堡\n")
    snapshot = chat._upload_binding.snapshot()
    assert snapshot[0]["content"] == "Original\tPreferred translation\nCastle\t城堡"


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_send_and_keyboard_followup_share_behavior_while_stop_stays_available(environment, monkeypatch, key):
    chat = environment.panel.chat
    sent, stopped = [], []
    monkeypatch.setattr(chat, "send_user_message", sent.append)
    monkeypatch.setattr(environment.binding.management, "stop_generation", lambda: stopped.append(True))
    chat._input_view.set_running(True)
    chat.set_input("Click follow-up")
    assert chat._send_btn.text() == "发送补充"
    assert not chat._input_view.stop_button.isHidden()
    chat._send_btn.click()
    assert sent == ["Click follow-up"] and not stopped
    chat._input_view.set_running(True)
    chat.set_input("Keyboard follow-up")
    assert chat.eventFilter(chat._input, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.ControlModifier))
    assert sent == ["Click follow-up", "Keyboard follow-up"]
    chat._input_view.set_running(True)
    chat._input_view.stop_button.click()
    assert stopped == [True]


def test_removed_attachment_sends_explicit_empty_snapshot(environment, tmp_path):
    chat = environment.panel.chat
    _add(chat, tmp_path / "remove.txt", "Removed draft material")
    chat._upload_binding.remove("remove.txt")
    chat.send_user_message("Nothing attached now")
    _until(lambda: bool(_requests(environment)) and all(r.terminal for r in _requests(environment)))
    user = next(m for m in chat._conversation.get_transcript() if m["role"] == "user")
    assert attachment_snapshot(user["content"])["attachments"] == []
    assert "Removed draft material" not in json.dumps(environment.client.calls, default=str)


def test_oversized_material_budget_stops_before_provider_and_explains_recovery(environment, tmp_path, monkeypatch):
    chat = environment.panel.chat
    notices = []
    monkeypatch.setattr(chat, "add_system_message", notices.append)
    chat._orchestrator._cached_llm_config = LLMConfig(model="offline-test", assistant_context_window=12000)
    _add(chat, tmp_path / "large.txt", "完整参考材料" * 4000)
    chat.send_user_message("Read the complete attachment")
    _until(lambda: bool(notices))
    assert not environment.client.calls
    assert any("CONTEXT_BUDGET_EXCEEDED" in text and "缩小材料" in text for text in notices)
    record = next(m for m in chat._conversation.get_transcript() if m["role"] == "user")
    assert attachment_snapshot(record["content"])["attachments"][0]["content"] == "完整参考材料" * 4000
    chat._upload_binding.remove("large.txt")
    chat.send_user_message("Small replacement without an attachment")
    _until(lambda: bool(_requests(environment)) and all(r.terminal for r in _requests(environment)))
    assert len(environment.client.calls) == 2
    assert "完整参考材料" not in json.dumps(environment.client.calls, ensure_ascii=False, default=str)
    state = environment.service.state(environment.binding.context)
    assert [item["status"] for item in state["ingress"]] == ["rejected", "applied"]
    assert state["batches"][0]["rejection_code"] == "CONTEXT_BUDGET_EXCEEDED"
    assert "完整参考材料" in environment.service.input_content(environment.binding.context, record["message_id"])


def test_snapshot_failure_preserves_unsent_input(environment, monkeypatch):
    chat = environment.panel.chat

    def fail():
        raise ValueError("附件尚未准备好")

    monkeypatch.setattr(chat._upload_binding, "snapshot", fail)
    chat.set_input("Keep my drafted request")
    chat._on_send()
    assert chat._input.toPlainText() == "Keep my drafted request"
    assert not environment.client.calls


@pytest.mark.parametrize("explicit_close", [False, True])
def test_queued_attachment_result_is_safe_when_parent_is_destroyed(tmp_path, monkeypatch, explicit_close):
    path = tmp_path / "queued.txt"
    path.write_text("Completed material", encoding="utf-8")
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append((kind, error)))
    parent = QWidget()
    documents = {"existing.txt": ParsedDocument(path, "text", raw_text="Existing material")}
    binding = UploadBinding(parent=parent, documents=documents, notify=lambda _: None, max_bytes=1000)
    binding.open_manager()
    binding.add_paths([path])
    # Finish the worker without processing Qt events, leaving delivery queued.
    binding._executor.shutdown(wait=True)
    assert binding.busy
    if explicit_close:
        binding.close()
    parent.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    assert not errors
    assert "queued.txt" not in documents
