# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Select and explicitly confirm an application data move."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from transbridge.config.storage_location import (
    StorageLocationError,
    active_storage_root,
    default_data_path,
    default_progress_path,
    selected_storage_root,
)


class StorageSettingsPage(QWidget):
    confirmed = pyqtSignal(object)

    def __init__(self, blocker, parent=None):
        super().__init__(parent)
        self._blocker = blocker
        self.destination = None
        layout = QVBoxLayout(self)
        try:
            self._current = active_storage_root()
        except StorageLocationError as exc:
            notice = QLabel(self)
            notice.setTextFormat(Qt.TextFormat.PlainText)
            notice.setWordWrap(True)
            notice.setText(str(exc))
            layout.addWidget(notice)
            layout.addStretch()
            return
        paths = QFormLayout()
        if self._current is None:
            paths.addRow("当前项目与设置", self._path_label(str(default_data_path())))
            paths.addRow("当前翻译进度", self._path_label(str(default_progress_path())))
        else:
            paths.addRow("当前位置", self._path_label(str(self._current)))
        self.preview = self._path_label("尚未选择")
        paths.addRow("新位置", self.preview)
        layout.addLayout(paths)
        self.choose = QPushButton("更改位置…", self)
        self.choose.clicked.connect(self._select)
        layout.addWidget(self.choose, alignment=Qt.AlignmentFlag.AlignLeft)
        self.move = QPushButton("迁移并重启", self)
        self.move.setAutoDefault(False)
        self.move.setEnabled(False)
        self.move.clicked.connect(self._confirm)
        layout.addWidget(self.move, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

    def _path_label(self, text):
        field = QLineEdit(text, self)
        field.setReadOnly(True)
        field.setCursorPosition(0)
        return field

    def _select(self):
        path = QFileDialog.getExistingDirectory(self, "选择数据存放位置")
        if not path:
            return
        self.destination = selected_storage_root(path)
        self.preview.setText(str(self.destination))
        self.preview.setToolTip(str(self.destination))
        self.move.setEnabled(self.destination != self._current)

    def _confirm(self):
        if self.destination is None:
            return
        if self._blocker is None:
            QMessageBox.information(self, "更改数据存放位置", "请从主窗口打开设置。")
            return
        text = (
            f"将项目、设置和翻译进度搬到以下位置，然后重新打开软件。\n\n{self.destination}"
            "\n\n其他设置页上尚未应用的修改将被放弃。确定开始迁移吗？"
        )
        if (
            QMessageBox.question(
                self,
                "迁移并重启",
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        reason = self._blocker()
        if reason:
            QMessageBox.information(self, "暂时无法迁移", reason)
            return
        self.confirmed.emit(self.destination)
