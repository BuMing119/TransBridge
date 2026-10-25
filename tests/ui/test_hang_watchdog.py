from __future__ import annotations

import os
import subprocess
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui import hang_watchdog
from transbridge.ui.hang_watchdog import UiHangWatchdog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def native_timer(monkeypatch):
    """Enforce descriptor lifetime without replacing pytest's own fault handlers."""
    active = []

    def arm(timeout, *, repeat, file, exit):
        assert not active
        assert timeout > 0 and not repeat and not exit
        assert not file.closed
        active.append(file)

    def cancel():
        assert len(active) == 1
        assert not active.pop().closed

    monkeypatch.setattr(hang_watchdog.faulthandler, "dump_traceback_later", arm)
    monkeypatch.setattr(hang_watchdog.faulthandler, "cancel_dump_traceback_later", cancel)
    return active


def test_heartbeat_rearms_and_close_cancels_before_descriptor_release(qapp, tmp_path, native_timer):
    watchdog = UiHangWatchdog(tmp_path)
    try:
        watchdog.start()
        original = native_timer[0]
        for _ in range(5):
            watchdog._heartbeat()
            assert native_timer == [original]
        assert "GUI thread: 0x" in watchdog.path.read_text(encoding="utf-8")
        assert "heartbeat resumed" not in watchdog.path.read_text(encoding="utf-8")
    finally:
        watchdog.close()
    assert original.closed and not native_timer
    assert not watchdog._timer.isActive()
    watchdog.close()
    watchdog.start()
    assert not native_timer


def test_rotation_preserves_latest_dumps_and_never_closes_an_armed_file(qapp, tmp_path, native_timer, monkeypatch):
    watchdog = UiHangWatchdog(tmp_path)
    monkeypatch.setattr(watchdog, "MAX_LOG_BYTES", 1)
    try:
        watchdog.start()
        for index in range(5):
            watchdog._file.write(f"synthetic-dump-{index}\n".encode())
            watchdog._heartbeat()
        files = sorted(tmp_path.glob("*.log"))
        assert len(files) == 3
        text = "".join(path.read_text(encoding="utf-8") for path in files)
        assert "synthetic-dump-4" in text and "synthetic-dump-3" in text
        assert "synthetic-dump-0" not in text and "synthetic-dump-2" not in text
    finally:
        watchdog.close()
    assert not native_timer


def test_unwritable_destination_does_not_block_gui(qapp, tmp_path, native_timer, caplog):
    destination = tmp_path / "not-a-directory"
    destination.write_text("existing file", encoding="utf-8")
    watchdog = UiHangWatchdog(destination)
    watchdog.start()
    assert not native_timer and not watchdog._timer.isActive()
    assert "could not start" in caplog.text
    assert destination.read_text(encoding="utf-8") == "existing file"
    watchdog.close()


def test_arm_failure_closes_file_and_leaves_gui_running(qapp, tmp_path, native_timer, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("watchdog unavailable")

    monkeypatch.setattr(hang_watchdog.faulthandler, "dump_traceback_later", fail)
    watchdog = UiHangWatchdog(tmp_path)
    watchdog.start()
    assert watchdog._file is None and not watchdog._timer.isActive()
    assert "watchdog unavailable" in caplog.text


@pytest.mark.parametrize("hold_gil", [False, True], ids=["python-wait", "native-gil-block"])
def test_native_capture_during_gui_stall_and_recovery_in_subprocess(tmp_path, hold_gil):
    if hold_gil and os.name != "nt":
        pytest.skip("The GIL-holding Sleep probe uses the Windows API")
    script = textwrap.dedent("""
        import ctypes
        from pathlib import Path
        import sys
        import time
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        from transbridge.ui.hang_watchdog import UiHangWatchdog

        app = QApplication([])
        watchdog = UiHangWatchdog(Path(sys.argv[1]), app, timeout_seconds=0.15)
        failures = []
        def exception_hook(kind, value, traceback):
            failures.append(str(value))
            app.quit()
        sys.excepthook = exception_hook
        counts = []
        def stall():
            private_local = "LOCAL_VALUE_MUST_NOT_APPEAR_IN_STACK"
            if sys.argv[2] == "1":
                # PyDLL deliberately retains the GIL across the native call.
                kernel = ctypes.PyDLL("kernel32")
                kernel.Sleep.argtypes = [ctypes.c_ulong]
                kernel.Sleep.restype = None
                kernel.Sleep(450)
            else:
                time.sleep(0.45)
            content = watchdog.path.read_text(encoding="utf-8")
            assert private_local not in content
            assert "stall" in content
            counts.append(content.count("Timeout ("))
            if len(counts) == 1:
                QTimer.singleShot(100, stall)
            else:
                QTimer.singleShot(100, app.quit)

        def healthy_then_stall():
            assert "Timeout (" not in watchdog.path.read_text(encoding="utf-8")
            stall()

        app.aboutToQuit.connect(watchdog.close)
        watchdog.start()
        QTimer.singleShot(350, healthy_then_stall)
        QTimer.singleShot(4000, app.quit)
        try:
            app.exec()
        finally:
            watchdog.close()
        assert not failures, failures
        assert counts == [1, 2], counts
        content = watchdog.path.read_text(encoding="utf-8")
        assert "previous heartbeat UTC:" in content
        assert "GUI thread: 0x" in content
        assert "LOCAL_VALUE_MUST_NOT_APPEAR_IN_STACK" not in content
        before = watchdog.path.stat().st_size
        time.sleep(0.25)
        assert watchdog.path.stat().st_size == before
        print("healthy heartbeat, two stalls, recovery and clean shutdown verified")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), "1" if hold_gil else "0"],
        capture_output=True,
        text=True,
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
