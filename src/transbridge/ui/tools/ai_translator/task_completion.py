"""Coordinate durable completion and user notifications after result publication."""

from PyQt6.QtCore import QObject, QTimer

from .task_run_presentation import summarize_entries


class TaskCompletion(QObject):
    def __init__(self, run):
        super().__init__(run)
        self.run = run
        self.pending = None
        self._last_notice = None
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._deliver)

    def reset(self):
        self._timer.stop()
        self.pending = None

    def applied(self, selected):
        run = self.run
        run.applying = False
        if run.cancelled or run.shutting_down:
            return
        if selected:
            run.entries.mark_applied(selected)
            run.logged.emit(f"已应用 {len(selected)} 条结果")
            run.translation_completed.emit()
        if run.entries.failed_keys:
            run.state, run.status = "partial", "处理结束 · 有失败条目"
        else:
            run.session.finish()
            run.state, run.status = "completed", "处理结束"
            run.activity.finish(cancelled=False)
            if run.worker is None:
                run._close_client()
        run._record()
        run.changed.emit()
        if run.session.can_save:
            self.save()
        else:
            self.notify()

    def save(self):
        run = self.run
        if run.shutting_down or run.session.is_busy or not run.session.can_save:
            return
        run.save_status = "正在保存项目"
        run.changed.emit()

        def saved(_):
            run.snapshot_failed = False
            run.save_status = "项目及快照已保存"
            if not run.cancelled:
                run.status = "已完成 · 项目已保存" if run.session.completed else "成功结果已保存 · 可重试失败条目"
            run._record()
            run.changed.emit()
            self.notify()

        def failed(error):
            run.snapshot_failed = bool(getattr(run.session, "project_saved", False))
            prefix = "项目已保存 · 快照失败" if run.snapshot_failed else "项目保存失败"
            run.save_status = f"{prefix}：{error}"
            run._record()
            run.changed.emit()
            self.notify(error=str(error))

        try:
            run.session.save_translation(on_success=saved, on_error=failed)
        except Exception as exc:
            failed(str(exc))

    def notify(self, *, error=""):
        self.pending = (self.run._attempt, error)
        self._timer.start()

    def _deliver(self):
        run = self.run
        if run.session.is_busy or run.worker is not None or run.records.busy:
            return
        self._timer.stop()
        pending, self.pending = self.pending, None
        run.changed.emit()
        if pending is None or pending[0] != run._attempt or run.cancelled or run.shutting_down:
            return
        error = pending[1]
        summary = summarize_entries(run.entries.entries)
        saved = bool(getattr(run.session, "project_saved", False))
        if error:
            title = "项目已保存，但快照创建失败" if saved else "项目保存失败"
        elif saved:
            title = "处理结束，成功结果已保存" if summary.failed else "任务已完成，项目已保存"
        else:
            title = "处理结束，没有应用新结果"
        review = sum(row.applied and row.stage == 2 for row in run.entries.entries.values())
        text = f"已应用 {summary.applied} 条，其中有疑问 {review} 条。"
        if summary.rejected:
            text += f"\n未采纳 {summary.rejected} 条。"
        if summary.failed:
            text += f"\n失败 {summary.failed} 条：" + "；".join(
                f"{reason} {count} 条" for reason, count in summary.reasons
            )
        if error:
            text += f"\n{error}"
        notice = (run._attempt, title, text)
        if notice != self._last_notice:
            self._last_notice = notice
            run.completion_notice.emit({"title": title, "text": text, "error": bool(error)})
