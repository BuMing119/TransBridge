"""Compact Workbench selector for naming schemes."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, ElidedLabel


@dataclass(frozen=True, slots=True)
class TerminologyProfileChoice:
    profile_id: str
    label: str


@dataclass(frozen=True, slots=True)
class TerminologyProfileBarState:
    choices: tuple[TerminologyProfileChoice, ...] = ()
    selected_profile_id: str | None = None
    enabled: bool = False
    can_manage: bool = False
    detail: str = "打开工程后可选择译名方案。"


class TerminologyProfileBar(QWidget):
    """Render profile state and emit selection intent without owning data."""

    selection_requested = pyqtSignal(object)
    manage_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("译名方案")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 4)
        row.setSpacing(6)

        row.addWidget(QLabel("译名方案 ·", self))
        self.combo = QComboBox(self)
        self.combo.setAccessibleName("当前译名方案")
        self.combo.setMinimumWidth(180)
        self.combo.setMinimumContentsLength(14)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ComponentStyle.apply_static(self.combo, ComponentKind.INPUT)
        self.combo.currentIndexChanged.connect(self._selection_changed)
        row.addWidget(self.combo)

        self.detail = ElidedLabel(parent=self)
        self.detail.setAccessibleName("译名方案状态")
        row.addWidget(self.detail, 1)

        self.manage_button = QPushButton("管理…", self)
        self.manage_button.setAccessibleName("管理译名方案")
        ComponentStyle.apply_static(self.manage_button, ComponentKind.BUTTON)
        self.manage_button.clicked.connect(self.manage_requested.emit)
        row.addWidget(self.manage_button)

        self.render(TerminologyProfileBarState())

    def render(self, state: TerminologyProfileBarState) -> None:
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            self.combo.addItem("不应用方案（保持项目译文）", None)
            for choice in state.choices:
                self.combo.addItem(choice.label, choice.profile_id)
            selected = self.combo.findData(state.selected_profile_id)
            self.combo.setCurrentIndex(max(selected, 0))
        finally:
            self.combo.blockSignals(False)
        self.combo.setEnabled(state.enabled)
        self.manage_button.setEnabled(state.can_manage)
        self.manage_button.setToolTip(
            "创建、复制、编辑、应用或归档译名方案。" if state.can_manage else "打开工程后可管理译名方案。"
        )
        self.detail.set_full_text(state.detail)
        self.detail.setToolTip(state.detail)
        self.detail.setAccessibleDescription(state.detail)

    def _selection_changed(self, index: int) -> None:
        if index >= 0:
            self.selection_requested.emit(self.combo.itemData(index))


__all__ = [
    "TerminologyProfileBar",
    "TerminologyProfileBarState",
    "TerminologyProfileChoice",
]
