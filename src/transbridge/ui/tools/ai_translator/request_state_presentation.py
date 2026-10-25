"""Present task-wide request counters without refreshing per-source results."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.application.translation.ai_request_budget import AiRequestBudgetSnapshot


class RequestStatePresentation(QObject):
    """Observe the current worker only; request counts do not alter task status."""

    changed = pyqtSignal(str)

    def __init__(self, max_in_flight: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.max_in_flight = max_in_flight
        self.snapshot: AiRequestBudgetSnapshot | None = None
        self.text = "请求状态：等待开始"
        self._worker = None

    def bind(self, worker) -> None:
        self._worker = worker
        self.snapshot = None
        self._show("请求状态：等待发送")
        signal = getattr(worker, "request_state_changed", None)
        if signal is not None:
            signal.connect(lambda snapshot: self._observe(worker, snapshot))

    def finish(self, worker) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        if self.snapshot is not None:
            self.snapshot = replace(self.snapshot, in_flight=0, waiting=0, preparing=0, waiting_preparation=0)
            self._show(self._format(self.snapshot))
        else:
            self._show("请求状态：已结束")

    def _observe(self, worker, snapshot: AiRequestBudgetSnapshot) -> None:
        if self._worker is not worker or snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        self._show(self._format(snapshot))

    def _format(self, snapshot: AiRequestBudgetSnapshot) -> str:
        return (
            f"准备中 {snapshot.preparing} · 等待准备 {snapshot.waiting_preparation} · "
            f"等待发送 {snapshot.waiting} · 模型调用中 {snapshot.in_flight}/{self.max_in_flight}"
        )

    def _show(self, text: str) -> None:
        self.text = text
        self.changed.emit(text)
