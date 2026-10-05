"""Project history listing with explicit access to live and persisted tasks."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout

from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workers import ApiWorker

from ._theme_support import AiThemeBinding
from .task_recovery_prompt import RECOVERABLE_STATES, matches_version, read_recovery_records, recovery_summary

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
    def __init__(self, registry, *, theme_view=None):
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
        self.setWindowTitle("AI 任务记录")
        self.resize(1060, 540)
        layout = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        hint = QLabel("有恢复信息的校对任务可继续或重新开始，继续时重新校验已保存进度；旧任务记录仅供查看。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tasks = QTreeWidget()
        self.tasks.setHeaderLabels(["时间", "处理内容", "状态", "结果", "项目", "版本"])
        self.tasks.setRootIsDecorated(False)
        for column, width in enumerate((170, 200, 130, 240, 100)):
            self.tasks.setColumnWidth(column, width)
        self.tasks.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.tasks.itemSelectionChanged.connect(self._update_recovery_buttons)
        layout.addWidget(self.tasks)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("查看任务")
        self.open_button.clicked.connect(self.open_selected)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.open_button)
        self.continue_button = QPushButton("继续任务")
        self.restart_button = QPushButton("重新开始")
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
        key = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
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
        self.tasks.clear()
        for record in sorted(records.values(), key=lambda item: item.get("created_at", ""), reverse=True):
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
            row = QTreeWidgetItem([
                record.get("created_at", "")[:19].replace("T", " "),
                "、".join(item["label"] for item in record.get("sources", ())),
                state,
                summary,
                project_state,
                str(record.get("version_label") or record.get("variant_id") or ""),
            ])
            row.setData(0, Qt.ItemDataRole.UserRole, run_id)
            for column in range(6):
                row.setToolTip(column, row.text(column))
            self.tasks.addTopLevelItem(row)
            if run_id == key:
                self.tasks.setCurrentItem(row)
        if self.tasks.currentItem() is None and self.tasks.topLevelItemCount():
            self.tasks.setCurrentItem(self.tasks.topLevelItem(0))
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
        self.continue_button.setEnabled(not self.registry.shutting_down and self._selected_recovery() is not None)
        self.restart_button.setEnabled(
            not self.registry.shutting_down and self._selected_recovery(restart=True) is not None
        )

    def _recover_selected(self, restart):
        record = self._selected_recovery(restart=restart)
        if record is not None:
            self.registry.request_recovery(record, restart=restart)

    def open_selected(self):
        if self.registry.shutting_down:
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
            self._dispatch(lambda: self.store.load(run_id), self._open_record)

    def _open_record(self, record):
        from .task_result_dialog import TaskResultDialog

        dialog = TaskResultDialog(record, theme_view=self._theme_view)
        self._dialogs.append(dialog)
        show_and_activate(dialog)

    def _dispatch(self, action, completed):
        worker = ApiWorker(action, route_http_errors=False)
        self.worker = worker
        worker.result.connect(completed)
        worker.error.connect(lambda error: self.status.setText(f"读取失败：{error}"))

        def finished():
            self.worker = None
            worker.deleteLater()

        worker.finished.connect(finished)
        worker.start()

    def closeEvent(self, event):
        self.timer.stop()
        event.accept()

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)
