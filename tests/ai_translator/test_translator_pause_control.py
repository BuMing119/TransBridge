from __future__ import annotations

import threading

from transbridge.ai_translator.translator import AutoTranslator, _CancelledByStop


def test_pause_allows_admitted_request_to_complete_and_gates_next_call():
    pause, stop, admitted, release = (threading.Event() for _ in range(4))
    pause.set()
    calls, results = [], []

    class Client:
        def chat(self, _messages, _tokens):
            calls.append("chat")
            admitted.set()
            assert release.wait(3)
            return "result"

        def cancel(self):
            calls.append("cancel")

    translator = AutoTranslator.__new__(AutoTranslator)
    translator._llm = Client()
    first = threading.Thread(target=lambda: results.append(translator._monitored_chat([], 0, pause, stop)))
    first.start()
    assert admitted.wait(2)
    pause.clear()
    release.set()
    first.join(2)
    assert not first.is_alive()
    assert results == ["result"] and calls == ["chat"]

    admitted.clear()
    second = threading.Thread(target=lambda: results.append(translator._monitored_chat([], 0, pause, stop)))
    second.start()
    try:
        assert not admitted.wait(0.15)
        pause.set()
        assert admitted.wait(2)
    finally:
        pause.set()
        second.join(2)
    assert results == ["result", "result"]
    assert calls == ["chat", "chat"]


def test_stop_while_waiting_for_resume_does_not_send_a_request():
    pause, stop = threading.Event(), threading.Event()
    results = []
    translator = AutoTranslator.__new__(AutoTranslator)

    def call():
        try:
            translator._monitored_chat([], 0, pause, stop)
        except _CancelledByStop:
            results.append("cancelled")

    thread = threading.Thread(target=call)
    thread.start()
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert results == ["cancelled"]
