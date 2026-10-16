"""Create mapping editors only for the cell currently being edited."""

from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate

from .mapping_model import SCOPE_LABELS


class ProfileMappingDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._active_editor = None

    def createEditor(self, parent, option, index):
        if index.column() == 3:
            editor = QComboBox(parent)
            for value, label in SCOPE_LABELS.items():
                editor.addItem(label, value)
            editor.activated.connect(lambda _index: self.commitData.emit(editor))
        else:
            editor = super().createEditor(parent, option, index)
        self._active_editor = editor
        return editor

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            with QSignalBlocker(editor):
                editor.setCurrentIndex(editor.findData(index.data(Qt.ItemDataRole.EditRole)))
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)

    def destroyEditor(self, editor, index):
        if editor is self._active_editor:
            self._active_editor = None
        super().destroyEditor(editor, index)

    def commit_pending(self) -> None:
        if self._active_editor is not None:
            self.commitData.emit(self._active_editor)


__all__ = ["ProfileMappingDelegate"]
