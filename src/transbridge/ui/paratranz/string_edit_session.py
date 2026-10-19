"""Keep a string editor bound to its visible record until edits are saved or discarded."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QMessageBox, QTextEdit, QWidget


class StringEditSession:
    def __init__(self, host: QWidget, translation: QTextEdit, stage: QComboBox) -> None:
        self._host = host
        self._translation = translation
        self._stage = stage
        self.record: dict | None = None

    def bind(self, record: dict | None) -> None:
        """Repainting the same record must not replace unsaved editor contents."""
        if record is self.record:
            return
        self.record = record
        self._restore_saved_values()

    def confirm_discard(self) -> bool:
        record = self.record
        if record is None or (
            self._translation.toPlainText() == (record.get("translation") or "")
            and self._stage.currentData() == record.get("stage", 0)
        ):
            return True
        result = QMessageBox.warning(
            self._host,
            "尚未保存的修改",
            "当前词条有尚未保存的译文或状态修改。要放弃修改并继续吗？",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Discard:
            return False
        self._restore_saved_values()
        return True

    def _restore_saved_values(self) -> None:
        record = self.record or {}
        self._translation.setPlainText(record.get("translation") or "")
        self._stage.setCurrentIndex(self._stage.findData(record.get("stage", 0)))
