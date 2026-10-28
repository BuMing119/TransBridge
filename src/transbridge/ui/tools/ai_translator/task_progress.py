"""A reopenable view of an independently owned AI task."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.translation.entry_alias import ai_entry_id
from transbridge.ui.foundation.components import ComponentStyle, SemanticState
from transbridge.ui.windowing import show_and_activate

from ._theme_support import AiThemeBinding
from .task_activity_panel import TaskActivityPanel
from .task_run import AiTaskRun
from .task_run_presentation import source_label
from .task_widget_style import configure_task_button, configure_task_host, configure_task_title


class _SourceProgressTable(QTreeWidget):
    """Reserve space for actual sources, with scrolling for large projects."""

    def __init__(self):
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setUniformRowHeights(True)

    def sizeHint(self):
        row_height = self.sizeHintForRow(0)
        if row_height <= 0:
            row_height = self.fontMetrics().height() + 8
        rows = min(max(self.topLevelItemCount(), 1), 6)
        height = self.header().sizeHint().height() + rows * row_height + 2 * self.frameWidth()
        if self.horizontalScrollBar().isVisible():
            height += self.horizontalScrollBar().sizeHint().height()
        return QSize(super().sizeHint().width(), height)

    def minimumSizeHint(self):
        return QSize(0, self.sizeHint().height())


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
        self._primary_button = None
        self.setWindowTitle("AI 翻译任务")
        self.resize(1080, 680)
        configure_task_host(self)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        configure_task_title(self.status, "section")
        layout.addWidget(self.status)
        self.request_status = QLabel(self.run.request_state.text)
        self.request_status.setTextFormat(Qt.TextFormat.PlainText)
        self.request_status.setWordWrap(True)
        self.request_status.setToolTip("所有来源共享请求名额。模型调用中包含供应商客户端的内部重试。")
        self.run.request_state.changed.connect(self.request_status.setText)
        layout.addWidget(self.request_status)
        self.result_summary = QLabel()
        self.result_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)
        self.project_summary = QLabel()
        self.project_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.project_summary.setWordWrap(True)
        self.project_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        configure_task_title(self.project_summary, "section")
        layout.addWidget(self.project_summary)
        self.bar = QProgressBar()
        layout.addWidget(self.bar)
        self.sources = _SourceProgressTable()
        self.sources.setHeaderLabels(["处理内容", "阶段", "进度", "当前处理"])
        self.sources.setRootIsDecorated(False)
        self.sources.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.sources.setWordWrap(False)
        header = self.sources.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.rows = {}
        for task in session.tasks:
            row = QTreeWidgetItem([source_label(session.tasks, task.key), "等待", "—", f"{len(task.entries)} 条"])
            row.setData(0, Qt.ItemDataRole.UserRole, task.key)
            row.setToolTip(0, row.text(0))
            self.sources.addTopLevelItem(row)
            self.rows[task.key] = row
        self.sources.setColumnWidth(0, 290)
        layout.addWidget(self.sources)
        diagnostics = QHBoxLayout()
        self.activity_panel = TaskActivityPanel(self)
        self.logs = self.activity_panel.logs
        self.log_toggle = self.activity_panel.toggle
        layout.addWidget(self.activity_panel)
        diagnostics.addStretch()
        self.log_button = QPushButton("LLM 日志")
        configure_task_button(self.log_button)
        self.log_button.clicked.connect(self._open_log)
        diagnostics.addWidget(self.log_button)
        layout.addLayout(diagnostics)
        layout.addStretch(1)
        self.record_status = QLabel()
        self.record_status.setWordWrap(True)
        self.save_status = QLabel()
        self.save_status.setWordWrap(True)
        layout.addWidget(self.record_status)
        layout.addWidget(self.save_status)
        self.next_step = QLabel()
        self.next_step.setTextFormat(Qt.TextFormat.PlainText)
        self.next_step.setWordWrap(True)
        configure_task_title(self.next_step, "hint")
        layout.addWidget(self.next_step)
        buttons = QHBoxLayout()
        for name, label, callback in (
            ("pause_button", "暂停", self.run.toggle_pause),
            ("preview_button", "确认结果", self._confirm),
            ("retry_button", "重试失败条目", self.run.retry),
            ("save_button", "保存项目", self.run.save),
            ("report_button", "查看结果", self._open_report),
            ("stop_button", "取消任务", self._stop),
        ):
            button = QPushButton(label)
            configure_task_button(button)
            button.clicked.connect(callback)
            if name == "stop_button":
                buttons.addStretch()
            buttons.addWidget(button)
            setattr(self, name, button)
        layout.addLayout(buttons)
        self.retry_record_button = QPushButton("重试保存任务记录")
        self.retry_record_button.clicked.connect(self.run.records.retry)
        layout.addWidget(self.retry_record_button)
        self._theme = AiThemeBinding(self, theme_view, lambda _: self.sources.updateGeometry())
        self.run.changed.connect(self._refresh)
        self.run.logged.connect(self.activity_panel.append)
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
        self.record_status.setVisible(bool(run.records.status))
        self.save_status.setText(run.save_status)
        self.save_status.setVisible(bool(run.save_status))
        self.retry_record_button.setVisible(bool(run.records.error))
        self.retry_record_button.setEnabled(not run.records.busy)
        current, total, pattern = run.progress
        self.bar.setRange(0, total)
        self.bar.setValue(current)
        self.bar.setFormat(pattern)
        self._refresh_source_progress()
        if self._last_selected != run.selected_key and run.selected_key in self.rows:
            self.sources.setCurrentItem(self.rows[run.selected_key])
            self._last_selected = run.selected_key
        self.pause_button.setEnabled(run.worker is not None and not run.completion_received and not run.cancelled)
        self.pause_button.setText("继续" if run.pause_state != "running" else "暂停")
        self.stop_button.setText("取消任务" if run.running and not run.completion_received else "关闭")
        self.stop_button.setEnabled(not run.cancelled or not run.running)
        self.retry_button.setEnabled(run.can_retry)
        self.preview_button.setText("确认结果" if run.state == "pending_confirmation" else "应用成功结果")
        self.preview_button.setEnabled(run.can_apply_success and not self._preview_open)
        self.report_button.setEnabled(run.records.record is not None)
        self.save_button.setText("保存项目")
        self.save_button.setEnabled(self.session.can_save and not self.session.is_busy)
        self._refresh_actions()
        self.activity_panel.observe(run)

    def _refresh_source_progress(self):
        view = getattr(self.run, "progress_view", None)
        summary = getattr(view, "summary", "")
        self.project_summary.setText(summary)
        self.project_summary.setVisible(bool(summary))
        source_rows = getattr(view, "source_rows", {})
        final_phases = {"处理结束", "已完成", "已取消", "未处理", "失败", "已失败"}
        for key, (phase, detail) in self.run.rows.items():
            progress = "—"
            source = source_rows.get(key)
            if source is not None and phase not in final_phases:
                phase, detail = source.phase, source.detail
                progress = getattr(source, "count_text", "") or (
                    f"{source.current:,} / {source.total:,}" if source.total > 0 else "—"
                )
            row = self.rows[key]
            for column, value in enumerate((phase, progress, detail), start=1):
                row.setText(column, value)
                row.setToolTip(column, value)
        self.sources.updateGeometry()

    def _refresh_actions(self):
        run = self.run
        executing = run.worker is not None and not run.completion_received and not run.cancelled
        self.pause_button.setVisible(executing)
        self.retry_button.setVisible(run.can_retry)
        self.preview_button.setVisible(
            not run.cancelled
            and not run.blocked
            and not run.running
            and (run.state == "pending_confirmation" or bool(run.entries.ready_keys))
        )
        self.report_button.setVisible(run.records.record is not None and not run.running)
        self.save_button.setVisible(self.session.can_save)
        primary = None
        if run.cancelled and run.running:
            hint = "正在停止后台任务，请等待取消完成。"
        elif run.preparing:
            hint = "准备完成后会自动开始处理。可取消本次任务。"
        elif executing:
            primary = self.pause_button
            hint = "可暂停后继续；取消处理会保留已完成的成果。"
            if run.pause_state != "running":
                hint = "可继续处理，或取消本次任务；尚未应用的结果不会写入项目。"
        elif run.applying or self.session.is_busy:
            hint = "正在应用或保存结果，请等待完成；关闭窗口不会中断此步骤。"
        elif run.running:
            hint = "正在整理结果，请等待后台任务结束。"
        elif run.can_apply_success:
            primary = self.preview_button
            hint = (
                "请确认成功结果后再应用；失败条目可单独重试。"
                if run.entries.failed_keys
                else "请确认结果后应用到项目。"
            )
        elif self.session.can_save:
            primary = self.save_button
            hint = (
                "项目已保存，可创建或重试快照；无需重新运行任务。"
                if getattr(self.session, "project_saved", False)
                else "结果已应用，项目尚未保存；请保存项目，无需重新运行任务。"
            )
        elif run.can_retry:
            primary = self.retry_button
            hint = "可重试失败条目，已应用的结果会保留；也可查看结果详情。"
        elif run.records.error:
            primary = self.retry_record_button if not run.records.busy else None
            hint = "任务记录保存失败，可重试保存记录；无需重新运行任务。"
        elif run.state == "error":
            primary = self.stop_button
            hint = "任务未启动，请根据上方原因修正后重新创建任务；当前任务可以关闭。"
        elif run.blocked:
            primary = self.report_button if run.records.record is not None else self.stop_button
            hint = "任务已停止，请根据上方原因修正后重新创建任务；可查看已有结果。"
        elif run.records.record is not None:
            primary = self.report_button
            hint = (
                "任务已取消；可查看结果记录，或关闭任务窗口。" if run.cancelled else "可查看结果详情，或关闭任务窗口。"
            )
        else:
            primary = self.stop_button
            hint = "可关闭任务窗口；需要再次执行时，请重新创建任务。"
        self.next_step.setText(hint)
        if primary is not self._primary_button:
            for button in (self._primary_button, primary):
                if button is not None:
                    selected = button is primary
                    button.setProperty("tbTaskPrimary", selected)
                    ComponentStyle.apply_state(button, SemanticState.PRIMARY if selected else SemanticState.DEFAULT)
            self._primary_button = primary

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
            decisions.update({entry.identity: chosen.get(ai_entry_id(entry)) is not None for entry in entries})
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
        dialog = TaskResultDialog(
            record,
            source_key=self._selected_key(),
            theme_view=self._theme_view,
            review_results=self._open_merge_review if self.run.recovery.record is not None else None,
        )
        self._dialogs.append(dialog)
        show_and_activate(dialog)

    def _open_merge_review(self):
        from .task_merge_dialog import TaskMergeDialog

        record = self.run.recovery.record
        if record is None or self.run.running:
            return
        dialog = TaskMergeDialog(
            self.session._ctx,
            self.session.project_dir,
            record["task_id"],
            store=self.run.recovery.store,
            theme_view=self._theme_view,
        )
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
        dialog.setInformativeText(self.next_step.text())
        dialog.setIcon(QMessageBox.Icon.Warning if notice["error"] else QMessageBox.Icon.Information)
        view = dialog.addButton("查看结果", QMessageBox.ButtonRole.ActionRole)
        view.setEnabled(self.run.records.record is not None)
        view.clicked.connect(self._open_report)
        primary = view
        if self.run.records.error:
            retry = dialog.addButton("重试保存任务记录", QMessageBox.ButtonRole.ActionRole)
            retry.clicked.connect(self.run.records.retry)
            primary = retry
        if self.run.can_retry:
            retry = dialog.addButton("重试失败条目", QMessageBox.ButtonRole.ActionRole)
            retry.clicked.connect(self.run.retry)
            primary = retry
        if notice["error"] and self.session.can_save:
            retry = dialog.addButton("重试保存", QMessageBox.ButtonRole.ActionRole)
            retry.clicked.connect(self.run.save)
            primary = retry
        configure_task_button(primary, primary=True)
        dialog.setDefaultButton(primary)
        dialog.addButton("关闭提示", QMessageBox.ButtonRole.RejectRole)
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
        self.activity_panel.timer.stop()
        self.run.dispose()
        for dialog in self._dialogs:
            dialog.close()
        self._theme.close()
        self.close()
