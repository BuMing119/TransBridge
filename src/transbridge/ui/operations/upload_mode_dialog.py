"""Choose the file layout before opening either upload workflow."""

from PyQt6.QtWidgets import QButtonGroup, QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout

from transbridge.ui.foundation.components import configure_dialog


class UploadModeDialog(QDialog):
    """Use the same explicit layout choice for current-source and batch uploads."""

    def __init__(self, *, batch: bool = False, parent=None):
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("批量上传至 ParaTranz" if batch else "上传当前内容至 ParaTranz")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择上传方式", self))
        self._plain = QRadioButton("普通上传", self)
        self._categorized = QRadioButton("分类上传", self)
        self._group = QButtonGroup(self)
        for button in (self._plain, self._categorized):
            self._group.addButton(button)
        self._plain.setChecked(True)
        layout.addWidget(self._plain)
        layout.addWidget(QLabel("每个插件上传为单个 JSON 文件。" if batch else "沿用普通上传流程。", self))
        layout.addWidget(self._categorized)
        description = (
            "选择工程中的插件及分类文件，按“插件 / 分类”组织上传。"
            if batch
            else "仅上传当前插件，按内容分类选择要上传的文件。"
        )
        hint = QLabel(description, self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("下一步")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def mode(self) -> str:
        return "categorized" if self._categorized.isChecked() else "plain"
