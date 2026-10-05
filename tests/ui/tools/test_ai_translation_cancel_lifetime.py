from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator import translator as translator_module
from transbridge.ai_translator.translator import AutoTranslator, _CancelledByStop
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


@pytest.mark.parametrize("cancel_raises", [False, True])
def test_cancel_keeps_worker_and_request_resources_alive_until_provider_exits(monkeypatch, cancel_raises):
    admitted, release, cancel_called, resources_closed = (threading.Event() for _ in range(4))
    budget = AiRequestBudget(1)
    calls = []

    class FastDeadlineEvent(threading.Event):
        def wait(self, timeout=None):
            # Advance finite deadlines without spending ten seconds reproducing
            # the old abandoned-request bug. Unbounded waits retain semantics.
            return super().wait(None if timeout is None else min(timeout, 0.02))

    monkeypatch.setattr(
        translator_module, "threading", SimpleNamespace(Event=FastDeadlineEvent, Thread=threading.Thread)
    )

    class Client:
        def chat(self, _messages, _tokens):
            admitted.set()
            assert release.wait(3)
            assert not resources_closed.is_set()
            return "late response"

        def cancel(self):
            calls.append("cancel")
            cancel_called.set()
            if cancel_raises:
                raise RuntimeError("provider cancellation failed")

    class Executor:
        def __init__(self, _request, *, stop_event, pause_event, **_kwargs):
            self.stop = stop_event
            self.pause = pause_event

        def execute(self, task):
            translator = AutoTranslator.__new__(AutoTranslator)
            translator._llm = LimitedLLMClient(Client(), budget, cancel_event=self.stop)
            try:
                translator._monitored_chat([], 0, self.pause, self.stop)
            except _CancelledByStop:
                return SourceOutcome(task, cancelled=True)
            finally:
                resources_closed.set()
            pytest.fail("cancelled translation returned a response")

    worker = AiTaskWorker(
        SimpleNamespace(request_budget=budget),
        [SimpleNamespace(key="source", entries=())],
        executor_factory=Executor,
    )
    worker.start()
    try:
        assert admitted.wait(2)
        assert budget.snapshot().in_flight == 1
        worker.stop()
        assert cancel_called.wait(2)
        assert not worker.wait(200), "worker must retain ownership of the outstanding request"
        assert not resources_closed.is_set()
        assert budget.snapshot().in_flight == 1
    finally:
        release.set()
        assert worker.wait(2000)
    assert calls == ["cancel"]
    assert resources_closed.is_set()
    assert budget.snapshot().in_flight == 0
