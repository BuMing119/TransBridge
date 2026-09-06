"""Naming-scheme page for the project terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologySchemesView(QWidget):
    """Render persistent naming schemes as project terminology assets."""

    create_requested = pyqtSignal()
    manage_requested = pyqtSignal()
    selection_requested = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 26, 26, 32)
        layout.setSpacing(18)

        heading = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(3)
        title = QLabel("译名方案", self)
        title.setProperty("tbTerminologyPageTitle", True)
        title.setAccessibleName("译名方案")
        description = QLabel("为同一批项目术语保存不同场景采用的译名，并在翻译与导出时快速切换。", self)
        description.setProperty("tbSecondary", True)
        description.setWordWrap(True)
        heading_text.addWidget(title)
        heading_text.addWidget(description)
        heading.addLayout(heading_text)
        heading.addStretch(1)
        self.create_button = QPushButton("从术语来源创建…", self)
        self.create_button.setAccessibleName("从术语来源创建译名方案")
        self.create_button.setProperty("tbTerminologyPrimary", True)
        self.manage_button = QPushButton("管理方案…", self)
        self.manage_button.setAccessibleName("管理译名方案")
        ComponentStyle.apply_static(self.manage_button, ComponentKind.BUTTON)
        heading.addWidget(self.manage_button)
        heading.addWidget(self.create_button)
        layout.addLayout(heading)

        current_card = QFrame(self)
        current_card.setProperty("tbTerminologyCard", True)
        current_layout = QVBoxLayout(current_card)
        current_layout.setContentsMargins(20, 18, 20, 18)
        current_layout.setSpacing(8)
        current_title = QLabel("当前翻译版本使用的方案", current_card)
        current_title.setProperty("tbTerminologySectionTitle", True)
        self.scheme_combo = QComboBox(current_card)
        self.scheme_combo.setAccessibleName("当前译名方案")
        self.scheme_combo.setMinimumWidth(280)
        ComponentStyle.apply_static(self.scheme_combo, ComponentKind.INPUT)
        self.status_label = QLabel("打开工程后可选择译名方案。", current_card)
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("tbSecondary", True)
        current_layout.addWidget(current_title)
        current_layout.addWidget(self.scheme_combo)
        current_layout.addWidget(self.status_label)
        layout.addWidget(current_card)

        source_card = QFrame(self)
        source_card.setProperty("tbTerminologySoftCard", True)
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(20, 18, 20, 18)
        source_layout.setSpacing(7)
        source_title = QLabel("从已有术语创建", source_card)
        source_title.setProperty("tbTerminologySectionTitle", True)
        source_text = QLabel(
            "可选择项目插件的动态词库、当前绑定的 ParaTranz 术语，或本地 JSON、CSV、Excel 文件。"
            "系统会先展示匹配和冲突预览，再创建一份与来源独立的方案。",
            source_card,
        )
        source_text.setWordWrap(True)
        source_layout.addWidget(source_title)
        source_layout.addWidget(source_text)
        layout.addWidget(source_card)
        layout.addStretch(1)

        self.scheme_combo.currentIndexChanged.connect(self._selection_changed)
        self.create_button.clicked.connect(self.create_requested)
        self.manage_button.clicked.connect(self.manage_requested)

    def render(self, state) -> None:
        self.scheme_combo.blockSignals(True)
        try:
            self.scheme_combo.clear()
            self.scheme_combo.addItem("不应用方案（保持项目译文）", None)
            for choice in state.choices:
                self.scheme_combo.addItem(choice.label, choice.profile_id)
            selected = self.scheme_combo.findData(state.selected_profile_id)
            self.scheme_combo.setCurrentIndex(max(selected, 0))
        finally:
            self.scheme_combo.blockSignals(False)
        self.scheme_combo.setEnabled(state.enabled)
        self.create_button.setEnabled(state.can_manage)
        self.manage_button.setEnabled(state.can_manage)
        self.status_label.setText(state.detail)

    def render_unavailable(self) -> None:
        from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBarState

        self.render(TerminologyProfileBarState(detail="当前运行入口没有译名方案服务。请从完整桌面入口打开。"))

    def _selection_changed(self, index: int) -> None:
        if index >= 0:
            self.selection_requested.emit(self.scheme_combo.itemData(index))


__all__ = ["TerminologySchemesView"]
