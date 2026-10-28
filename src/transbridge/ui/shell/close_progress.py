"""An independently usable return action while the other windows drain."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget


class CloseProgress:
    def __init__(self, host, cancel) -> None:
        self._dialog = None
        self._host = host
        self._cancel = cancel

    def show(self) -> None:
        if self._dialog is not None or not isinstance(self._host, QWidget):
            return
        # Parentless: disabling the workbench/main window must not disable return.
        dialog = self._dialog = QDialog()
        dialog.setProperty("tbCloseProgress", True)
        dialog.setWindowTitle("正在关闭")
        layout = QVBoxLayout(dialog)
        note = QLabel("正在保存内容并等待后台任务安全结束。你可以返回工作台，稍后再退出。", dialog)
        note.setWordWrap(True)
        layout.addWidget(note)
        button = QPushButton("返回工作台", dialog)
        button.setAccessibleName("取消退出并返回工作台")
        layout.addWidget(button)
        button.clicked.connect(self._cancel)
        dialog.rejected.connect(self._cancel)
        self._host.destroyed.connect(dialog.deleteLater)
        dialog.resize(420, 130)
        dialog.show()

    def hide(self) -> None:
        dialog, self._dialog = self._dialog, None
        if dialog is not None:
            dialog.hide()
            dialog.deleteLater()
