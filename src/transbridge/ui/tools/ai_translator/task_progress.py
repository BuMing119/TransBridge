"""A reopenable view of an independently owned AI task."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.windowing import show_and_activate

from ._theme_support import AiThemeBinding
from .task_run import AiTaskRun
from .task_run_presentation import source_label
from .task_widget_style import configure_task_button, configure_task_host


class AiTaskProgressWindow(QWidget):
    translation_completed = pyqtSignal()

    def __init__(self, request, session, activity, *, client=None, project_id=None, theme_view=None, consistency=None):
        super().__init__(None, Qt.WindowType.Window)
        self.run = AiTaskRun(request, session, activity, client=client, project_id=project_id, consistency=consistency)
        self.request, self.session, self.activity = request, session, activity
        self._theme_view = theme_view
        self._dialogs = []
        self._preview_open = False
        self._last_selected = None
        self.setWindowTitle("AI 翻译任务")
        self.resize(1080, 680)
        configure_task_host(self)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        layout.addWidget(self.bar)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["处理内容", "阶段", "进度 / 结果"])
        self.sources.setRootIsDecorated(False)
        self.rows = {}
        for task in session.tasks:
            row = QTreeWidgetItem([source_label(session.tasks, task.key), "等待", f"{len(task.entries)} 条"])
            row.setData(0, Qt.ItemDataRole.UserRole, task.key)
            self.sources.addTopLevelItem(row)
            self.rows[task.key] = row
        self.sources.setColumnWidth(0, 290)
        layout.addWidget(self.sources)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(3000)
        layout.addWidget(self.logs, 1)
        self.result_summary = QLabel()
        self.result_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)
        self.record_status = QLabel()
        self.record_status.setWordWrap(True)
        self.save_status = QLabel()
        self.save_status.setWordWrap(True)
        layout.addWidget(self.record_status)
        layout.addWidget(self.save_status)
        buttons = QHBoxLayout()
        for name, label, callback in (
            ("pause_button", "暂停", self.run.toggle_pause),
            ("stop_button", "取消任务", self._stop),
            ("retry_button", "重试失败条目", self.run.retry),
            ("preview_button", "确认结果", self._confirm),
            ("log_button", "LLM 日志", self._open_log),
            ("report_button", "查看结果", self._open_report),
            ("save_button", "保存项目", self.run.save),
        ):
            button = QPushButton(label)
            configure_task_button(button, primary=name == "save_button")
            button.clicked.connect(callback)
            buttons.addWidget(button)
            setattr(self, name, button)
        layout.addLayout(buttons)
        self.retry_record_button = QPushButton("重试保存任务记录")
        self.retry_record_button.clicked.connect(self.run.records.retry)
        layout.addWidget(self.retry_record_button)
        self._theme = AiThemeBinding(self, theme_view, lambda _: None)
        self.run.changed.connect(self._refresh)
        self.run.logged.connect(self.logs.appendPlainText)
        self.run.preview_requested.connect(self._offer_preview)
        self.run.translation_completed.connect(self.translation_completed)
        self.run.completion_notice.connect(self._show_completion)
        self._refresh()

    def prepare(self):
        self.run.prepare()

    def _refresh(self):
        run = self.run
        self.status.setText(run.status)
        self.result_summary.setText(run.result_summary)
        self.result_summary.setVisible(bool(run.result_summary))
        self.record_status.setText(run.records.status)
        self.save_status.setText(run.save_status)
        self.retry_record_button.setVisible(bool(run.records.error))
        self.retry_record_button.setEnabled(not run.records.busy)
        current, total, pattern = run.progress
        self.bar.setRange(0, total)
        self.bar.setValue(current)
        self.bar.setFormat(pattern)
        for key, (phase, detail) in run.rows.items():
            self.rows[key].setText(1, phase)
            self.rows[key].setText(2, detail)
        if self._last_selected != run.selected_key and run.selected_key in self.rows:
            self.sources.setCurrentItem(self.rows[run.selected_key])
            self._last_selected = run.selected_key
        self.pause_button.setEnabled(run.worker is not None and not run.completion_received and not run.cancelled)
        self.pause_button.setText("继续" if run.pause_state != "running" else "暂停")
        self.stop_button.setText("取消任务" if run.running and not run.completion_received else "关闭")
        self.stop_button.setEnabled(not run.cancelled or not run.running)
        self.retry_button.setEnabled(run.can_retry)
        self.preview_button.setText("确认结果" if run.state == "pending_confirmation" else "应用成功结果")
        self.preview_button.setVisible(run.state == "pending_confirmation" or bool(run.entries.ready_keys))
        self.preview_button.setEnabled(run.can_apply_success and not self._preview_open)
        self.report_button.setEnabled(run.records.record is not None)
        retry_snapshot = getattr(self.session, "project_saved", False) and not self.session.saved
        self.save_button.setText(("重试快照" if run.snapshot_failed else "创建快照") if retry_snapshot else "保存项目")
        self.save_button.setEnabled(self.session.can_save and not self.session.is_busy)

    def _offer_preview(self):
        if self.isVisible() and not self.isMinimized():
            self._confirm()

    def _confirm(self):
        if self._preview_open or self.run.state not in {"pending_confirmation", "failed", "partial"}:
            return
        self._preview_open = True
        try:
            preview = self._apply_preview if self.request.spec.execution_profile.preview_enabled else None
            self.run.apply_results(preview)
        finally:
            self._preview_open = False
            self._refresh()

    def _apply_preview(self):
        from ._polish_preview_dialog import _PolishPreviewDialog

        decisions = {}
        ready = self.run.entries.ready_keys
        for outcome in self.run.outcomes.values():
            entries = [
                entry
                for entry in outcome.task.polish_entries
                if entry.identity in ready and self.run.entries.entries[entry.identity].decision == "pending"
            ]
            if not entries:
                continue
            dialog = _PolishPreviewDialog(entries, outcome.polish, parent=self, theme_view=self._theme_view)
            dialog.setWindowTitle(f"{outcome.task.label} · 校改结果")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            chosen = dialog.get_results()
            decisions.update({entry.identity: chosen.get(entry.id) is not None for entry in entries})
        self.run.entries.decide(decisions)
        return True

    def _selected_key(self):
        row = self.sources.currentItem()
        return None if row is None else row.data(0, Qt.ItemDataRole.UserRole)

    def _open_report(self):
        from .task_result_dialog import TaskResultDialog

        record = self.run.records.record
        if record is None:
            return
        dialog = TaskResultDialog(record, source_key=self._selected_key(), theme_view=self._theme_view)
        self._dialogs.append(dialog)
        show_and_activate(dialog)

    def _open_log(self):
        from ._llm_log_viewer import _LLMLogViewer

        outcome = self.run.outcomes.get(self._selected_key())
        path = self.run.log_paths.get(self._selected_key())
        if self._selected_key() not in self.run.log_paths and outcome is not None:
            path = outcome.log_dir
        if path:
            dialog = _LLMLogViewer(path)
            self._dialogs.append(dialog)
            show_and_activate(dialog)
        else:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("LLM 日志")
            dialog.setText("日志不可用，请检查日志目录权限。" if path == "" else "正在等待日志目录创建，请稍后打开。")
            self._dialogs.append(dialog)
            dialog.open()

    def _show_completion(self, notice):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(notice["title"])
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(notice["text"])
        dialog.setIcon(QMessageBox.Icon.Warning if notice["error"] else QMessageBox.Icon.Information)
        view = dialog.addButton("查看结果", QMessageBox.ButtonRole.ActionRole)
        view.clicked.connect(self._open_report)
        if notice["error"] and self.session.can_save:
            retry = dialog.addButton(
                "重试快照" if self.run.snapshot_failed else "重试保存", QMessageBox.ButtonRole.ActionRole
            )
            retry.clicked.connect(self.run.save)
        if self.run.can_retry:
            retry = dialog.addButton("重试失败条目", QMessageBox.ButtonRole.ActionRole)
            retry.clicked.connect(self.run.retry)
        dialog.addButton("关闭", QMessageBox.ButtonRole.RejectRole)
        self._dialogs.append(dialog)
        dialog.open()

    def _stop(self):
        if self.run.running and not self.run.completion_received:
            self.run.cancel()
        else:
            self.close()

    def is_running(self):
        return self.run.running

    def _prepare_failed(self, error):
        self.run._prepare_failed(error)

    def closeEvent(self, event):
        # Window closure only changes visibility. Registry shutdown owns disposal.
        event.accept()

    def dispose(self):
        self.run.dispose()
        for dialog in self._dialogs:
            dialog.close()
        self._theme.close()
        self.close()
