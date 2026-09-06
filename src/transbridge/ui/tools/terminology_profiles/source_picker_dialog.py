"""Choose one terminology source before creating a naming scheme."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .source_catalog import TerminologySourceSelection, configured_source_selections, local_file_selection


class TerminologySourcePickerDialog(QDialog):
    """Present project sources and explicit local-file import in one place."""

    def __init__(self, context, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selections = list(configured_source_selections(context))
        self.setWindowTitle("选择术语来源")
        self.setAccessibleName("创建译名方案的术语来源")
        self.resize(680, 460)

        layout = QVBoxLayout(self)
        title = QLabel("从哪一份术语创建译名方案？", self)
        title.setProperty("tbTerminologyPageTitle", True)
        explanation = QLabel(
            "每次只读取一个来源，并先显示采用、保留和冲突预览。创建后的方案是独立副本，来源变化不会自动改写它。",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setProperty("tbSecondary", True)
        layout.addWidget(title)
        layout.addWidget(explanation)

        self.source_list = QListWidget(self)
        self.source_list.setAccessibleName("可用于创建译名方案的术语来源")
        ComponentStyle.apply_static(self.source_list, ComponentKind.TABLE)
        layout.addWidget(self.source_list, 1)
        for selection in self._selections:
            self._append(selection)

        local_row = QHBoxLayout()
        local_hint = QLabel("没有列出需要的文件？", self)
        local_hint.setProperty("tbSecondary", True)
        local_row.addWidget(local_hint)
        local_row.addStretch(1)
        self.browse_button = QPushButton("选择本地术语文件…", self)
        ComponentStyle.apply_static(self.browse_button, ComponentKind.BUTTON)
        self.browse_button.clicked.connect(self._browse)
        local_row.addWidget(self.browse_button)
        layout.addLayout(local_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        self.continue_button = QPushButton("继续预览", self)
        self.continue_button.setDefault(True)
        ComponentStyle.apply_static(self.continue_button, ComponentKind.BUTTON)
        buttons.addButton(self.continue_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source_list.currentRowChanged.connect(lambda _row: self._update_enabled())
        self.source_list.itemDoubleClicked.connect(lambda _item: self.accept())
        if self._selections:
            self.source_list.setCurrentRow(0)
        self._update_enabled()

    @property
    def selection(self) -> TerminologySourceSelection | None:
        row = self.source_list.currentRow()
        return self._selections[row] if 0 <= row < len(self._selections) else None

    def accept(self) -> None:
        if self.selection is None:
            return
        super().accept()

    def _append(self, selection: TerminologySourceSelection) -> None:
        item = QListWidgetItem(f"{selection.label}\n{selection.detail}", self.source_list)
        item.setToolTip(selection.detail)

    def _browse(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择术语文件",
            "",
            "术语文件 (*.json *.csv *.xls *.xlsx);;JSON (*.json);;CSV (*.csv);;Excel (*.xls *.xlsx)",
        )
        if not path:
            return
        try:
            selection = local_file_selection(path)
        except ValueError as exc:
            QMessageBox.warning(self, "无法使用该文件", str(exc))
            return
        self._selections.append(selection)
        self._append(selection)
        self.source_list.setCurrentRow(len(self._selections) - 1)

    def _update_enabled(self) -> None:
        self.continue_button.setEnabled(self.selection is not None)


__all__ = ["TerminologySourcePickerDialog"]
