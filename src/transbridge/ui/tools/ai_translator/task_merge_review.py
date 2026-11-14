# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Review durable AI candidates and restore overwritten current values without AI calls."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.application.tasks.result_application import ResultApplication, prior_merge_plan
from transbridge.application.tasks.storage_contracts import ApplicationState
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.version_persistence import VersionPersistence

from .task_result_preparation import TaskResultPreparation
from .task_review_snapshot import freeze_current_reads, prepare_review, review_stamp, saved_state as saved_state
from .version_operations import require_success


class TaskMergeReview(QObject):
    changed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, ctx, directory, task_id, parent=None, *, store=None):
        super().__init__(parent)
        self.ctx, self.task_id = ctx, task_id
        self.identity = None
        task_store = getattr(ctx, "task_store", None)
        if task_store is not None:
            self.store = store or task_store.recovery()
            self.directory = task_store.run_directory(task_id)
        else:
            self.directory = directory
            self.store = store or TaskRecoveryStore(directory / "ai-task-recovery")
        self._owns_store = store is None
        self.applications = (
            ResultApplication.for_commands(self.store, ctx.project_commands) if task_store is not None else None
        )
        self.preparation = TaskResultPreparation(self)
        self.preparation.changed.connect(self.changed)
        self.preparation.changed.connect(self._release_closed)
        self.rows, self.current, self.previous = {}, {}, {}
        self.record = None
        self.error = ""
        self.shutting_down = False
        self.closed = False
        self.evidence_writer = None
        self._confirmation = None
        self._finish_confirmation = None
        self._read_stamp = None

    @property
    def busy(self):
        return self.preparation.busy

    @property
    def confirmation_finish_pending(self):
        return self._finish_confirmation is not None

    def refresh(self):
        if self.busy or self.shutting_down or self.closed:
            return
        finish = self._finish_confirmation
        stamp = review_stamp(self.ctx)
        reads = freeze_current_reads(self.ctx)
        self._read_stamp = None
        self.current = {}

        def read():
            if finish is not None:
                finish()
            elif self.evidence_writer is not None:
                self.evidence_writer.retry_receipts()
            record = self.store.load(self.task_id)
            if self._owns_store:
                self.store.acquire(record.get("origin_task_id") or self.task_id)
            payload = self.store.load_results(self.task_id)
            if self.applications is not None:
                self.applications.list(self.task_id)
            evidence = []
            for run_id in record.get("execution_runs", (self.task_id,)):
                evidence.extend(TaskMergeEvidenceStore(self.directory / "ai-task-recovery", run_id).load())
            return prepare_review(record, payload, evidence, reads, stamp[0])

        def ready(value):
            if self.closed or self.shutting_down:
                return
            if finish is not None:
                self._confirmation = self._finish_confirmation = None
            self.record, self.rows, self.previous, current = value
            self.identity = (self.record["project_id"], self.record["variant_id"])
            self.current = {}
            if self.ctx.active_version_identity != self.identity:
                self.error = "请返回任务对应的工程版本后处理结果；已保存的成果仍可查看。"
            elif review_stamp(self.ctx) != stamp:
                self.error = "读取期间工程内容已变化，请刷新当前内容后再处理结果。"
            else:
                self.error = ""
                self.current = current
                self._read_stamp = stamp
            self.changed.emit()

        self.preparation.start(read, on_success=ready, on_error=self._failed)

    def _failed(self, error):
        self.error = f"合入处理已完成，结果记录补记失败。{error}" if self.confirmation_finish_pending else str(error)
        self.failed.emit(self.error)
        self.changed.emit()

    def finish_before_close(self, on_success):
        finish = self._finish_confirmation
        assert finish is not None

        def recorded(_):
            self._confirmation = self._finish_confirmation = None
            on_success()

        self.preparation.start(finish, on_success=recorded, on_error=self._failed)

    def resolve(self, keys, *, restore=False, keep=False):
        if self.busy or self.shutting_down or self.closed:
            return
        if self._finish_confirmation is not None:
            self.refresh()
            return
        if self.record is None or self.ctx.active_version_identity != self.identity:
            self._failed("请返回任务对应的工程版本后处理结果。")
            return
        if self._read_stamp is None:
            self._failed("工程内容已变化，请刷新当前内容后再处理结果。")
            return
        selected = {key: dict(self.rows[key]) for key in keys}
        if not selected:
            return
        self.error = ""
        for row in selected.values():
            row["decision_revision"] = row.get("decision_revision", 0) + 1
        if keep:
            self._confirmation = None

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
        identity = self.identity
        confirmation = (
            identity,
            restore,
            tuple(sorted((key, row.get("candidate_revision")) for key, row in selected.items())),
            tuple(sorted(decisions.items())),
            tuple(sorted(candidates, key=lambda change: change.base.entry_key)),
        )
        if self._confirmation is None or self._confirmation[0] != confirmation:
            self._confirmation = confirmation, uuid4().hex
        operation_id = self._confirmation[1]

        def record(plan):
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
            application = None
            if self.applications is not None:
                application = self.applications.prepare(
                    self.task_id,
                    *identity,
                    tuple(selected.values()),
                    ResultConflictPolicy.PREFER_INCOMING,
                    decision="restore" if restore else "merge",
                    operation_id=operation_id,
                )
                if (prior := prior_merge_plan(application)) is not None:
                    return prior
            plan = require_success(
                operation(application.request.operation_id if application is not None else None)
            ).value
            receipt_started = False

            def finish():
                nonlocal receipt_started
                if not receipt_started:
                    saved = writer.acknowledge(candidates, plan, ResultConflictPolicy.PREFER_INCOMING)
                    receipt_started = True
                    if not saved:
                        raise RuntimeError(writer.error)
                elif writer.error:
                    writer.retry_receipts()
                if application is not None:
                    outcome = self.applications.finish(application, plan)
                    if outcome.state is ApplicationState.ACKNOWLEDGEMENT_PENDING:
                        raise RuntimeError("任务应用回执尚未保存，请重试补记。")
                return record(plan)

            self._finish_confirmation = finish
            return finish()

        def completed(_):
            self._confirmation = self._finish_confirmation = None
            self.refresh()

        self.preparation.start(execute, on_success=completed, on_error=self._failed)

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
