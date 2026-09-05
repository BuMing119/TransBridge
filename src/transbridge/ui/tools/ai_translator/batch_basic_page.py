"""Basic per-run options for batch AI translation."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QButtonGroup, QComboBox, QFormLayout, QLabel, QRadioButton, QVBoxLayout, QWidget

from transbridge.config.language_profiles import discover_language_profiles
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class BatchBasicPage(QWidget):
    changed = pyqtSignal()

    def __init__(self, config: object, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        title = QLabel("基础配置", self)
        title.setProperty("tbTaskSectionTitle", True)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        note = QLabel("当前批量能力会依次翻译所选插件，并可在“质量处理”中启用翻译后校对。", self)
        note.setProperty("tbTaskHint", True)
        note.setWordWrap(True)
        note.setAccessibleName("批量翻译模式说明")
        layout.addWidget(note)

        form = QFormLayout()
        self.target_language = QComboBox(self)
        ComponentStyle.apply_static(self.target_language, ComponentKind.INPUT)
        self.target_language.setAccessibleName("本次批量翻译目标语言")
        for profile in discover_language_profiles():
            self.target_language.addItem(f"{profile.display_name} ({profile.locale})", profile.locale)
        locale = str(getattr(config, "target_lang", "") or "")
        index = self.target_language.findData(locale)
        if index < 0 and locale:
            self.target_language.addItem(f"{locale}（配置不可用）", locale)
            index = self.target_language.count() - 1
        self.target_language.setCurrentIndex(max(0, index))
        form.addRow("目标语言", self.target_language)
        layout.addLayout(form)

        range_title = QLabel("翻译范围", self)
        range_title.setProperty("tbTaskSectionTitle", True)
        range_font = range_title.font()
        range_font.setBold(True)
        range_title.setFont(range_font)
        layout.addWidget(range_title)
        self.range_group = QButtonGroup(self)
        self.untranslated_only = QRadioButton("仅翻译未翻译内容（推荐）", self)
        self.overwrite = QRadioButton("翻译全部内容并覆盖已有译文", self)
        self.untranslated_only.setProperty("tbTaskControl", True)
        self.overwrite.setProperty("tbTaskControl", True)
        self.untranslated_only.setChecked(True)
        self.range_group.addButton(self.untranslated_only, 0)
        self.range_group.addButton(self.overwrite, 1)
        layout.addWidget(self.untranslated_only)
        layout.addWidget(self.overwrite)
        warning = QLabel("覆盖已有译文不可在任务中自动撤销，请仅在确实需要重翻时使用。", self)
        warning.setProperty("tbTaskHint", True)
        warning.setWordWrap(True)
        layout.addWidget(warning)
        layout.addStretch(1)

        self.target_language.currentIndexChanged.connect(self.changed.emit)
        self.range_group.idToggled.connect(lambda _button_id, checked: checked and self.changed.emit())

    def apply_to(self, config: object) -> None:
        config.target_lang = str(self.target_language.currentData() or "")

    @property
    def overwrite_enabled(self) -> bool:
        return self.overwrite.isChecked()


__all__ = ["BatchBasicPage"]
