# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Best-effort Python stack capture when the GUI stops acknowledging heartbeats.

Use a Python monitor and retained frame references: the native delayed
faulthandler sampler can race frame destruction and crash CPython 3.12 itself.
A native call retaining the GIL delays sampling until Python can run again.
No process-wide fault handlers or delayed-dump timers are installed or changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer

from transbridge import __version__

_logger = logging.getLogger(__name__)


class UiHangWatchdog(QObject):
    """Serialize capture, rotation and close; write once per continuous stall."""

    MAX_LOG_BYTES = 2 * 1024 * 1024

    def __init__(self, log_dir: Path, parent=None, *, timeout_seconds: float = 5.0):
        super().__init__(parent)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        self._timeout = timeout_seconds
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
        self.path = Path(log_dir) / f"ui-hang-{stamp}-{os.getpid()}.log"
        self._file = None
        self._armed = False
        self._closed = False
        self._last_heartbeat = 0.0
        self._last_heartbeat_utc = ""
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._monitor: threading.Thread | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, min(1000, int(timeout_seconds * 250))))
        self._timer.timeout.connect(self._heartbeat)

    def start(self) -> None:
        if self._closed or self._file is not None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._open_file()
            self._arm()
            self._monitor = threading.Thread(target=self._watch, name="transbridge-gui-watchdog", daemon=True)
            self._monitor.start()
            self._timer.start()
            _logger.info("GUI hang watchdog enabled: timeout=%ss, stacks=%s", self._timeout, self.path)
        except Exception:
            self.close()
            _logger.exception("GUI hang watchdog could not start; continuing without automatic stack capture")

    def _open_file(self) -> None:
        self._file = self.path.open("xb", buffering=0)
        self._write(
            f"TransBridge {__version__} GUI hang diagnostics\n"
            f"Opened UTC: {datetime.now(UTC).isoformat()}\n"
            f"PID: {os.getpid()}; GUI thread: 0x{threading.get_ident():x}\n"
            f"Timeout: {self._timeout}s without a GUI heartbeat; one dump per stall.\n"
            "Python stacks only (no locals or source text); at most 100 threads / 100 frames per thread.\n\n"
            "Sampling requires Python execution; native calls retaining the GIL can delay capture.\n\n"
        )

    def _write(self, text: str) -> None:
        self._file.write(text.encode("utf-8"))

    def _arm(self) -> None:
        self._last_heartbeat = time.monotonic()
        self._last_heartbeat_utc = datetime.now(UTC).isoformat()
        self._armed = True

    def _watch(self) -> None:
        try:
            while not self._stop.wait(min(0.25, self._timeout / 4)):
                with self._lock:
                    if self._closed:
                        return
                    if self._armed and time.monotonic() - self._last_heartbeat >= self._timeout:
                        self._armed = False
                        self._write(f"Timeout ({self._timeout:.3f}s)!\n")
                        self._dump_threads()
        except Exception:
            self._stop.set()
            _logger.exception("GUI hang watchdog stopped after a diagnostic failure")

    def _dump_threads(self) -> None:
        # _current_frames retains the Python frame objects. Never walk raw
        # interpreter frames from a native thread without the GIL (CPython #140815).
        frames = sys._current_frames()
        try:
            for identity, frame in tuple(frames.items())[:100]:
                self._write(f"Thread 0x{identity:x} (most recent call first):\n")
                for _ in range(100):
                    if frame is None:
                        break
                    code = frame.f_code
                    self._write(f'  File "{code.co_filename[:500]}", line {frame.f_lineno} in {code.co_name[:500]}\n')
                    frame = frame.f_back
                self._write("\n")
        finally:
            # Do not keep application locals alive after the diagnostic finishes.
            frames.clear()

    def _record_recovery(self) -> None:
        elapsed = time.monotonic() - self._last_heartbeat
        if elapsed >= self._timeout:
            self._write(
                f"\nGUI heartbeat resumed UTC: {datetime.now(UTC).isoformat()}; "
                f"previous heartbeat UTC: {self._last_heartbeat_utc}; gap={elapsed:.3f}s\n\n"
            )

    def _heartbeat(self) -> None:
        if self._closed or self._file is None:
            return
        if self._stop.is_set():
            self.close()
            return
        try:
            with self._lock:
                self._record_recovery()
                if self._file.tell() >= self.MAX_LOG_BYTES:
                    self._rotate()
                self._arm()
        except Exception:
            self.close()
            _logger.exception("GUI hang watchdog stopped after a diagnostic I/O or scheduling failure")

    def _rotate(self) -> None:
        self._file.close()
        self._file = None
        older = self.path.with_suffix(".2.log")
        previous = self.path.with_suffix(".1.log")
        if previous.exists():
            previous.replace(older)
        self.path.replace(previous)
        self._open_file()

    def close(self) -> None:
        """Stop before Qt teardown, without touching unrelated fault handlers."""
        if self._closed:
            return
        self._timer.stop()
        self._stop.set()
        # The monitor never waits for the GUI or a queued Qt callback.
        if self._monitor is not None and self._monitor.is_alive():
            self._monitor.join()
        with self._lock:
            self._closed = True
            self._armed = False
            if self._file is not None:
                try:
                    self._file.close()
                except OSError:
                    _logger.exception("Could not close GUI hang diagnostic file: %s", self.path)
                finally:
                    self._file = None
