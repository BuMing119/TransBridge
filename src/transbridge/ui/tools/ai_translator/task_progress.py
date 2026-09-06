"""Unified task progress, source reports and atomic result publication."""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workers import ApiWorker

from ._theme_support import AiThemeBinding
from .task_widget_style import configure_task_button, configure_task_host
from .task_worker import AiTaskWorker

logger = logging.getLogger(__name__)


class AiTaskProgressWindow(QWidget):
    translation_completed = pyqtSignal()

    def __init__(self, request, session, activity, *, client=None, project_id=None, theme_view=None):
        super().__init__(None, Qt.WindowType.Window)
        self.request = request
        self.session = session
        self.activity = activity
        self._client = client
        self._project_id = project_id
        self._theme_view = theme_view
        self._worker = None
        self._completion_received = False
        self._reports_worker = None
        self._outcomes = {}
        self._cancelled = False
        self._preparing = True
        self._dialogs = []
        self.setWindowTitle("AI 翻译任务 · 运行进度")
        self.resize(880, 680)
        configure_task_host(self)
        layout = QVBoxLayout(self)
        self.status = QLabel("正在创建执行前版本快照…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, len(session.tasks))
        layout.addWidget(self.bar)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["处理内容", "阶段", "进度 / 结果"])
        self.sources.setRootIsDecorated(False)
        self.rows = {}
        for task in session.tasks:
            row = QTreeWidgetItem([task.label, "等待", f"{len(task.entries)} 条"])
            row.setData(0, Qt.ItemDataRole.UserRole, task.key)
            self.sources.addTopLevelItem(row)
            self.rows[task.key] = row
        self.sources.setColumnWidth(0, 290)
        layout.addWidget(self.sources)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(3000)
        layout.addWidget(self.logs, 1)
        buttons = QHBoxLayout()
        for name, label, callback in (
            ("pause_button", "暂停", self._pause),
            ("stop_button", "取消任务", self._stop),
            ("retry_button", "重试失败插件", self._retry),
            ("log_button", "LLM 日志", self._open_log),
            ("report_button", "查看所选报告", self._open_report),
            ("save_button", "保存翻译", self._save),
        ):
            button = QPushButton(label)
            configure_task_button(button, primary=name == "save_button")
            button.clicked.connect(callback)
            buttons.addWidget(button)
            setattr(self, name, button)
        layout.addLayout(buttons)
        self.pause_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.report_button.setEnabled(False)
        self._theme = AiThemeBinding(self, theme_view, lambda _: None)

    def prepare(self):
        try:
            self.session.capture_before(on_success=self._ready, on_error=self._prepare_failed)
        except Exception as exc:
            self._prepare_failed(str(exc))

    def _ready(self, _result):
        self._preparing = False
        if self._cancelled:
            self._finish_cancelled()
            return
        try:
            self._start(self.session.tasks)
        except Exception as exc:
            self._prepare_failed(str(exc))

    def _prepare_failed(self, error):
        self._preparing = False
        if self._cancelled:
            self._finish_cancelled()
            return
        self.status.setText(f"任务未启动：{error}")
        self.session.rollback_uncommitted()
        self.activity.fail(error)
        self.stop_button.setEnabled(False)

    def _start(self, tasks):
        worker = AiTaskWorker(self.request, tasks, client=self._client, project_id=self._project_id)
        self._worker = worker
        self._completion_received = False
        self.activity.bind_worker(worker)
        worker.source_started.connect(self._source_started)
        worker.progress.connect(self._progress)
        worker.log.connect(lambda key, text: self.logs.append(f"[{self.rows[key].text(0)}] {text}"))
        worker.completed.connect(lambda outcomes: self._completed(outcomes) if self._worker is worker else None)
        worker.finished.connect(lambda: self._worker_finished(worker))
        self.pause_button.setEnabled(True)
        self.pause_button.setText("暂停")
        self.stop_button.setEnabled(True)
        self.retry_button.setEnabled(False)
        self.status.setText(f"正在执行：{self.request.spec.execution_profile.summary}")
        worker.start()

    def _source_started(self, key):
        self.rows[key].setText(1, "执行中")
        self.sources.setCurrentItem(self.rows[key])

    def _progress(self, key, stage, current, total, message):
        self.rows[key].setText(1, stage)
        self.rows[key].setText(2, f"{current}/{total} · {message}")
        self.status.setText(f"{self.rows[key].text(0)} · {stage} · {message}")
        completed = sum(outcome.successful for outcome in self._outcomes.values())
        self.activity.progress(completed, len(self.session.tasks), self.status.text())

    def _completed(self, outcomes):
        if self._completion_received or self._worker is None:
            return
        self._completion_received = True
        self._cancelled |= self._worker.was_cancelled
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        for outcome in outcomes:
            self._outcomes[outcome.task.key] = outcome
            row = self.rows[outcome.task.key]
            row.setText(1, "完成" if outcome.successful else "失败")
            row.setText(2, outcome.error or f"{len(outcome.failed_keys)} 条失败")
        successful = sum(outcome.successful for outcome in self._outcomes.values())
        self.bar.setValue(successful)
        complete = len(self._outcomes) == len(self.session.tasks) and successful == len(self.session.tasks)
        if self._cancelled:
            self._finish_cancelled()
        elif not complete:
            self.status.setText("部分插件失败，整个任务尚未提交。可重试失败插件，成功结果保留在任务中。")
            # Keep the task activity active while recovery is available.
            self.activity.progress(successful, len(self.session.tasks), self.status.text())
            self.retry_button.setEnabled(True)
        else:
            try:
                if not self._apply_preview():
                    self._cancelled = True
                    self._finish_cancelled()
                else:
                    self.session.mark_completed()
                    self.status.setText("任务完成，所有来源结果已统一应用。可保存翻译及版本快照。")
                    self.save_button.setEnabled(True)
                    self.activity.finish(cancelled=False)
                    self.translation_completed.emit()
            except Exception as exc:
                self.status.setText(f"结果未提交：{exc}")
                self.session.rollback_uncommitted()
                self.activity.fail(str(exc))
                self.logs.append(str(exc))
        self._render_reports()

    def _worker_finished(self, worker):
        if self._worker is worker:
            if not self._completion_received:
                from .source_execution import SourceOutcome

                self._completed(
                    tuple(SourceOutcome(task, error="AI 工作线程异常结束，请重试。") for task in worker.tasks)
                )
            self._worker = None
        worker.deleteLater()

    def _finish_cancelled(self):
        self.status.setText("任务已取消，翻译内容未提交。")
        self.session.rollback_uncommitted()
        self.activity.request_cancel()
        self.activity.finish(cancelled=True)
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.retry_button.setEnabled(False)

    def _apply_preview(self):
        from ._polish_preview_dialog import _PolishPreviewDialog
        from .result_presenter import ResultPresenter

        presenter = ResultPresenter()
        # No authoritative writes occur until every source preview has been confirmed.
        for outcome in self._outcomes.values():
            entries = list(outcome.task.polish_entries)
            if not entries:
                continue
            if self.request.spec.execution_profile.preview_enabled:
                dialog = _PolishPreviewDialog(entries, outcome.polish, parent=self, theme_view=self._theme_view)
                dialog.setWindowTitle(f"{outcome.task.label} · 校改结果预览")
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return False
                outcome.polish_summary = presenter.apply_decisions(
                    outcome.task.collection, entries, dialog.get_results(), results=outcome.polish
                )
            else:
                outcome.polish_summary = presenter.apply_direct(outcome.task.collection, entries, outcome.polish)
        return True

    def _render_reports(self):
        from .source_execution import render_source_report

        self.report_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        outcomes = tuple(self._outcomes.values())

        def render():
            errors = []
            for outcome in outcomes:
                try:
                    render_source_report(outcome, self.request)
                except Exception as exc:
                    errors.append(f"{outcome.task.label} 报告生成失败：{exc}")
            return errors

        worker = ApiWorker(render, route_http_errors=False)
        self._reports_worker = worker
        worker.result.connect(lambda errors: self.logs.append("\n".join(errors)) if errors else None)
        worker.error.connect(self.logs.append)

        def finished():
            self._reports_worker = None
            self.report_button.setEnabled(True)
            incomplete = len(self._outcomes) < len(self.session.tasks) or any(
                not outcome.successful for outcome in self._outcomes.values()
            )
            self.retry_button.setEnabled(not self._cancelled and not self.session.completed and incomplete)
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()

    def _selected_outcome(self):
        row = self.sources.currentItem()
        return None if row is None else self._outcomes.get(row.data(0, Qt.ItemDataRole.UserRole))

    def _open_report(self):
        from ._translation_report_dialog import _TranslationReportDialog

        outcome = self._selected_outcome()
        if outcome is None:
            return
        dialog = _TranslationReportDialog(
            snapshot=outcome.snapshot,
            report_path=getattr(outcome.report, "excel_path", None),
            theme_view=self._theme_view,
        )
        state = "" if self.session.completed else "未提交 · "
        dialog.setWindowTitle(f"{outcome.task.label} · {state}AI 任务报告")
        self._dialogs.append(dialog)
        show_and_activate(dialog)

    def _open_log(self):
        from ._llm_log_viewer import _LLMLogViewer

        outcome = self._selected_outcome()
        if outcome is not None and outcome.log_dir:
            dialog = _LLMLogViewer(outcome.log_dir)
            self._dialogs.append(dialog)
            show_and_activate(dialog)

    def _save(self):
        self.save_button.setEnabled(False)

        def saved(_):
            self.status.setText("翻译已保存，保存后版本快照已创建。")

        def failed(error):
            self.status.setText(f"保存失败，可重试：{error}")
            self.save_button.setEnabled(self.session.can_save)

        try:
            self.session.save_translation(on_success=saved, on_error=failed)
        except Exception as exc:
            failed(str(exc))

    def _pause(self):
        if self._worker is None:
            return
        if self._worker.is_paused:
            self._worker.resume()
            self.activity.resume()
            self.pause_button.setText("暂停")
        else:
            self._worker.pause()
            self.activity.pause()
            self.pause_button.setText("继续")

    def _stop(self):
        self._cancelled = True
        self.session.rollback_uncommitted()
        self.activity.request_cancel()
        if self._worker is not None:
            self._worker.stop()
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.status.setText("正在取消任务，翻译内容不会提交。")

    def _retry(self):
        if self.is_running() or self._reports_worker is not None or self._cancelled or self.session.completed:
            return
        tasks = [
            task
            for task in self.session.tasks
            if not self._outcomes.get(task.key) or not self._outcomes[task.key].successful
        ]
        try:
            keys = {task.key for task in tasks}
            self.session.reset_sources(keys)
            self._start(tuple(task for task in self.session.tasks if task.key in keys))
        except Exception as exc:
            self.status.setText(f"重试失败：{exc}")
            self.retry_button.setEnabled(False)
            self.session.rollback_uncommitted()
            self.activity.fail(str(exc))

    def is_running(self):
        return self._preparing or (self._worker is not None and self._worker.isRunning())

    def _close_client(self):
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.exception("AI task remote client cleanup failed")
                self.logs.append(f"远端客户端关闭失败：{exc}")

    def closeEvent(self, event):
        if self.is_running() or self._reports_worker is not None or self.session.is_busy:
            QMessageBox.information(self, "任务仍在处理", "任务正在后台处理。可最小化窗口；需要结束时先取消任务。")
            event.ignore()
            return
        if not self.session.completed:
            self.activity.request_cancel()
            self.activity.finish(cancelled=True)
            self.session.rollback_uncommitted()
        self._close_client()
        self._theme.close()
        event.accept()
