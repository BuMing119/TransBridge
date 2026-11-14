# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Shared input and output codec choices for project and program settings."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from transbridge.config.text_encoding import TEXT_ENCODINGS, TEXT_LANGUAGES, TextEncodings, fallback_encoding


class TextEncodingFields(QWidget):
    changed = pyqtSignal()

    def __init__(self, encodings: TextEncodings, parent=None, *, prefix="") -> None:
        super().__init__(parent)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.source_combo = self._add_combo(layout, prefix + "原文语言", encodings.source_language, TEXT_LANGUAGES)
        self.input_combo = self._add_combo(layout, prefix + "输入编码", encodings.input_encoding)
        self.target_combo = self._add_combo(layout, prefix + "目标语言", encodings.target_language, TEXT_LANGUAGES)
        self.output_combo = self._add_combo(layout, prefix + "输出编码", encodings.output_encoding)
        self.read_notice = QLabel(self)
        self.read_notice.setWordWrap(True)
        layout.addRow(self.read_notice)
        self.source_combo.currentIndexChanged.connect(lambda: self._language_changed(self.input_combo))
        self.target_combo.currentIndexChanged.connect(lambda: self._language_changed(self.output_combo))
        self.changed.connect(self._update_read_notice)
        self._update_read_notice()

    def _add_combo(self, layout, label, selected, choices=TEXT_ENCODINGS):
        combo = QComboBox(self)
        combo.setAccessibleName(label)
        combo.setMinimumContentsLength(12)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        for value, title in choices:
            if value == "utf-8":
                title = "UTF-8（语言默认）"
            combo.addItem(title, value)
        combo.setCurrentIndex(combo.findData(selected))
        combo.currentIndexChanged.connect(self.changed)
        layout.addRow(label, combo)
        return combo

    def _language_changed(self, codec_combo):
        codec_combo.setCurrentIndex(codec_combo.findData("utf-8"))
        self.changed.emit()

    def _update_read_notice(self):
        codec = self.input_combo.currentData()
        fallback = fallback_encoding(self.source_combo.currentData()) if codec == "utf-8" else None
        if fallback:
            label = dict(TEXT_ENCODINGS)[fallback]
            self.read_notice.setText(f"每条文本先按 UTF-8 读取，无法读取的条目使用 {label}。")
        else:
            self.read_notice.setText(f"按 {dict(TEXT_ENCODINGS)[codec]} 读取文本。")

    def value(self) -> TextEncodings:
        return TextEncodings(
            self.input_combo.currentData(),
            self.output_combo.currentData(),
            self.source_combo.currentData(),
            self.target_combo.currentData(),
        )
