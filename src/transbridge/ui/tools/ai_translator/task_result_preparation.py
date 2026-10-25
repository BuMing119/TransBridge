"""Prepare detached AI results away from the Qt event loop."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.ui.workers import ApiWorker

from .task_apply_preparation import ApplyDelivery


def prepare_outcomes(ledger, outcomes, keys, tasks, previous, cancelled):
    ledger.ingest(outcomes, keys, cancelled=cancelled)
    failed = {}
    for key in ledger.failed_keys:
        failed.setdefault(ledger.entries[key].source, []).append(
            key.serialize() if key.original is not None else key.local_key
        )
    full_tasks = {task.key: task for task in tasks}
    merged = dict(previous)
    for item in outcomes:
        old = previous.get(item.task.key)
        merged[item.task.key] = replace(
            item,
            task=full_tasks[item.task.key],
            polish={**(old.polish if old else {}), **item.polish},
            failed_keys=tuple(failed.get(item.task.key, ())),
        )
    return ledger, merged


def prepare_application(ledger, tasks):
    ledger.confirm_without_preview()
    selected = ledger.publish_to_drafts(tasks)
    # Build all summary/key projections while these private inputs are owned by
    # this worker. GUI refreshes after delivery perform constant-time reads.
    _ = ledger.index
    return ledger, selected


def prepare_applied(ledger, selected):
    ledger.mark_applied(selected)
    _ = ledger.index
    return ledger


class TaskResultPreparation(QObject):
    """One private computation at a time, retained through thread completion."""

    changed = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.worker = None
        self.delivery = None

    @property
    def busy(self):
        return self.worker is not None

    def start(self, operation, *, on_success, on_error):
        if self.busy:
            raise RuntimeError("正在整理任务结果，请稍候。")
        worker = ApiWorker(operation, route_http_errors=False)
        self.worker = worker

        def delivered(value, error):
            self.worker = self.delivery = None
            try:
                on_error(error) if error is not None else on_success(value)
            finally:
                self.changed.emit()

        self.delivery = ApplyDelivery(worker, delivered)
        try:
            worker.start()
        except Exception as exc:
            self.delivery.deleteLater()
            worker.deleteLater()
            delivered(None, str(exc))
            return
        self.changed.emit()
