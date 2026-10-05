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
from transbridge.ui.workers import ApiWorker

from ._theme_support import AiThemeBinding
from .reporting import export_snapshot

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
        self.resize(1080, 720)
        layout = QVBoxLayout(self)
        applied = "已应用到项目" if record.get("applied") else "未应用到项目"
        saved = "项目已保存" if record.get("project_saved", record.get("saved")) else "项目未保存"
        version = record.get("version_label") or record.get("variant_id", "")
        layout.addWidget(
            QLabel(f"{record.get('created_at', '')[:19].replace('T', ' ')} · {version} · {applied} · {saved}")
        )
        filters = QHBoxLayout()
        self.sources = QComboBox()
        for source in record.get("sources", ()):
            self.sources.addItem(source["label"], source["key"])
        self.sources.currentIndexChanged.connect(self._populate)
        filters.addWidget(self.sources)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索原文、译文或条目")
        self.search.textChanged.connect(self._populate)
        filters.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItems(["全部", *dict.fromkeys(RESULT_LABELS.values()), "通过"])
        self.filter.currentTextChanged.connect(self._populate)
        filters.addWidget(self.filter)
        layout.addLayout(filters)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableView()
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
        self.detail.setReadOnly(True)
        splitter.addWidget(self.detail)
        splitter.setSizes([300, 240])
        layout.addWidget(splitter, 1)
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(100)
        layout.addWidget(self.diagnostics)
        self.export_status = QLabel()
        self.export_status.setWordWrap(True)
        layout.addWidget(self.export_status)
        buttons = QHBoxLayout()
        self.export_buttons = []
        for label, format in (("导出 Excel", "xlsx"), ("导出 CSV", "csv")):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, format=format: self._choose_export(format))
            buttons.addWidget(button)
            self.export_buttons.append(button)
        self.cancel_button = QPushButton("取消导出")
        self.cancel_button.clicked.connect(self.cancel_export)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
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
        self.summary.setText(f"{len(visible)} / {len(entries)} 条")
        self.detail.clear()
        notes = [source.get("error", "")]
        notes.extend(f"{item['code']}：{item['message']}" for item in snapshot.get("diagnostics", ()))
        self.diagnostics.setPlainText("\n".join(dict.fromkeys(note for note in notes if note)))

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
            self.export_status.setText("没有可导出的条目")
            return
        self._cancel.clear()
        self.export_status.setText("正在导出")
        for button in self.export_buttons:
            button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        worker = ApiWorker(
            lambda: export_snapshot(restore_report_snapshot(snapshot_data), path, format, cancel_event=self._cancel),
            route_http_errors=False,
        )
        self.worker = worker
        worker.result.connect(lambda result: self.export_status.setText(f"已导出：{result}"))
        worker.error.connect(
            lambda error: self.export_status.setText("导出已取消" if self._cancel.is_set() else f"导出失败：{error}")
        )

        def finished():
            self.worker = None
            worker.deleteLater()
            for button in self.export_buttons:
                button.setEnabled(True)
            self.cancel_button.setEnabled(False)

        worker.finished.connect(finished)
        worker.start()

    def cancel_export(self):
        if self.worker is not None:
            self._cancel.set()
            self.export_status.setText("正在取消导出")

    def shutdown(self):
        self._shutdown = True
        self.cancel_export()
        for button in self.export_buttons:
            button.setEnabled(False)

    def resume_exports(self):
        self._shutdown = False
        for button in self.export_buttons:
            button.setEnabled(not self.busy)
