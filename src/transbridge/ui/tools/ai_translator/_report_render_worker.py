"""Background rendering for canonical AI report snapshots."""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from transbridge.application.translation import ReportSnapshot

from .reporting import render_snapshot_report


class _ReportRenderWorker(QThread):
    """Render durable report files without blocking the Qt event loop."""

    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, snapshot: ReportSnapshot, esp_stem: str) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._esp_stem = esp_stem

    def run(self) -> None:
        try:
            artifacts = render_snapshot_report(self._snapshot, self._esp_stem)
        except Exception as exc:
            self.failed.emit(f"REPORT_RENDER_FAILED: {type(exc).__name__}: {exc}")
            return
        self.completed.emit(artifacts)


_ACTIVE_REPORT_WORKERS: set[_ReportRenderWorker] = set()


def start_report_render(
    snapshot: ReportSnapshot,
    esp_stem: str,
    *,
    on_completed,
    on_failed,
) -> _ReportRenderWorker:
    """Start one retained worker; callbacks are delivered on the Qt thread."""
    worker = _ReportRenderWorker(snapshot, esp_stem)
    _ACTIVE_REPORT_WORKERS.add(worker)
    worker.completed.connect(on_completed)
    worker.failed.connect(on_failed)

    def release() -> None:
        _ACTIVE_REPORT_WORKERS.discard(worker)
        worker.deleteLater()

    worker.finished.connect(release)
    worker.start()
    return worker


__all__ = ["_ReportRenderWorker", "start_report_render"]
