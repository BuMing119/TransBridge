"""Detached, read-only recovery view for saved Project translations."""

from __future__ import annotations

import json

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QTableView,
    QVBoxLayout,
)

from transbridge.persistence.project_recovery import ProjectRecoverySnapshot
from transbridge.ui.foundation.components import configure_dialog


class _SavedEntriesModel(QAbstractTableModel):
    """Expose persisted rows lazily; never populate editable workbench slots."""

    headers = ("来源身份", "条目键", "已保存译文", "阶段", "标签")

    def __init__(self, recovery: ProjectRecoverySnapshot, parent=None) -> None:
        super().__init__(parent)
        self.entries = recovery.variant.entries

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            return None
        entry = self.entries[index.row()]
        return (
            entry.entry_key.namespace.value,
            entry.entry_key.local_key,
            entry.translation,
            str(entry.stage.value),
            ", ".join(entry.labels),
        )[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
        return super().headerData(section, orientation, role)


class ProjectRecoveryDialog(QDialog):
    def __init__(self, recovery: ProjectRecoverySnapshot, parent=None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{recovery.name} — 只读恢复")
        self.setAccessibleName("工程已保存译文恢复视图")
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "来源文件暂不可用或已变化。这里显示已保存的译文，不会更改当前工程或覆盖文件。\n"
            "恢复原始来源后，可重新检查并打开工程继续编辑。原始正文未保存在版本中，因此不在此推测显示。",
            self,
        )
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        failures = QPlainTextEdit(self)
        failures.setReadOnly(True)
        failures.setAccessibleName("来源恢复诊断")
        failures.setPlainText(
            "\n".join(
                f"{dict(item.details).get('source_location', '')}\n{item.code}: {item.message}"
                for item in recovery.diagnostics
            )
        )
        failures.setMaximumHeight(110)
        layout.addWidget(failures)
        self.entries_model = _SavedEntriesModel(recovery, self)
        self.table = QTableView(self)
        self.table.setAccessibleName("已保存译文，只读")
        self.table.setModel(self.entries_model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(28)
        layout.addWidget(self.table, 1)
        count = QLabel(f"版本 {recovery.variant.ref.identity.value} · 已保存 {len(recovery.variant.entries)} 条", self)
        count.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(count)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        self.copy_button = buttons.addButton("复制选中条目 JSON", QDialogButtonBox.ButtonRole.ActionRole)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_selected)
        self.table.selectionModel().selectionChanged.connect(
            lambda: self.copy_button.setEnabled(self.table.selectionModel().hasSelection())
        )
        retry = buttons.addButton("重新检查来源", QDialogButtonBox.ButtonRole.AcceptRole)
        retry.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self.copy_shortcut.activated.connect(self.copy_selected)

    def copy_selected(self) -> None:
        rows = sorted(index.row() for index in self.table.selectionModel().selectedRows())
        if rows:
            payload = [self.entries_model.entries[row].to_dict() for row in rows]
            QApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False, indent=2))


__all__ = ["ProjectRecoveryDialog"]
