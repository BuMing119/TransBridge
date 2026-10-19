"""Safe confirmation buttons for destructive ParaTranz cloud operations."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget


def confirm_cloud_removal(
    parent: QWidget,
    message: str,
    *,
    action_text: str,
    title: str = "确认删除",
) -> QMessageBox.StandardButton:
    """Require an explicit destructive choice while retaining Yes/No semantics."""

    dialog = QMessageBox(
        QMessageBox.Icon.Warning,
        title,
        message,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    delete_button = dialog.button(QMessageBox.StandardButton.Yes)
    delete_button.setText(action_text)
    cancel_button = dialog.button(QMessageBox.StandardButton.No)
    cancel_button.setText("取消")
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    dialog.setEscapeButton(QMessageBox.StandardButton.No)
    cancel_button.setFocus()
    try:
        return QMessageBox.StandardButton(dialog.exec())
    finally:
        dialog.deleteLater()
