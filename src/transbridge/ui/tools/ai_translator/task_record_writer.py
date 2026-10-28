"""Serialize task records independently of progress views and optional exports."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.ui.workers import ApiWorker


class TaskRecordWriter(QObject):
    changed = pyqtSignal()
    ready = pyqtSignal(object)

    def __init__(self, request, session, parent=None):
        super().__init__(parent)
        self.request = request
        self.session = session
        self.created_at = datetime.now().astimezone().isoformat()
        self.worker = None
        self.pending = None
        self.last_request = None
        self.record = None
        self.status = ""
        self.error = ""
        self._sequence = 0
        from .task_record_persistence import TaskRecordPersistence

        self._persistence = TaskRecordPersistence()
        owner = getattr(getattr(request, "spec", None), "owner", None)
        self._report_request = SimpleNamespace(
            run_id=request.run_id,
            spec=SimpleNamespace(
                owner=SimpleNamespace(
                    project_id=getattr(owner, "project_id", None), variant_id=getattr(owner, "variant_id", None)
                )
            ),
            config=SimpleNamespace(
                mixed_execution_order=getattr(getattr(request, "config", None), "mixed_execution_order", "")
            ),
        )

    @property
    def busy(self):
        return self.worker is not None or self.pending is not None

    def write(self, outcomes, *, state, frozen_entry_results):
        from .task_record_inputs import source_descriptors

        frozen = source_descriptors(outcomes)
        ledger = frozen_entry_results
        self._sequence += 1
        self.pending = (
            self._sequence,
            frozen,
            state,
            bool(self.session.applied_keys),
            self.session.saved,
            bool(self.session.project_saved),
            False,
            ledger,
        )
        self.last_request = self.pending
        if self.worker is None:
            self._start()

    def retry(self):
        if self.last_request is not None and self.worker is None:
            self.pending = self.last_request
            self._start()

    def _start(self):
        sequence, outcomes, state, applied, saved, project_saved, snapshot_saved, entry_results = self.pending
        self.pending = None
        self.status, self.error = "正在保存任务记录", ""
        self.changed.emit()
        history_dir = getattr(self.session, "history_dir", None)
        version_label = getattr(self.session, "version_label", "")
        report_request, created_at = self._report_request, self.created_at

        def persist():
            return self._persistence.save(
                history_dir,
                outcomes,
                entry_results,
                report_request,
                created_at=created_at,
                state=state,
                applied=applied,
                saved=saved,
                project_saved=project_saved,
                snapshot_saved=snapshot_saved,
                version_label=version_label,
            )

        worker = ApiWorker(persist, route_http_errors=False)
        self.worker = worker

        def success(result):
            if sequence != self._sequence:
                return
            record, error = result
            self.record = record
            self.error = error
            self.status = f"任务记录保存失败：{error}" if error else "任务记录已保存"
            self.ready.emit(record)
            self.changed.emit()

        def failed(error):
            if sequence != self._sequence:
                return
            self.error = str(error)
            self.status = f"任务记录保存失败：{error}"
            self.changed.emit()

        def finished():
            self.worker = None
            worker.deleteLater()
            if self.pending is not None:
                self._start()
            else:
                self.changed.emit()

        worker.result.connect(success)
        worker.error.connect(failed)
        worker.finished.connect(finished)
        worker.start()
