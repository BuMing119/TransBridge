"""Serialize task records independently of progress views and optional exports."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
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

    def write(self, outcomes, *, state, entry_results=None, frozen_entry_results=None):
        from .task_record_inputs import source_descriptors

        ledger = frozen_entry_results
        if ledger is None and entry_results is not None:
            ledger = entry_results.freeze()
        # Production receives frozen ledger rows and only O(sources) metadata.
        # The legacy no-ledger adapter below retains its explicit copy boundary.
        frozen = source_descriptors(outcomes) if ledger is not None else self._legacy_inputs(outcomes)
        self._sequence += 1
        self.pending = (
            self._sequence,
            frozen,
            state,
            bool(self.session.applied_keys) if hasattr(self.session, "applied_keys") else self.session.completed,
            self.session.saved,
            bool(getattr(self.session, "project_saved", self.session.saved)),
            bool(getattr(self.session, "snapshot_saved", self.session.saved)),
            ledger,
        )
        self.last_request = self.pending
        if self.worker is None:
            self._start()

    @staticmethod
    def _legacy_inputs(outcomes):
        """Compatibility for callers without a ledger; never copy collections."""
        return tuple(
            replace(
                item,
                task=replace(
                    item.task,
                    collection=None,
                    translate_entries=tuple(copy(entry) for entry in item.task.translate_entries),
                    polish_entries=tuple(copy(entry) for entry in item.task.polish_entries),
                ),
                translation=(
                    SimpleNamespace(
                        post_process_result=getattr(item.translation, "post_process_result", None),
                        failed_entries=tuple(getattr(item.translation, "failed_entries", ()) or ()),
                        failed_count=getattr(item.translation, "failed_count", 0),
                    )
                    if item.translation is not None
                    else None
                ),
                polish={key: copy(value) for key, value in item.polish.items()},
            )
            for item in outcomes
        )

    def retry(self):
        if self.last_request is not None and self.worker is None:
            self.pending = self.last_request
            self._start()

    def _start(self):
        from transbridge.application.translation.task_history import TaskHistoryStore

        from .source_execution import build_task_record

        sequence, outcomes, state, applied, saved, project_saved, snapshot_saved, entry_results = self.pending
        self.pending = None
        self.status, self.error = "正在保存任务记录", ""
        self.changed.emit()
        history_dir = getattr(self.session, "history_dir", None)
        version_label = getattr(self.session, "version_label", "")
        report_request, created_at = self._report_request, self.created_at

        def persist():
            report_outcomes = outcomes
            if entry_results is not None:
                from .task_record_inputs import ledger_outcomes

                report_outcomes = ledger_outcomes(outcomes, entry_results, cancelled=state == "cancelled")
            record = build_task_record(report_outcomes, report_request, state=state, applied=applied, saved=saved)
            record["created_at"] = created_at
            record["project_saved"] = project_saved
            record["snapshot_saved"] = snapshot_saved
            record["version_label"] = version_label
            try:
                if history_dir is None:
                    raise RuntimeError("项目目录不可用")
                TaskHistoryStore(history_dir).save(record)
            except Exception as exc:
                return record, str(exc)
            return record, ""

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
