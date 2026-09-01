from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PyQt6.QtWidgets import QApplication, QPushButton

from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator import llm_connection_controller as module
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "connection worker did not reach the expected state"
        _APP.processEvents()
        time.sleep(0.005)


def _controller(config, results):
    button = QPushButton("测试 LLM 连接")

    def build():
        assert QThread.currentThread() == _APP.thread()
        return config

    controller = module.LlmConnectionController(
        SimpleNamespace(controls=SimpleNamespace(llm_test_btn=button)),
        SimpleNamespace(build=build),
        results.append,
    )
    return controller, button


def test_window_connection_test_keeps_event_loop_responsive_and_freezes_config(monkeypatch):
    config = LLMConfig(api_key="offline-test-key", model="original-model")
    started = threading.Event()
    release = threading.Event()
    observations = []
    results = []

    def create_client(snapshot):
        observations.append((snapshot.model, QThread.currentThread() == _APP.thread()))

        def chat(*_args, **_kwargs):
            started.set()
            assert release.wait(3)
            return "OK"

        return SimpleNamespace(chat=chat)

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", create_client)
    controller, button = _controller(config, results)
    window = SimpleNamespace(_llm_connection=controller)
    ticks = []
    try:
        AITranslatorWindow.on_test_connection(window)
        assert started.wait(3)
        assert not button.isEnabled()
        assert not controller.start()
        config.model = "changed-model"
        QTimer.singleShot(0, lambda: ticks.append("UI tick"))
        _wait(lambda: bool(ticks))
        assert results == []
    finally:
        release.set()
        _wait(lambda: not controller.is_running)
        controller.close()
    assert observations == [("original-model", False)]
    assert results[0].level == "info"
    assert button.isEnabled()
    assert button.text() == "测试 LLM 连接"


def test_closing_window_detaches_late_result_and_retains_worker_until_it_stops(monkeypatch):
    config = LLMConfig(api_key="offline-test-key", model="model")
    started = threading.Event()
    release = threading.Event()
    results = []

    def chat(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        raise RuntimeError("late provider failure")

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda _config: SimpleNamespace(chat=chat))
    controller, button = _controller(config, results)
    try:
        assert controller.start()
        assert started.wait(3)
        worker = controller._worker
        controller.close()
        controller.close()
        assert worker in module._ACTIVE_WORKERS
        assert not controller.start()
        button.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    finally:
        release.set()
        _wait(lambda: not module._ACTIVE_WORKERS)
    assert results == []
    assert not controller.is_running


def test_connection_validation_failure_restores_button_without_network(monkeypatch):
    def create_client(_config):
        raise AssertionError("missing credentials must not reach the provider")

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", create_client)
    results = []
    controller, button = _controller(LLMConfig(api_key="", model="model"), results)
    try:
        assert controller.start()
        _wait(lambda: not controller.is_running)
        assert results[0].level == "warning"
        assert "API Key" in results[0].message
        assert button.isEnabled()
    finally:
        controller.close()
