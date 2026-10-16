"""Read-only extracted-text preview and explicit removal of composer attachments."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class AttachmentDialog(QDialog):
    def __init__(self, binding, parent):
        super().__init__(parent)
        self.binding = binding
        self.setWindowTitle("当前会话的参考文件")
        self.resize(620, 500)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "预览的是完整提取文本，不包括图片和原始排版。当前文件随每次发送保存为材料快照。\n"
            "移除只影响后续发送，已发送材料仍保留在历史。未发送附件在退出应用后不恢复。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.files = QListWidget()
        self.files.setAccessibleName("当前附件列表")
        self.files.setMaximumHeight(110)
        layout.addWidget(self.files)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("附件完整提取文本")
        layout.addWidget(self.preview, 1)
        row = QHBoxLayout()
        add = QPushButton("添加文件")
        add.clicked.connect(lambda: binding.select_files())
        self.remove = QPushButton("移除所选附件")
        self.remove.clicked.connect(self._remove)
        clear = QPushButton("清空附件")
        clear.setToolTip("移除当前文件并取消接收正在提取的文件；已发送历史仍保留")
        clear.clicked.connect(binding.clear)
        row.addWidget(add)
        row.addWidget(self.remove)
        row.addWidget(clear)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.files.currentTextChanged.connect(self._preview)
        self.refresh()

    def refresh(self):
        selected = self.files.currentItem().text() if self.files.currentItem() else ""
        self.files.clear()
        self.files.addItems(list(self.binding.documents))
        matches = self.files.findItems(selected, Qt.MatchFlag.MatchExactly)
        if matches:
            self.files.setCurrentItem(matches[0])
        elif self.files.count():
            self.files.setCurrentRow(0)
        else:
            self._preview("")

    def _preview(self, name):
        document = self.binding.documents.get(name)
        self.preview.setPlainText(document.raw_text if document is not None else "暂无参考文件。")
        self.remove.setEnabled(document is not None)

    def _remove(self):
        item = self.files.currentItem()
        if item is not None:
            self.binding.remove(item.text())
