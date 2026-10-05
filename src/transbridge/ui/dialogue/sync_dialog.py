"""Explicit selection of matching originals, including protected entries and review states."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.converter.translation_entry import STAGE_LABELS


class SyncTranslationDialog(QDialog):
    def __init__(self, candidates, original: str, translation: str, parent=None):
        super().__init__(parent)
        self._candidates = candidates
        self.setWindowTitle("统一相同原文的译文")
        self.resize(850, 560)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"当前词条已应用。当前内容中还有 {len(candidates)} 条相同原文、不同译文的词条。\n"
            "勾选后统一译文；已检查、已审核条目默认不选，未应用草稿及锁定、隐藏条目受保护。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        for title, text in (("相同原文", original), ("统一为", translation)):
            layout.addWidget(QLabel(title))
            field = QPlainTextEdit(text)
            field.setReadOnly(True)
            field.setAccessibleName(title)
            field.setMaximumHeight(80)
            layout.addWidget(field)
        self.table = QTableWidget(len(candidates), 4)
        self.table.setAccessibleName("待统一译文词条")
        self.table.setHorizontalHeaderLabels(["同步", "词条位置", "当前译文", "状态 / 保护原因"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for row, candidate in enumerate(candidates):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Checked if candidate.selected_by_default else Qt.CheckState.Unchecked)
            if candidate.protected_reason:
                check.setFlags(Qt.ItemFlag.NoItemFlags)
            self.table.setItem(row, 0, check)
            status = STAGE_LABELS.get(candidate.draft.before.stage, str(candidate.draft.before.stage))
            if candidate.protected_reason:
                status += f" · {candidate.protected_reason}"
            for column, value in enumerate((candidate.key, candidate.draft.before.translation, status), 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("统一所选译文")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("跳过同步")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_candidates(self):
        return tuple(
            candidate
            for row, candidate in enumerate(self._candidates)
            if not candidate.protected_reason and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
