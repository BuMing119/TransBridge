"""Review durable AI candidates and restore overwritten current values without AI calls."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.version_persistence import VersionPersistence

from .task_result_preparation import TaskResultPreparation
from .task_session_merge import entry_state
from .version_operations import require_success


def saved_state(value):
    return ResultEntryState(
        EntryKey.from_dict(value["entry_key"]),
        value["original"],
        value["translation"],
        value["stage"],
        value.get("context", ""),
        value.get("deleted", False),
        value.get("source_locator"),
    )


class TaskMergeReview(QObject):
    changed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, ctx, directory, task_id, parent=None, *, store=None):
        super().__init__(parent)
        self.ctx, self.directory, self.task_id = ctx, directory, task_id
        self.identity = None
        self.store = store or TaskRecoveryStore(directory / "ai-task-recovery")
        self._owns_store = store is None
        self.preparation = TaskResultPreparation(self)
        self.preparation.changed.connect(self.changed)
        self.preparation.changed.connect(self._release_closed)
        self.rows, self.current, self.previous = {}, {}, {}
        self.record = None
        self.error = ""
        self.shutting_down = False
        self.closed = False
        self.evidence_writer = None

    @property
    def busy(self):
        return self.preparation.busy

    def refresh(self):
        if self.busy or self.shutting_down or self.closed:
            return

        def read():
            if self.evidence_writer is not None:
                self.evidence_writer.retry_receipts()
            record = self.store.load(self.task_id)
            if self._owns_store:
                self.store.acquire(record.get("origin_task_id") or self.task_id)
            payload = self.store.load_results(self.task_id)
            evidence = []
            for run_id in record.get("execution_runs", (self.task_id,)):
                evidence.extend(TaskMergeEvidenceStore(self.directory / "ai-task-recovery", run_id).load())
            return record, payload, evidence

        def ready(value):
            self.record, payload, evidence = value
            self.identity = (self.record["project_id"], self.record["variant_id"])
            self.rows = {EntryKey.from_dict(row["entry_key"]): row for row in (payload or {}).get("rows", ())}
            self.previous = {}
            for item in evidence:
                applied = {EntryKey.from_dict(key) for key in item.get("applied_keys", ())}
                for conflict in item["conflicts"]:
                    key = EntryKey.from_dict(conflict["entry_key"])
                    if key in applied and conflict["current"] is not None and conflict["decision"] == "prefer_incoming":
                        # File order is presentation chronology only. The actual
                        # write is authorized separately by decision_states.
                        self.previous.setdefault(key, []).insert(0, saved_state(conflict["current"]))
            self.current = {}
            if self.ctx.active_version_identity == self.identity:
                self.error = ""
                for key in self.rows:
                    for slot in self.ctx.slots.values():
                        entry = slot.collection.get(key)
                        if entry is not None:
                            self.current[key] = entry_state(entry)
                            break
            else:
                self.error = "请返回任务对应的工程版本后处理结果；已保存的成果仍可查看。"
            self.changed.emit()

        self.preparation.start(read, on_success=ready, on_error=self._failed)

    def _failed(self, error):
        self.error = str(error)
        self.failed.emit(self.error)
        self.changed.emit()

    def resolve(self, keys, *, restore=False, keep=False):
        if self.busy or self.shutting_down or self.closed:
            return
        if self.record is None or self.ctx.active_version_identity != self.identity:
            self._failed("请返回任务对应的工程版本后处理结果。")
            return
        selected = {key: dict(self.rows[key]) for key in keys}
        if not selected:
            return
        self.error = ""
        for row in selected.values():
            row["decision_revision"] = row.get("decision_revision", 0) + 1
        if keep:

            def operation():
                for row in selected.values():
                    row.update(decision="rejected", applied=False, merge_status="kept_current", merge_reason="")
                self.store.save_results(self.task_id, list(selected.values()))

            self.preparation.start(operation, on_success=lambda _: self.refresh(), on_error=self._failed)
            return
        candidates = []
        for key, row in selected.items():
            before = row["before"]
            base = ResultEntryState(key, before["original"], before["translation"], before["stage"], before["context"])
            incoming = replace(base, translation=row["text"], stage=row["stage"])
            if restore:
                previous = self.previous.get(key)
                if not previous or key not in self.current:
                    self._failed("该词条没有可恢复的覆盖前译文。")
                    return
                base, incoming = self.current[key], previous[0]
            candidates.append(ResultEntryChange(base, incoming))
        candidates = tuple(candidates)
        writer = TaskMergeEvidenceStore(self.directory / "ai-task-recovery", self.task_id)
        self.evidence_writer = writer
        persistence = VersionPersistence(self.ctx, self.identity)
        decisions = {key: self.current[key] for key in selected if key in self.current}

        def record(plan):
            writer.acknowledge(candidates, plan, ResultConflictPolicy.PREFER_INCOMING)
            if writer.error:
                self.error = writer.error
            applied = {row.entry_key for row in plan.applied} | set(plan.unchanged_keys)
            pending = {row.entry_key: row.reason for row in plan.pending}
            for key, row in selected.items():
                row.update(
                    applied=not restore and key in applied,
                    decision="rejected" if restore and key in applied else "accepted",
                    merge_status="pending" if key in pending else "restored" if restore else "applied",
                    merge_reason=pending.get(key, ""),
                )
            self.store.save_results(self.task_id, list(selected.values()))
            return plan

        # Frozen ports contain no Qt objects; facade publication uses the
        # established ProjectionStore -> AppContext.safe_mutate handoff.
        operation = persistence.freeze_merge_translation(
            candidates,
            ResultConflictPolicy.PREFER_INCOMING,
            before_commit=lambda plan: writer.save(candidates, plan, ResultConflictPolicy.PREFER_INCOMING),
            decision_states=decisions,
        )

        def execute():
            return record(require_success(operation()).value)

        self.preparation.start(execute, on_success=lambda _: self.refresh(), on_error=self._failed)

    def shutdown(self):
        self.shutting_down = True
        self.changed.emit()

    def resume_exports(self):
        self.shutting_down = False
        self.changed.emit()

    def close(self):
        self.closed = True
        self._release_closed()

    def _release_closed(self):
        if self.closed and not self.busy and self._owns_store:
            self.store.release()
