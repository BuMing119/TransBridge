# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Virtualized difference rows with full selected-entry text and Excel export."""

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class ComparisonModel(QAbstractTableModel):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.headers = (
            "来源",
            "词条标识",
            "原文",
            "当前译文",
            *(f"{p.name}（r{p.revision}）" for p in report.schemes),
            "提示",
        )

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.report.items)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        row = self.report.items[index.row()]
        value = (row.source, row.entry_key, row.original, row.current, *row.translations, row.notes)[index.column()]
        return value if role == Qt.ItemDataRole.ToolTipRole else value[:240].replace("\n", " ")

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return None


class SchemeComparisonDialog(QDialog):
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("术语方案 · 译文差异")
        available = self.screen().availableGeometry()
        self.resize(min(1180, int(available.width() * 0.9)), min(780, int(available.height() * 0.85)))
        self.report = None
        layout = QVBoxLayout(self)
        self.status = QLabel("正在比较所选方案…", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.table = QTableView(splitter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.details = QPlainTextEdit(splitter)
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("选择词条，查看完整原文和各方案译文。")
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.export_button = QPushButton("导出 Excel 工作簿…", self)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_requested)
        footer.addWidget(self.export_button)
        footer.addStretch()
        close = QPushButton("关闭", self)
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)
        ComponentStyle.apply_static(self.table, ComponentKind.TABLE)
        for button in (close, self.export_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)

    def set_report(self, report):
        self.report = report
        self.status.setText(
            f"比较 {report.total_count} 条，发现 {len(report.items)} 条译文差异；"
            f"{report.warning_count} 条含转换提示。只显示方案之间不同的译文。"
        )
        self.table.setModel(ComparisonModel(report, self.table))
        self.table.selectionModel().currentRowChanged.connect(self._selected)
        for column in range(self.table.model().columnCount()):
            self.table.setColumnWidth(column, 230 if column >= 2 else 150)
        self.export_button.setEnabled(True)
        if report.items:
            self.table.setCurrentIndex(self.table.model().index(0, 0))
        else:
            self.details.setPlainText("所选方案的词条译文相同，没有差异。仍可导出工作簿查看方案版本及比较统计。")

    def _selected(self, index, _previous=None):
        if not index.isValid():
            return
        row = self.report.items[index.row()]
        parts = [f"来源：{row.source}\n词条：{row.entry_key}", f"原文\n{row.original}", f"当前译文\n{row.current}"]
        parts.extend(
            f"{profile.name}（r{profile.revision}）\n{text}"
            for profile, text in zip(self.report.schemes, row.translations, strict=True)
        )
        if row.notes:
            parts.append(f"转换提示\n{row.notes}")
        self.details.setPlainText("\n\n".join(parts))
