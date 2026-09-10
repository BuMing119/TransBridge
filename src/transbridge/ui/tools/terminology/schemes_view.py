"""Compact output-naming selector for the project terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologySchemesView(QFrame):
    """Switch output naming in context and keep creation behind management."""

    create_requested = pyqtSignal()
    manage_requested = pyqtSignal()
    selection_requested = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminologySourceBar")
        self.setAccessibleName("当前术语")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(16)

        identity = QVBoxLayout()
        identity.setSpacing(6)
        selector = QHBoxLayout()
        selector.setSpacing(12)
        title = QLabel("当前术语", self)
        title.setProperty("tbSecondary", True)
        selector.addWidget(title)
        self.scheme_combo = QComboBox(self)
        self.scheme_combo.setAccessibleName("当前术语副本")
        self.scheme_combo.setMinimumWidth(280)
        self.scheme_combo.setMaximumWidth(480)
        ComponentStyle.apply_static(self.scheme_combo, ComponentKind.INPUT)
        selector.addWidget(self.scheme_combo, 1)
        selector.addStretch(1)
        identity.addLayout(selector)
        self.metadata = QLabel("未选用", self)
        self.metadata.setProperty("tbSecondary", True)
        identity.addWidget(self.metadata)
        layout.addLayout(identity, 1)

        self.actions_button = QPushButton("选用术语源…", self)
        self.actions_button.setAccessibleName("选用术语源")
        ComponentStyle.apply_static(self.actions_button, ComponentKind.BUTTON)
        self.actions_menu = QMenu(self.actions_button)
        self.create_action = self.actions_menu.addAction("从术语源导入副本…")
        self.manage_action = self.actions_menu.addAction("管理术语副本…")
        self.actions_button.setMenu(self.actions_menu)
        layout.addWidget(self.actions_button)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        identity.addWidget(self.status_label)

        self.scheme_combo.currentIndexChanged.connect(self._selection_changed)
        self.create_action.triggered.connect(self.create_requested)
        self.manage_action.triggered.connect(self.manage_requested)

    def render(self, state) -> None:
        self.scheme_combo.blockSignals(True)
        try:
            self.scheme_combo.clear()
            self.scheme_combo.addItem("无", None)
            for choice in state.choices:
                self.scheme_combo.addItem(choice.label, choice.profile_id)
            selected = self.scheme_combo.findData(state.selected_profile_id)
            self.scheme_combo.setCurrentIndex(max(selected, 0))
        finally:
            self.scheme_combo.blockSignals(False)
        self.scheme_combo.setEnabled(state.enabled)
        self.actions_button.setEnabled(state.can_manage)
        self.create_action.setEnabled(state.can_manage)
        self.manage_action.setEnabled(state.can_manage)
        if state.selection_error:
            self.status_label.setText(state.selection_error)
        elif not state.enabled:
            self.status_label.setText(state.detail)
        elif state.selected_profile_id is None:
            self.status_label.setText("无")
        else:
            selected = next(
                (choice.label for choice in state.choices if choice.profile_id == state.selected_profile_id),
                "所选译名版本",
            )
            self.status_label.setText(selected)
        self.scheme_combo.setAccessibleDescription(self.status_label.text())
        revision = getattr(state, "selected_revision", None)
        self.metadata.setText(
            f"独立副本 · {len(revision.content.mappings):,} 条术语 · 第 {revision.revision} 版"
            if revision is not None and state.enabled
            else ""
        )
        self.metadata.setVisible(revision is not None and state.enabled)
        self.status_label.setVisible(not state.enabled or bool(state.selection_error))
        self.show()

    def render_unavailable(self) -> None:
        self.hide()

    def _selection_changed(self, index: int) -> None:
        if index >= 0:
            self.selection_requested.emit(self.scheme_combo.itemData(index))


__all__ = ["TerminologySchemesView"]
