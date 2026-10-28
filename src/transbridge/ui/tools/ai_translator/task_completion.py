"""Coordinate durable completion and user notifications after result publication."""

from PyQt6.QtCore import QObject, QTimer


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

    def applied(self, selected, *, prepared=False):
        run = self.run
        run.applying = False
        if selected:
            if not prepared:
                run.entries.mark_applied(selected)
            run.logged.emit(f"已应用 {len(selected)} 条结果")
            run.translation_completed.emit()
        run.runtime_binding.synchronize()
        if run.cancelled or run.shutting_down:
            run._finish_cancelled()
            return
        if run.entries.failed_keys:
            run.state, run.status = "partial", "处理结束 · 有失败条目"
        else:
            run.session.finish()
            pending = sum(row.merge_status == "pending" for row in run.entries.entries.values())
            run.state, run.status = "completed", f"处理结束 · {pending} 条待合入" if pending else "处理结束"
            run.activity.finish(cancelled=False)
            run.runtime_binding.synchronize()
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
            run.save_status = "项目已保存"
            if not run.cancelled:
                pending = run.entries.index.summary.unapplied
                run.status = (
                    f"项目已保存 · {pending} 条结果待合入"
                    if pending
                    else "已完成 · 项目已保存"
                    if run.session.completed
                    else "成功结果已保存 · 可重试失败条目"
                )
            run._record()
            run.changed.emit()
            self.notify()

        def failed(error):
            run.save_status = f"项目保存失败：{error}"
            run._record()
            run.changed.emit()
            self.notify(error=str(error))

        try:
            options = dict(on_success=saved, on_error=failed)
            options["background_validate"] = run.result_flow._validate_background
            run.session.save_translation(**options)
        except Exception as exc:
            failed(str(exc))

    def notify(self, *, error=""):
        self.pending = (self.run._attempt, error)
        self._timer.start()

    def _deliver(self):
        run = self.run
        if run.shutting_down:
            self.reset()
            run.changed.emit()
            return
        if (
            run.session.is_busy
            or run.worker is not None
            or run.records.busy
            or run.recovery.busy
            or run.result_preparation.busy
        ):
            return
        self._timer.stop()
        pending, self.pending = self.pending, None
        run.changed.emit()
        if pending is None or pending[0] != run._attempt or run.cancelled or run.shutting_down:
            return
        error = pending[1]
        summary = run.entries.index.summary
        saved = bool(getattr(run.session, "project_saved", False))
        if error:
            title = "项目已保存，但快照创建失败" if saved else "项目保存失败"
        elif saved:
            title = "处理结束，成功结果已保存" if summary.failed or summary.unapplied else "任务已完成，项目已保存"
        else:
            title = "处理结束，没有应用新结果"
        review = run.entries.index.applied_review
        text = f"已应用 {summary.applied} 条，其中有疑问 {review} 条。"
        if summary.rejected:
            text += f"\n未采纳 {summary.rejected} 条。"
        if summary.failed:
            text += f"\n失败 {summary.failed} 条：" + "；".join(
                f"{reason} {count} 条" for reason, count in summary.reasons
            )
        if summary.unapplied:
            text += f"\n待合入 {summary.unapplied} 条，AI 成果已保留；可在查看结果中逐条处理。"
        if error:
            text += f"\n{error}"
        notice = (run._attempt, title, text)
        if notice != self._last_notice:
            self._last_notice = notice
            run.completion_notice.emit({"title": title, "text": text, "error": bool(error)})
