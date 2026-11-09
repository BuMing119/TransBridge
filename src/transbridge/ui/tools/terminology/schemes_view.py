# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compact output-naming selector for the project terminology workbench."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle


class TerminologySchemesView(QFrame):
    """Switch output naming in context and keep creation behind management."""

    create_requested = pyqtSignal()
    paratranz_requested = pyqtSignal()
    create_from_base_requested = pyqtSignal()
    manage_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    source_requested = pyqtSignal()
    extract_requested = pyqtSignal()
    rename_requested = pyqtSignal()
    selection_requested = pyqtSignal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminologySourceBar")
        self.setAccessibleName("译名方案")
        self._project_editable = False
        self._selected_profile_id = None
        self._can_import = False
        self._import_busy = False
        self._paratranz_available = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(10)

        identity = QVBoxLayout()
        identity.setSpacing(0)
        selector = QHBoxLayout()
        selector.setSpacing(12)
        title = QLabel("翻译时使用", self)
        title.setProperty("tbSecondary", True)
        selector.addWidget(title)
        self.scheme_combo = QComboBox(self)
        self.scheme_combo.setAccessibleName("当前译名方案")
        self.scheme_combo.setMinimumWidth(190)
        ComponentStyle.apply_static(self.scheme_combo, ComponentKind.INPUT)
        selector.addWidget(self.scheme_combo, 1)
        identity.addLayout(selector)
        self.metadata = QLabel("使用项目默认译名", self)
        self.metadata.setProperty("tbSecondary", True)
        self.metadata.hide()
        identity.addWidget(self.metadata)
        layout.addLayout(identity, 1)

        self.import_button = QPushButton("导入术语…", self)
        self.import_button.setProperty("tbTerminologyPrimary", True)
        self.import_button.setToolTip("选择 JSON、CSV 或 Excel 术语文件，预览后创建独立方案")
        self.paratranz_button = QPushButton("ParaTranz 未绑定", self)
        self.paratranz_button.setEnabled(False)
        self.edit_button = QPushButton("编辑当前术语…", self)
        self.edit_button.hide()
        for button in (self.import_button, self.paratranz_button, self.edit_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            layout.addWidget(button)
        self.actions_button = QPushButton("更多…", self)
        self.actions_button.setAccessibleName("译名方案操作")
        ComponentStyle.apply_static(self.actions_button, ComponentKind.BUTTON)
        self.actions_menu = QMenu(self.actions_button)
        self.extract_action = self.actions_menu.addAction("从项目提取术语…")
        self.source_action = self.actions_menu.addAction("方案全部条目…")
        self.actions_menu.addSeparator()
        self.new_action = self.actions_menu.addAction("基于项目术语新建方案…")
        self.rename_action = self.actions_menu.addAction("重命名当前方案…")
        self.manage_action = self.actions_menu.addAction("管理译名方案…")
        self.actions_menu.addSeparator()
        self.context_action = self.actions_menu.addAction("正在读取术语状态…")
        self.context_action.setEnabled(False)
        self.actions_button.setMenu(self.actions_menu)
        layout.addWidget(self.actions_button)

        self.status_label = QLabel("", self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        identity.addWidget(self.status_label)

        self.scheme_combo.currentIndexChanged.connect(self._selection_changed)
        self.import_button.clicked.connect(self.create_requested)
        self.paratranz_button.clicked.connect(self.paratranz_requested)
        self.manage_action.triggered.connect(self.manage_requested)
        self.new_action.triggered.connect(self.create_from_base_requested)
        self.edit_button.clicked.connect(self.edit_requested)
        self.rename_action.triggered.connect(self.rename_requested)
        self.source_action.triggered.connect(self.source_requested)
        self.extract_action.triggered.connect(self.extract_requested)

    def render(self, state) -> None:
        self._selected_profile_id = state.selected_profile_id
        self.scheme_combo.blockSignals(True)
        try:
            self.scheme_combo.clear()
            self.scheme_combo.addItem("使用项目默认译名", None)
            for choice in state.choices:
                self.scheme_combo.addItem(choice.label, choice.profile_id)
            selected = self.scheme_combo.findData(state.selected_choice_id)
            self.scheme_combo.setCurrentIndex(max(selected, 0))
        finally:
            self.scheme_combo.blockSignals(False)
        self.scheme_combo.setEnabled(state.enabled)
        self.actions_button.setEnabled(state.can_manage)
        self._can_import = state.can_manage
        self.set_import_busy(self._import_busy)
        self.manage_action.setEnabled(state.can_manage)
        self.new_action.setEnabled(state.can_manage)
        self.edit_button.setEnabled(state.can_manage)
        self.edit_button.setText("编辑当前方案…" if state.selected_profile_id is not None else "编辑当前术语…")
        self.set_project_editable(self._project_editable)
        self.rename_action.setEnabled(state.can_manage and state.selected_profile_id is not None)
        self.source_action.setEnabled(state.enabled and state.selected_profile_id is not None)
        self.rename_action.setToolTip(
            "重命名当前译名方案"
            if state.selected_profile_id is not None
            else "项目默认译名不是独立方案；可先点击“新建方案”创建并命名。"
        )
        if state.selection_error:
            self.status_label.setText(state.selection_error)
        elif not state.enabled:
            self.status_label.setText(state.detail)
        elif state.selected_profile_id is None:
            self.status_label.setText("使用项目默认译名")
        else:
            selected = next(
                (choice.label for choice in state.choices if choice.profile_id == state.selected_choice_id),
                "所选译名方案",
            )
            self.status_label.setText(selected)
        self.scheme_combo.setAccessibleDescription(self.status_label.text())
        revision = getattr(state, "selected_revision", None)
        self.metadata.setText(
            f"{len(revision.content.mappings):,} 条方案映射 · 预览修订 {revision.revision}"
            if revision is not None and state.enabled
            else ""
        )
        self.metadata.hide()
        self.context_action.setText(self.metadata.text() or "使用项目默认译名")
        self.metadata.setToolTip("方案映射数量；当前生效术语还取决于项目术语和适用范围。")
        self.status_label.setVisible(not state.enabled or bool(state.selection_error))
        self.show()

    def set_project_editable(self, available: bool) -> None:
        self._project_editable = available
        self.edit_button.setVisible(available or self._selected_profile_id is not None)

    def set_import_busy(self, busy: bool) -> None:
        self._import_busy = busy
        self.import_button.setEnabled(self._can_import and not busy)
        self.paratranz_button.setEnabled(self._can_import and self._paratranz_available and not busy)

    def set_paratranz_target(self, available: bool, *, bound: bool = False, detail: str = "") -> None:
        self._paratranz_available = available
        self.paratranz_button.setText(
            "ParaTranz 导入…" if available else "ParaTranz 不可用" if bound else "ParaTranz 未绑定"
        )
        self.paratranz_button.setToolTip(detail)
        self.set_import_busy(self._import_busy)

    def render_unavailable(self) -> None:
        self.hide()

    def _selection_changed(self, index: int) -> None:
        if index >= 0:
            self.selection_requested.emit(self.scheme_combo.itemData(index))


__all__ = ["TerminologySchemesView"]
