"""Explicit confirmation of a prepared source update across every Variant."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from transbridge.ui.foundation.components import configure_dialog


class SourceUpdatePreviewDialog(QDialog):
    def __init__(self, preview, parent=None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowTitle("确认更新源文件")
        self.setAccessibleName("源文件更新预览")
        self.resize(640, 440)
        layout = QVBoxLayout(self)
        self.summary = QLabel(self)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        self.summary.setText(
            f"来源：{preview.source_name}\n新文件：{preview.replacement_path}\n\n"
            f"将迁移全部 {preview.variant_count} 个翻译版本。\n"
            f"新增 {preview.added} · 移除 {preview.removed} · 原文未变 {preview.unchanged}\n"
            f"原文变化 {preview.changed} · 原文无法核验 {preview.unverified} · 顺序变化 {preview.reordered}"
        )
        layout.addWidget(self.summary)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setAccessibleName("更新影响与注意事项")
        self.details.setPlainText(
            "匹配的译文会保留；原文变化或无法核验的译文需要重新复核。\n"
            "更新前会备份工程及全部版本，移除条目的已保存译文保留在备份中。\n"
            "这一步不会向 ParaTranz 写入数据。\n\n" + "\n".join(preview.warnings)
        )
        layout.addWidget(self.details, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        self.confirm_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.confirm_button.setText("确认更新全部版本")
        self.confirm_button.setAutoDefault(False)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("取消")
        cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


__all__ = ["SourceUpdatePreviewDialog"]
