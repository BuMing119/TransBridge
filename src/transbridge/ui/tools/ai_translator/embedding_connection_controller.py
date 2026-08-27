"""Asynchronous connection-test lifecycle for the Embedding API panel."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from transbridge.ui.tools.ai_translator.config_presenter import ConnectionTestResult
from transbridge.ui.workers import ApiWorker


class _ConnectionTestPresenter(Protocol):
    def build(self) -> object: ...

    def test_embedding_connection(self, config: object | None = None) -> ConnectionTestResult: ...


_ACTIVE_WORKERS: set[ApiWorker] = set()


def _retain_until_finished(worker: ApiWorker) -> None:
    """Keep a detached worker alive without retaining its former window/controller."""

    _ACTIVE_WORKERS.add(worker)

    def release() -> None:
        _ACTIVE_WORKERS.discard(worker)
        worker.deleteLater()

    worker.finished.connect(release)


class EmbeddingConnectionController:
    """Run one explicit API connection test without blocking or outliving its window UI."""

    _IDLE_TEXT = "测试 Embedding 连接"
    _BUSY_TEXT = "正在测试…"
    _BUSY_PROPERTY = "embeddingConnectionBusy"

    def __init__(
        self,
        view: Any,
        presenter: _ConnectionTestPresenter,
        show_result: Callable[[ConnectionTestResult], None],
    ) -> None:
        self._view = view
        self._presenter = presenter
        self._show_result: Callable[[ConnectionTestResult], None] | None = show_result
        self._worker: ApiWorker | None = None
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self) -> bool:
        """Start an API-only connection test and return whether work was accepted."""

        if self._closed or self._worker is not None:
            return False
        controls = self._view.controls
        if controls.embed_provider_combo.currentData() != "api":
            return False
        try:
            # Reading the Qt-backed form must stay on the GUI thread. The worker
            # receives this detached configuration snapshot and performs only I/O.
            config = self._presenter.build()
        except Exception as exc:
            self._present(ConnectionTestResult("critical", "语义检索检查失败", str(exc)))
            return False

        worker = ApiWorker(
            lambda: self._presenter.test_embedding_connection(config),
            route_http_errors=False,
        )
        self._worker = worker
        _retain_until_finished(worker)
        self._set_busy(True)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        worker.start()
        return True

    def close(self) -> None:
        """Detach the window UI while allowing an in-flight network request to finish safely."""

        self._closed = True
        self._show_result = None
        worker = self._worker
        self._worker = None
        if worker is not None:
            for signal, slot in (
                (worker.result, self._on_result),
                (worker.error, self._on_error),
                (worker.finished, self._on_finished),
            ):
                try:
                    signal.disconnect(slot)
                except TypeError:
                    pass

    def _on_result(self, result: object) -> None:
        if isinstance(result, ConnectionTestResult):
            self._present(result)
            return
        self._present(ConnectionTestResult("critical", "语义检索检查失败", "连接测试返回了无效结果。"))

    def _on_error(self, message: str) -> None:
        self._present(ConnectionTestResult("critical", "语义检索检查失败", message))

    def _on_finished(self) -> None:
        self._worker = None
        if not self._closed:
            self._set_busy(False)

    def _present(self, result: ConnectionTestResult) -> None:
        if not self._closed and self._show_result is not None:
            self._show_result(result)

    def _set_busy(self, busy: bool) -> None:
        button = self._view.controls.embed_test_btn
        button.setProperty(self._BUSY_PROPERTY, busy)
        button.setText(self._BUSY_TEXT if busy else self._IDLE_TEXT)
        is_api = self._view.controls.embed_provider_combo.currentData() == "api"
        button.setEnabled(is_api and not busy)


__all__ = ["EmbeddingConnectionController"]
