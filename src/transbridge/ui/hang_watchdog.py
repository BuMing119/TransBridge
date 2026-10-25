"""Capture Python stacks when the GUI stops acknowledging a native watchdog.

The GUI entrypoint owns faulthandler's process-wide delayed-dump timer. Its C
watchdog can fire even when Python code cannot acquire the GIL. This module
never enables fault/signal handlers or terminates the process.
"""

from __future__ import annotations

from datetime import UTC, datetime
import faulthandler
import logging
import math
import os
from pathlib import Path
import threading
import time

from PyQt6.QtCore import QObject, QTimer

from transbridge import __version__

_logger = logging.getLogger(__name__)


class UiHangWatchdog(QObject):
    """One dump per missed heartbeat interval; rearm when the GUI recovers.

    Keep the output descriptor open until the native watchdog has stopped.
    Rotate only this session's files, after cancellation, retaining two backups.
    A single dump may exceed the size limit; it must remain intact for diagnosis.
    """

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
            self._timer.start()
            _logger.info("GUI hang watchdog enabled: timeout=%ss, stacks=%s", self._timeout, self.path)
        except Exception:
            self.close()
            _logger.exception("GUI hang watchdog could not start; continuing without automatic stack capture")

    def _open_file(self) -> None:
        # Unbuffered output shares the descriptor with faulthandler's native write.
        self._file = self.path.open("xb", buffering=0)
        self._write(
            f"TransBridge {__version__} GUI hang diagnostics\n"
            f"Opened UTC: {datetime.now(UTC).isoformat()}\n"
            f"PID: {os.getpid()}; GUI thread: 0x{threading.get_ident():x}\n"
            f"Timeout: {self._timeout}s without a GUI heartbeat; one dump per stall.\n"
            "Python stacks only (no locals or source text); at most 100 threads / 100 frames per thread.\n\n"
        )

    def _write(self, text: str) -> None:
        self._file.write(text.encode("utf-8"))

    def _arm(self) -> None:
        self._last_heartbeat = time.monotonic()
        self._last_heartbeat_utc = datetime.now(UTC).isoformat()
        faulthandler.dump_traceback_later(self._timeout, repeat=False, file=self._file, exit=False)
        self._armed = True

    def _cancel(self) -> None:
        if self._armed:
            faulthandler.cancel_dump_traceback_later()
            self._armed = False

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
        try:
            self._cancel()
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
        self._closed = True
        self._timer.stop()
        self._cancel()
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                _logger.exception("Could not close GUI hang diagnostic file: %s", self.path)
            finally:
                self._file = None
