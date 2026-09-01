"""Background LLM connection checks with detached, bounded window ownership."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from functools import partial

from transbridge.ui.workers import ApiWorker

from .config_presenter import ConnectionTestResult, check_llm_connection

_ACTIVE_WORKERS: set[ApiWorker] = set()


def _retain_until_finished(worker: ApiWorker) -> None:
    _ACTIVE_WORKERS.add(worker)

    def release() -> None:
        _ACTIVE_WORKERS.discard(worker)
        worker.deleteLater()

    worker.finished.connect(release)


class LlmConnectionController:
    """Read the form on the GUI thread, then run only the detached request in a worker."""

    def __init__(self, view, presenter, show_result: Callable[[ConnectionTestResult], None]) -> None:
        self._button = view.controls.llm_test_btn
        self._build_config = presenter.build
        self._show_result = show_result
        self._worker: ApiWorker | None = None
        self._closed = False

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def start(self) -> bool:
        if self._closed or self._worker is not None:
            return False
        try:
            config = deepcopy(self._build_config())
        except Exception as exc:
            self._present(ConnectionTestResult("critical", "LLM 连接失败", str(exc)))
            return False
        worker = ApiWorker(partial(check_llm_connection, config), route_http_errors=False)
        self._worker = worker
        _retain_until_finished(worker)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        self._set_busy(True)
        try:
            worker.start()
        except Exception as exc:
            _ACTIVE_WORKERS.discard(worker)
            self._worker = None
            worker.deleteLater()
            self._set_busy(False)
            self._on_error(str(exc))
            return False
        return True

    def close(self) -> None:
        """Do not block on HTTP; retain the worker but detach all window callbacks."""
        self._closed = True
        self._show_result = None
        self._build_config = None
        self._button = None
        worker, self._worker = self._worker, None
        if worker is not None:
            for signal, slot in (
                (worker.result, self._on_result),
                (worker.error, self._on_error),
                (worker.finished, self._on_finished),
            ):
                signal.disconnect(slot)

    def _on_result(self, result: object) -> None:
        if isinstance(result, ConnectionTestResult):
            self._present(result)
        else:
            self._on_error("连接测试返回了无效结果。")

    def _on_error(self, message: str) -> None:
        self._present(ConnectionTestResult("critical", "LLM 连接失败", message))

    def _on_finished(self) -> None:
        self._worker = None
        if not self._closed:
            self._set_busy(False)

    def _present(self, result: ConnectionTestResult) -> None:
        if not self._closed and self._show_result is not None:
            self._show_result(result)

    def _set_busy(self, busy: bool) -> None:
        self._button.setEnabled(not busy)
        self._button.setText("正在测试…" if busy else "测试 LLM 连接")


__all__ = ["LlmConnectionController"]
