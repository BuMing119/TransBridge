from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator import _mixed_worker, _translation_worker, run_controller
from transbridge.ui.tools.ai_translator._translation_worker import _TranslationWorker

_APP = QApplication.instance() or QApplication([])


def _drain_until(predicate) -> None:
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)
    assert predicate()


def _mixed_scenario(outcome: str, cleanup_error: bool) -> None:
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    stopped = threading.Event()
    exceptions = []
    terminal = []

    class LogStore:
        is_available = False
        last_error = ""

        def close(self):
            cleanup_started.set()
            assert release_cleanup.wait(5)
            if cleanup_error:
                raise OSError("log close failed")

    worker_class = _mixed_worker._MixedWorker

    def create_worker(**kwargs):
        worker = worker_class(**kwargs)

        def run_stage():
            if outcome == "error":
                raise ValueError("stage failed")
            if outcome == "cancelled":
                worker.cancel()
            return {}

        worker._run_serial = run_stage
        worker._finalize_report = lambda result: result
        worker.finished.connect(stopped.set, Qt.ConnectionType.DirectConnection)
        return worker

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "excepthook", lambda *exc: exceptions.append(exc))
        patch.setattr(_mixed_worker, "WorkflowLogStore", lambda *_args, **_kwargs: LogStore())
        patch.setattr(_mixed_worker, "_MixedWorker", create_worker)
        patch.setattr(run_controller, "show_and_activate", lambda *_args, **_kwargs: None)
        config = LLMConfig(mixed_execution_order="serial")
        controller = run_controller.RunController()
        request = controller.begin("mixed", config, [])
        progress = run_controller.start_mixed_run(
            controller,
            request,
            SimpleNamespace(esp_path="Plugin.esp"),
            config,
            [],
            [],
            finished=lambda _result: terminal.append("success"),
            error=lambda _error: terminal.append("error"),
            cancelled=lambda: terminal.append("cancelled"),
        )
        worker = progress._worker
        try:
            assert cleanup_started.wait(5)
            _drain_until(lambda: bool(terminal))
            assert terminal == [outcome]
            assert not stopped.is_set()
            assert not sip.isdeleted(worker)
            assert worker.isRunning()
            # Shell shutdown checks must include the log-cleanup tail.
            assert progress.is_running()
        finally:
            release_cleanup.set()
            if not sip.isdeleted(worker):
                assert worker.wait(5000)
        _drain_until(lambda: sip.isdeleted(worker))
        assert stopped.is_set()
        assert terminal == [outcome]
        assert not exceptions
        assert not progress.is_running()
        progress.close()


@pytest.mark.parametrize("outcome", ["success", "error", "cancelled"])
@pytest.mark.parametrize("cleanup_error", [False, True], ids=["cleanup-ok", "cleanup-fails"])
def test_mixed_native_finished_owns_deletion_after_slow_cleanup(outcome, cleanup_error) -> None:
    """Keep native QThread abort regressions isolated from the pytest process."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), outcome, str(int(cleanup_error))],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_translation_log_initialization_failure_emits_one_error_and_native_finished(monkeypatch, caplog) -> None:
    exceptions, errors, results, translated, closed = [], [], [], [], []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: exceptions.append(exc))
    worker = _TranslationWorker(
        SimpleNamespace(translate=lambda **_kwargs: translated.append(True)),
        [],
        [],
        esp_path="Plugin.esp",
        log_store=SimpleNamespace(close=lambda: closed.append(True)),
    )

    def fail_init():
        raise PermissionError("cannot create stream directory")

    worker._make_stream_log_dir = fail_init
    worker.error.connect(errors.append)
    worker.result.connect(results.append)
    stopped = threading.Event()
    worker.finished.connect(stopped.set, Qt.ConnectionType.DirectConnection)
    worker.start()
    assert worker.wait(5000)
    _drain_until(lambda: bool(errors))
    assert errors == ["cannot create stream directory"]
    assert stopped.is_set()
    assert not results and not translated and not exceptions
    assert closed == [True]
    assert "PermissionError" in caplog.text
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_translation_cleanup_failures_preserve_success_and_close_remaining_resources(monkeypatch, caplog) -> None:
    exceptions, errors, results, closed = [], [], [], []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: exceptions.append(exc))

    class Handle:
        def write(self, _chunk):
            pass

        def flush(self):
            pass

        def close(self):
            closed.append("file")
            raise OSError("stream close failed")

    def close_store():
        closed.append("store")
        raise OSError("store close failed")

    result = SimpleNamespace(post_process_result=None)

    def translate(**kwargs):
        kwargs["stream_callback"](0, "first")
        kwargs["stream_callback"](1, "second")
        return result

    from transbridge.ui.tools.ai_translator import reporting

    monkeypatch.setattr(_translation_worker, "open", lambda *_args, **_kwargs: Handle(), raising=False)
    monkeypatch.setattr(
        reporting,
        "render_translation_report",
        lambda *_args: SimpleNamespace(excel_path=None, paths=(), diagnostics=()),
    )
    worker = _TranslationWorker(
        SimpleNamespace(translate=translate),
        [],
        [],
        esp_path="Plugin.esp",
        log_store=SimpleNamespace(close=close_store),
    )
    worker._make_stream_log_dir = lambda: "unused"
    worker.error.connect(errors.append)
    worker.result.connect(results.append)
    worker.start()
    assert worker.wait(5000)
    _drain_until(lambda: bool(results))
    assert results == [result]
    assert not errors and not exceptions
    assert closed == ["file", "file", "store"]
    assert "stream close failed" in caplog.text
    assert "store close failed" in caplog.text
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    _mixed_scenario(sys.argv[1], bool(int(sys.argv[2])))
