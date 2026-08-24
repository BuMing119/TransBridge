from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

import transbridge.ui.workbench.remote_target_view as remote_target_view


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _ControlledWorker:
    instances = []

    def __init__(self, _fn) -> None:
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.running = False
        self.deleted = False
        self.instances.append(self)

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:  # noqa: N802 - QThread compatibility
        return self.running

    def deleteLater(self) -> None:  # noqa: N802 - QObject compatibility
        self.deleted = True

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


def test_reject_cancels_catalog_request_and_defers_destruction_until_worker_finishes(qapp, monkeypatch) -> None:
    _ControlledWorker.instances.clear()
    monkeypatch.setattr(remote_target_view, "ApiWorker", _ControlledWorker)
    context = SimpleNamespace(
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=7),
        current_user={"id": 7},
    )
    dialog = remote_target_view.ParaTranzTargetDialog(
        context,
        remote_target_view.ParaTranzProjectCatalog(),
        0,
    )
    worker = _ControlledWorker.instances[-1]

    dialog.reject()

    assert dialog._closing is True
    assert dialog._cancellation is not None and dialog._cancellation.is_cancelled
    assert worker in dialog._workers

    worker.finish()

    assert dialog._closing is False
    assert dialog._workers == []
    assert worker.deleted is True
    dialog.deleteLater()
    qapp.processEvents()
