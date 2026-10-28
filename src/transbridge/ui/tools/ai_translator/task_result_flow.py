"""Coordinate private result preparation and guarded GUI publication."""

from __future__ import annotations

from transbridge.application.translation.result_merge import ResultMergePlan

from .task_result_preparation import prepare_application, prepare_merge_receipt, prepare_outcomes


class TaskResultFlow:
    def __init__(self, run):
        self.run = run
        self._consistency, self._request = run.consistency, run.request
        self._restore_record = None

    def prepare(self):
        run = self.run

        def recovered(record):
            # Status persistence may publish a newer metadata-only record while
            # the before snapshot is being captured. Retain this validated payload.
            self._restore_record = record
            run._record()
            if run.cancelled:
                run.preparing = False
                run._finish_cancelled()
                return
            try:
                options = dict(on_success=run._ready, on_error=run._prepare_failed)
                options["background_validate"] = self._validate_background
                run.session.capture_before(**options)
            except Exception as exc:
                run._prepare_failed(str(exc))

        try:
            run.recovery.prepare(on_success=recovered, on_error=run._prepare_failed)
        except Exception as exc:
            run._prepare_failed(str(exc))

    def _validate_background(self):
        if self._consistency is not None:
            self._consistency.require_current(self._request)

    def start(self, tasks):
        run = self.run
        if run.cancelled:
            run.preparing = False
            run._finish_cancelled()
            return

        ledger = run.entries.freeze()
        record = (self._restore_record or run.recovery.record) if run._attempt == 0 else None

        def restore():
            from transbridge.application.translation.task_result_recovery import restore_recovery_results

            self._validate_background()
            return restore_recovery_results(ledger, tasks, record)

        def ready(value):
            run.preparing = False
            if run.cancelled or run.shutting_down:
                run._finish_cancelled()
                return
            try:
                run.entries, remaining, restored = value
                self._restore_record = None
                if restored:
                    run.logged.emit(f"已恢复 {restored} 条完整处理结果及用户决定")
                if remaining:
                    run._start(remaining)
                else:
                    self._results_ready(run.entries, {})
            except Exception as exc:
                run._prepare_failed(str(exc))

        run.result_preparation.start(restore, on_success=ready, on_error=run._prepare_failed)

    def _results_ready(self, ledger, outcomes):
        run = self.run
        run.entries, run.outcomes = ledger, outcomes
        run._record()
        run.completion_received = True
        run.progress_view.finish()
        run.progress = (len(run.session.tasks), len(run.session.tasks), "已处理 %v/%m 个来源")
        run.logged.emit("处理结束：" + run.entries.index.summary.counts_text)
        if run.cancelled or run.shutting_down:
            run._finish_cancelled()
        elif run.request.spec.execution_profile.preview_enabled and run.entries.index.pending_preview:
            run.state, run.status = "pending_confirmation", "待确认"
            run._record()
            run.preview_requested.emit()
        else:
            self.apply()
        run.changed.emit()

    def completed(self, outcomes):
        run = self.run
        if run.completion_received or run.worker is None:
            return
        run.completion_received = True
        run.progress_view.finish()
        run.request_state.finish(run.worker)
        run.cancelled |= run.worker.was_cancelled
        run.status = "正在整理处理结果"
        ledger, keys = run.entries.freeze(), frozenset(run._attempt_keys)
        tasks, previous, cancelled = tuple(run.session.tasks), dict(run.outcomes), run.cancelled

        def prepared(value):
            self._results_ready(*value)

        def operation():
            return prepare_outcomes(ledger, outcomes, keys, tasks, previous, cancelled)

        run.result_preparation.start(
            operation,
            on_success=prepared,
            on_error=run._apply_failed,
        )
        run.changed.emit()

    def apply(self, preview=None):
        run = self.run
        if (
            run.session.completed
            or run.cancelled
            or run.shutting_down
            or run.blocked
            or run.applying
            or run.result_preparation.busy
            or run.session.is_busy
            or (run.worker is not None and not run.completion_received)
        ):
            return
        try:
            run._require_current()
            if self._consistency is not None:
                run.applying = True
                run.state, run.status = "applying", "正在校验任务输入"
                run.result_preparation.start(
                    self._validate_background,
                    on_success=lambda _: self._validated_apply(preview),
                    on_error=run._apply_failed,
                )
            else:
                self._validated_apply(preview)
        except Exception as exc:
            run._apply_failed(str(exc))
        run.changed.emit()

    def _validated_apply(self, preview):
        run = self.run
        run.applying = False
        if run.cancelled or run.shutting_down:
            run._finish_cancelled()
            return
        try:
            run._require_current()
            if run.request.spec.execution_profile.preview_enabled and run.entries.index.pending_preview:
                if preview is None:
                    run.state, run.status = "pending_confirmation", "待确认"
                    run._record()
                    run.preview_requested.emit()
                    run.changed.emit()
                    return
            if preview is not None and not preview():
                run.cancel()
                return
            if run.cancelled or run.shutting_down:
                return
            run._require_current()
            ledger, tasks = run.entries.freeze(), tuple(run.session.tasks)
            run.applying = True
            run.state, run.status = "applying", "正在准备应用结果"

            def operation():
                self._validate_background()
                result = prepare_application(ledger, tasks)
                self._validate_background()
                return result

            run.result_preparation.start(operation, on_success=self._apply_prepared, on_error=run._apply_failed)
        except Exception as exc:
            run._apply_failed(str(exc))
        run.changed.emit()

    def _apply_prepared(self, value):
        run = self.run
        ledger, selected = value
        # Deliver model work before any filesystem operation can fail. Retrying
        # this barrier reuses the ledger and never dispatches the model again.
        run.entries = ledger

        def durable(_):
            self._commit_prepared(selected)

        if run.recovery.store is not None:
            from transbridge.application.translation.task_result_recovery import save_recovery_results

            run.result_preparation.start(
                lambda: save_recovery_results(run.recovery.store, run.recovery.record["task_id"], ledger),
                on_success=durable,
                on_error=run._apply_failed,
            )
        else:
            durable(None)

    def _commit_prepared(self, selected):
        run = self.run
        if run.cancelled or run.shutting_down:
            run.applying = False
            run._finish_cancelled()
            return
        try:
            run._require_current()
            if not selected:
                run.completion.applied(selected, prepared=True)
                return
            options = dict(
                on_success=lambda result: self.applied(selected, result),
                on_error=run._apply_failed,
                validate=run._require_current,
            )
            options["background_validate"] = self._validate_background
            run.session.apply_entries_async(selected, **options)
        except Exception as exc:
            run._apply_failed(str(exc))
        run._record()
        run.changed.emit()

    def applied(self, selected, result=None):
        run = self.run
        if not isinstance(result, ResultMergePlan):
            raise TypeError("合入未返回有效回执；任务成果已保留。")
        ledger = run.entries.freeze()

        def delivered(value):
            run.entries = value
            try:
                run.completion.applied(selected, prepared=True)
            except Exception as exc:
                run._apply_failed(str(exc))

        selected = {row.entry_key for row in result.applied} | set(result.unchanged_keys)

        def prepare():
            return prepare_merge_receipt(ledger, result)

        run.result_preparation.start(prepare, on_success=delivered, on_error=run._apply_failed)

    def retry(self):
        run = self.run
        if not run.can_retry or run.shutting_down:
            return
        try:
            run._require_current()
            keys = run.entries.failed_keys
            run.preparing = True
            run.status = "正在准备重试条目"

            def ready(tasks):
                if run.cancelled or run.shutting_down:
                    run.preparing = False
                    run._finish_cancelled()
                    return
                for task in tasks:
                    run.rows[task.key] = ("等待", f"{len(task.entries)} 条")
                self.start(tasks)

            run.session.retry_entries_async(keys, on_success=ready, on_error=run._prepare_failed)
        except Exception as exc:
            run.preparing = False
            run.status = f"无法重试：{exc}"
            run.logged.emit("重试未能开始")
        run.changed.emit()
