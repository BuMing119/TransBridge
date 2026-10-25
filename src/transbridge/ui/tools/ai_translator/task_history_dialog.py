"""Project history listing with explicit access to live and persisted tasks."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QSignalBlocker, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workers import ApiWorker

from ._theme_support import AiThemeBinding
from .task_recovery_prompt import RECOVERABLE_STATES, matches_version, read_recovery_records, recovery_summary
from .task_widget_style import configure_task_button, configure_task_host, configure_task_title

STATE_LABELS = {
    "preparing": "准备中",
    "running": "执行中",
    "failed": "有失败条目",
    "error": "失败",
    "cancelled": "已取消",
    "cancelling": "正在取消",
    "completed": "已完成",
    "pending_confirmation": "待确认",
    "partial": "部分已应用",
    "interrupted": "已中断",
    "applying": "应用未完成",
}


class TaskHistoryDialog(QDialog):
    def __init__(self, registry, *, theme_view=None, run_id=None):
        super().__init__()
        self.registry = registry
        directory = registry.directory_provider()
        self.store = TaskHistoryStore(directory / "ai-task-history") if directory else None
        self.project_dir = directory
        self._theme_view = theme_view
        self._dialogs = []
        self.worker = None
        self._records = ()
        self._recovery_records = {}
        self._requested_run_id = run_id
        self._auto_open_run_id = run_id
        self._result_generation = 0
        self._closed = False
        self._selection_recovery_error = ""
        self.setWindowTitle("AI 任务记录")
        configure_task_host(self)
        self.resize(1060, 540)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAccessibleName("任务记录读取状态")
        layout.addWidget(self.status)
        hint = QLabel(
            "翻译、校对和混合任务均可按恢复记录继续；保存的候选会重新核验。"
            "有检查点的旧任务会先确认当前配置，再接续处理并重新校对。"
        )
        hint.setWordWrap(True)
        configure_task_title(hint, "hint")
        layout.addWidget(hint)
        self.tasks = QTreeWidget()
        self.tasks.setAccessibleName("AI 任务记录")
        self.tasks.setHeaderLabels(["时间", "处理内容", "状态", "结果", "项目", "版本"])
        self.tasks.setRootIsDecorated(False)
        for column, width in enumerate((170, 200, 130, 240, 100)):
            self.tasks.setColumnWidth(column, width)
        self.tasks.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.tasks.itemSelectionChanged.connect(self._update_recovery_buttons)
        layout.addWidget(self.tasks)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("查看任务")
        configure_task_button(self.open_button, primary=True)
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self.open_selected)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.open_button)
        self.continue_button = QPushButton("继续任务")
        self.restart_button = QPushButton("重新开始")
        for button in (self.continue_button, self.restart_button, self.refresh_button):
            configure_task_button(button)
        self.continue_button.clicked.connect(lambda: self._recover_selected(False))
        self.restart_button.clicked.connect(lambda: self._recover_selected(True))
        buttons.addWidget(self.continue_button)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)
        self._theme = AiThemeBinding(self, theme_view, lambda _: None)
        # Refresh live status at a bounded rate, without reading disk on each progress event.
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._populate)
        self.timer.start()
        self.refresh()

    @property
    def busy(self):
        return self.worker is not None

    def refresh(self):
        if self.worker is not None:
            return
        if self.store is None:
            self.status.setText("请先打开项目")
            self._populate()
            QTimer.singleShot(0, self._open_requested)
            return
        self.status.setText("正在读取任务记录")

        def loaded(result):
            records, recovery_records, recovery_error = result
            self._records = records
            self._recovery_records = {record["task_id"]: record for record in recovery_records}
            self.status.setText(recovery_error or ("" if records or recovery_records else "暂无任务记录"))
            self._populate()

        def read():
            records = self.store.list_records()
            try:
                recovery_records = read_recovery_records(self.project_dir)
            except (OSError, ValueError, RuntimeError) as exc:
                return records, (), f"读取恢复信息失败：{exc}"
            return records, recovery_records, ""

        self._dispatch(read, loaded)

    def _populate(self):
        selected = self.tasks.currentItem()
        key = selected.data(0, Qt.ItemDataRole.UserRole) if selected else self._requested_run_id
        live = {
            run_id: win for run_id, win in self.registry.windows.items() if win.session.project_dir == self.project_dir
        }
        records = {item["run_id"]: item for item in self._records}
        for run_id, record in self._recovery_records.items():
            records.setdefault(run_id, {**record, "run_id": run_id})
        for run_id, window in live.items():
            records[run_id] = window.run.records.record or {
                "run_id": run_id,
                "created_at": window.run.records.created_at,
                "sources": [{"label": task.label} for task in window.session.tasks],
            }
        existing = {
            self.tasks.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole): self.tasks.topLevelItem(index)
            for index in range(self.tasks.topLevelItemCount())
        }
        anchor = self.tasks.itemAt(QPoint(2, 2))
        vertical, horizontal = self.tasks.verticalScrollBar().value(), self.tasks.horizontalScrollBar().value()
        blocker = QSignalBlocker(self.tasks)
        rows = {}
        ordered = sorted(records.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        for index, record in enumerate(ordered):
            run_id = record["run_id"]
            window = live.get(run_id)
            recovery = self._recovery_records.get(run_id)
            current_state = (recovery or record).get("state")
            state = window.run.status if window else STATE_LABELS.get(current_state, "已中断")
            if not window and current_state in {"running", "preparing", "cancelling"}:
                state = "已中断"
            if not window and current_state == "pending_confirmation":
                state = "待确认（已结束）"
            counts = record.get("counts", {})
            summary = (
                f"成功 {counts.get('successful', 0)} · 失败 {counts.get('failed', 0)}"
                f" · 未处理 {counts.get('unprocessed', 0)}"
            )
            if run_id in self._recovery_records:
                summary = recovery_summary(self._recovery_records[run_id])
            applied = record.get("applied")
            if window:
                applied = (
                    bool(window.session.applied_keys)
                    if hasattr(window.session, "applied_keys")
                    else window.session.completed
                )
            saved = window.session.project_saved if window else record.get("project_saved", record.get("saved"))
            project_state = "已保存" if saved else ("已应用" if applied else "未应用")
            if recovery is not None and window is None:
                if "project_saved" in recovery:
                    project_state = (
                        "已保存" if recovery["project_saved"] else "已应用" if recovery.get("applied") else "未应用"
                    )
                else:
                    project_state = "已保存部分结果" if recovery.get("applied_entries") else "保存状态待核验"
            values = [
                record.get("created_at", "")[:19].replace("T", " "),
                "、".join(item["label"] for item in record.get("sources", ())),
                state,
                summary,
                project_state,
                str(record.get("version_label") or record.get("variant_id") or ""),
            ]
            row = existing.pop(run_id, None)
            if row is None:
                row = QTreeWidgetItem()
                self.tasks.insertTopLevelItem(index, row)
            elif self.tasks.indexOfTopLevelItem(row) != index:
                self.tasks.takeTopLevelItem(self.tasks.indexOfTopLevelItem(row))
                self.tasks.insertTopLevelItem(index, row)
            row.setData(0, Qt.ItemDataRole.UserRole, run_id)
            for column, value in enumerate(values):
                row.setText(column, value)
                row.setToolTip(column, value)
            if recovery is not None and recovery.get("recovery_error"):
                row.setToolTip(2, f"无法恢复任务：{recovery['recovery_error']}")
            rows[run_id] = row
        for row in existing.values():
            self.tasks.takeTopLevelItem(self.tasks.indexOfTopLevelItem(row))
        current = rows.get(key)
        if current is None and rows and self._requested_run_id is None:
            current = self.tasks.topLevelItem(0)
        if self.tasks.currentItem() is not current:
            self.tasks.setCurrentItem(current)
        if anchor is not None and anchor.treeWidget() is self.tasks:
            self.tasks.scrollToItem(anchor, QAbstractItemView.ScrollHint.PositionAtTop)
        elif selected is not None:
            self.tasks.verticalScrollBar().setValue(vertical)
        self.tasks.horizontalScrollBar().setValue(horizontal)
        del blocker
        if self._requested_run_id is not None and self.tasks.currentItem() is None:
            self.status.setText("所选任务记录已不存在或不属于当前项目，请刷新或返回原项目。")
        self._update_recovery_buttons()

    def _selected_recovery(self, *, restart=False):
        row = self.tasks.currentItem()
        key = row.data(0, Qt.ItemDataRole.UserRole) if row else None
        record = self._recovery_records.get(key)
        if (
            record is None
            or self.project_dir != self.registry.directory_provider()
            or self.registry.is_recovery_active(key)
            or not record.get("supported", True)
            or (not restart and record.get("state") not in RECOVERABLE_STATES)
            or not matches_version(record, getattr(self.registry.ctx, "active_version_identity", None))
        ):
            return None
        return record

    def _update_recovery_buttons(self):
        row = self.tasks.currentItem()
        key = row.data(0, Qt.ItemDataRole.UserRole) if row else None
        error = self._recovery_records.get(key, {}).get("recovery_error", "")
        text = f"无法恢复任务：{error}。原记录已保留，可查看结果。" if error else ""
        if text or self.status.text() == self._selection_recovery_error:
            self.status.setText(text)
        self._selection_recovery_error = text
        available = not self.registry.shutting_down and not self._closed and not self.busy
        self.open_button.setEnabled(available and self.tasks.currentItem() is not None)
        self.refresh_button.setEnabled(available)
        self.continue_button.setEnabled(not self.registry.shutting_down and self._selected_recovery() is not None)
        self.restart_button.setEnabled(
            not self.registry.shutting_down and self._selected_recovery(restart=True) is not None
        )

    def _recover_selected(self, restart):
        record = self._selected_recovery(restart=restart)
        if record is not None:
            self.registry.request_recovery(record, restart=restart)

    def open_selected(self):
        if self._closed or self.registry.shutting_down:
            return
        row = self.tasks.currentItem()
        if row is None or self.busy:
            return
        run_id = row.data(0, Qt.ItemDataRole.UserRole)
        live = self.registry.windows.get(run_id)
        if live is not None:
            show_and_activate(live)
            return
        if run_id in self._recovery_records and not any(record["run_id"] == run_id for record in self._records):
            self.status.setText("该任务尚无完整结果报告，可选择继续任务或重新开始。")
            return
        if self.store is not None:
            generation = self._result_generation
            self._dispatch(lambda: self.store.load(run_id), lambda record: self._open_record(record, generation))

    def _open_record(self, record, generation):
        if generation != self._result_generation or self._closed or self.registry.shutting_down:
            return
        from .task_result_dialog import TaskResultDialog

        dialog = TaskResultDialog(record, theme_view=self._theme_view)
        self._dialogs.append(dialog)
        show_and_activate(dialog)

    def _open_requested(self):
        run_id, self._auto_open_run_id = self._auto_open_run_id, None
        row = self.tasks.currentItem()
        if run_id is not None and row is not None and row.data(0, Qt.ItemDataRole.UserRole) == run_id:
            self.open_selected()

    def _dispatch(self, action, completed):
        worker = ApiWorker(action, route_http_errors=False)
        self.worker = worker
        self._update_recovery_buttons()
        worker.result.connect(completed)
        worker.error.connect(lambda error: self.status.setText(f"读取失败：{error}"))

        def finished():
            self.worker = None
            worker.deleteLater()
            self._update_recovery_buttons()
            if self._auto_open_run_id is not None:
                QTimer.singleShot(0, self._open_requested)

        worker.finished.connect(finished)
        worker.start()

    def closeEvent(self, event):
        self._closed = True
        self._auto_open_run_id = None
        self._result_generation += 1
        self.timer.stop()
        event.accept()

    def showEvent(self, event):
        self._closed = False
        self.timer.start()
        self._update_recovery_buttons()
        super().showEvent(event)
