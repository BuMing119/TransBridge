"""Read-only task results with a virtual table and optional background exports."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
)

from transbridge.application.translation.task_history import restore_report_snapshot
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, ElidedLabel, SemanticState
from transbridge.ui.workers import ApiWorker

from ._theme_support import AiThemeBinding
from .reporting import export_snapshot
from .task_widget_style import configure_task_button, configure_task_host, configure_task_input, configure_task_title

RESULT_LABELS = {
    "applied": "已应用",
    "accepted": "已接受",
    "rejected": "未采纳",
    "failed": "失败",
    "pending": "待确认",
    "not_applied": "未应用",
    "not_started": "未处理",
    "cancelled": "已取消",
}


def result_label(entry):
    details = entry.get("report_details", {})
    status = details.get("result_status")
    return RESULT_LABELS.get(status, "通过" if entry.get("accepted") else "未采用")


class TaskEntriesModel(QAbstractTableModel):
    headers = ("条目", "原文", "原译文", "候选译文", "结果", "原因")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = ()

    def set_entries(self, entries):
        self.beginResetModel()
        self.entries = tuple(entries)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        entry = self.entries[index.row()]
        values = (
            entry.get("entry_key", {}).get("local_key", ""),
            entry.get("original", ""),
            entry.get("before", ""),
            entry.get("candidate", ""),
            result_label(entry),
            entry.get("report_details", {}).get("note", ""),
        )
        text = str(values[index.column()])
        return text if role == Qt.ItemDataRole.ToolTipRole else text[:240].replace("\n", " ")


class TaskResultDialog(QDialog):
    def __init__(self, record, *, source_key=None, theme_view=None):
        super().__init__()
        self.record = record
        self.worker = None
        self._shutdown = False
        self._cancel = threading.Event()
        self.setWindowTitle("AI 任务结果")
        configure_task_host(self)
        self.resize(1080, 720)
        layout = QVBoxLayout(self)
        applied = "已应用到项目" if record.get("applied") else "未应用到项目"
        saved = "项目已保存" if record.get("project_saved", record.get("saved")) else "项目未保存"
        version = record.get("version_label") or record.get("variant_id", "")
        self.project_status = QLabel(f"{applied} · {saved}")
        self.project_status.setWordWrap(True)
        configure_task_title(self.project_status, "section")
        layout.addWidget(self.project_status)
        self.context_label = ElidedLabel(f"{record.get('created_at', '')[:19].replace('T', ' ')} · {version}")
        self.context_label.setTextFormat(Qt.TextFormat.PlainText)
        self.context_label.setToolTip(self.context_label.full_text)
        self.context_label.setAccessibleName("任务时间与版本")
        configure_task_title(self.context_label, "meta")
        layout.addWidget(self.context_label)
        filters = QHBoxLayout()
        self.sources = QComboBox()
        self.sources.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.sources.setMinimumContentsLength(12)
        self.sources.setAccessibleName("结果来源")
        for source in record.get("sources", ()):
            self.sources.addItem(source["label"], source["key"])
            self.sources.setItemData(self.sources.count() - 1, source["label"], Qt.ItemDataRole.ToolTipRole)
        self.sources.currentIndexChanged.connect(self._populate)
        filters.addWidget(self.sources, 1)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索任务结果")
        self.search.setPlaceholderText("搜索原文、译文或条目")
        self.search.textChanged.connect(self._populate)
        filters.addWidget(self.search, 2)
        self.filter = QComboBox()
        self.filter.setAccessibleName("结果状态筛选")
        self.filter.addItems(["全部", *dict.fromkeys(RESULT_LABELS.values()), "通过"])
        self.filter.currentTextChanged.connect(self._populate)
        filters.addWidget(self.filter)
        for control in (self.sources, self.search, self.filter):
            configure_task_input(control)
        layout.addLayout(filters)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setAccessibleName("结果条目计数")
        configure_task_title(self.summary, "hint")
        layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
        self.table.setAccessibleName("任务结果条目")
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        self.model = TaskEntriesModel(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(False)
        self.table.setColumnWidth(0, 190)
        for column in (1, 2, 3):
            self.table.setColumnWidth(column, 220)
        self.table.selectionModel().currentRowChanged.connect(self._detail)
        splitter.addWidget(self.table)
        self.detail = QTextEdit()
        self.detail.setAccessibleName("所选条目详情")
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setSizes([300, 240])
        layout.addWidget(splitter, 1)
        self.diagnostics = QTextEdit()
        self.diagnostics.setAccessibleName("任务诊断")
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(100)
        layout.addWidget(self.diagnostics)
        self.export_status = QLabel()
        self.export_status.setTextFormat(Qt.TextFormat.PlainText)
        self.export_status.setWordWrap(True)
        self.export_status.setAccessibleName("结果导出状态")
        ComponentStyle.apply_static(self.export_status, ComponentKind.LABEL)
        self.export_status.hide()
        layout.addWidget(self.export_status)
        buttons = QHBoxLayout()
        self.export_buttons = []
        for label, format in (("导出 Excel", "xlsx"), ("导出 CSV", "csv")):
            button = QPushButton(label)
            configure_task_button(button, primary=format == "xlsx")
            button.setToolTip("导出当前来源的完整结果，包含被搜索或状态筛选隐藏的条目。")
            button.clicked.connect(lambda _=False, format=format: self._choose_export(format))
            buttons.addWidget(button)
            self.export_buttons.append(button)
        self.cancel_button = QPushButton("取消导出")
        configure_task_button(self.cancel_button)
        self.cancel_button.clicked.connect(self.cancel_export)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.close_button = QPushButton("关闭")
        configure_task_button(self.close_button)
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._theme = AiThemeBinding(self, theme_view, lambda _: None)
        index = self.sources.findData(source_key)
        if index >= 0:
            self.sources.setCurrentIndex(index)
        self._populate()

    @property
    def busy(self):
        return self.worker is not None

    def _source(self):
        index = self.sources.currentIndex()
        return self.record["sources"][index] if index >= 0 else {}

    def _populate(self, *_):
        if not hasattr(self, "model"):
            return
        source = self._source()
        self.sources.setToolTip(self.sources.currentText())
        snapshot = source.get("snapshot") or {}
        entries = snapshot.get("entries", ())
        query, status = self.search.text().casefold(), self.filter.currentText()
        visible = [
            entry
            for entry in entries
            if (status == "全部" or result_label(entry) == status)
            and (
                not query
                or query
                in " ".join(
                    str(entry.get(key, "")) for key in ("entry_key", "original", "before", "candidate")
                ).casefold()
            )
        ]
        self.model.set_entries(visible)
        self.summary.setText(f"当前显示 {len(visible)} / {len(entries)} 条 · 导出包含本来源全部结果")
        self.detail.clear()
        self.detail.setPlaceholderText(
            "此来源暂无可显示的结果。"
            if not entries
            else "没有匹配的条目，请清除搜索或将状态筛选改为“全部”。"
            if not visible
            else "请选择一条结果，查看完整原文、译文及处理原因。"
        )
        notes = [source.get("error", "")]
        notes.extend(f"{item['code']}：{item['message']}" for item in snapshot.get("diagnostics", ()))
        self.diagnostics.setPlainText("\n".join(dict.fromkeys(note for note in notes if note)))
        self.diagnostics.setVisible(bool(self.diagnostics.toPlainText()))
        self._update_export_actions()

    def _update_export_actions(self):
        available = bool(self._source().get("snapshot")) and not self.busy and not self._shutdown
        for button in self.export_buttons:
            button.setEnabled(available)
        self.cancel_button.setVisible(self.busy)
        self.cancel_button.setEnabled(self.busy and not self._cancel.is_set())

    def _set_export_status(self, text, state):
        self.export_status.setText(text)
        self.export_status.setAccessibleDescription(text)
        self.export_status.setVisible(bool(text))
        ComponentStyle.apply_state(self.export_status, state)

    def _detail(self, index, _previous):
        if not index.isValid() or index.row() >= len(self.model.entries):
            return
        entry = self.model.entries[index.row()]
        key = entry.get("entry_key")
        notes = [entry.get("report_details", {}).get("note", "")]
        for diagnostic in (self._source().get("snapshot") or {}).get("diagnostics", ()):
            details = diagnostic.get("details", {})
            if details.get("entry_key") == key or key in details.get("entry_keys", ()):
                notes.append(f"{diagnostic['code']}：{diagnostic['message']}")
        reason = "\n".join(dict.fromkeys(note for note in notes if note))
        self.detail.setPlainText(
            f"原文\n{entry.get('original', '')}\n\n原译文\n{entry.get('before', '')}"
            f"\n\n候选译文\n{entry.get('candidate', '')}\n\n{result_label(entry)}"
            f"\n{reason}"
        )

    def _choose_export(self, format):
        extension = "xlsx" if format == "xlsx" else "csv"
        path, _ = QFileDialog.getSaveFileName(self, "导出任务结果", f"AI任务结果.{extension}", f"*.{extension}")
        if path:
            self.export(path, format)

    def export(self, path, format):
        if self.worker is not None or self._shutdown:
            return
        snapshot_data = self._source().get("snapshot")
        if not snapshot_data:
            self._set_export_status("没有可导出的条目", SemanticState.WARNING)
            return
        self._cancel.clear()
        self._set_export_status("正在导出当前来源的完整结果…", SemanticState.INFO)
        worker = ApiWorker(
            lambda: export_snapshot(restore_report_snapshot(snapshot_data), path, format, cancel_event=self._cancel),
            route_http_errors=False,
        )
        self.worker = worker
        self._update_export_actions()
        worker.result.connect(lambda result: self._set_export_status(f"已导出：{result}", SemanticState.SUCCESS))
        worker.error.connect(
            lambda error: self._set_export_status(
                "导出已取消" if self._cancel.is_set() else f"导出失败：{error}",
                SemanticState.INFO if self._cancel.is_set() else SemanticState.ERROR,
            )
        )

        def finished():
            self.worker = None
            worker.deleteLater()
            self._update_export_actions()

        worker.finished.connect(finished)
        worker.start()

    def cancel_export(self):
        if self.worker is not None:
            self._cancel.set()
            self._set_export_status("正在取消导出", SemanticState.INFO)
            self._update_export_actions()

    def shutdown(self):
        self._shutdown = True
        self.cancel_export()
        self._update_export_actions()

    def resume_exports(self):
        self._shutdown = False
        self._update_export_actions()
