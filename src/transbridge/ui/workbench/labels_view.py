"""Label-management view for the Workbench preview."""

from __future__ import annotations

import uuid

from PyQt6.QtCore import QSignalBlocker, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from transbridge.ui.foundation.accessibility import contrast_ratio, update_accessible_state
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog

from ._theme_support import readable_user_color

PRESET_COLORS = (
    "#2196F3",
    "#FF9800",
    "#4CAF50",
    "#F44336",
    "#9C27B0",
    "#00BCD4",
    "#795548",
    "#607D8B",
)


class LabelManagerDialog(QDialog):
    """Edit a detached label-library value; persistence remains with the caller."""

    def __init__(self, label_library: dict, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        configure_dialog(self)
        self._labels = {label_id: dict(info) for label_id, info in label_library.items()}
        self._selected_id: str | None = None
        self._selected_color = PRESET_COLORS[0]
        self._new_name = ""
        self._new_color = PRESET_COLORS[0]
        self._color_buttons: dict[str, QPushButton] = {}
        self.setWindowTitle("管理标签")
        self.resize(580, 340)
        self._init_ui()
        self._refresh_list()
        if theme_view is not None:
            theme_view.subscribe(self, self._apply_theme)

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        self._new_button = QPushButton("新建标签")
        ComponentStyle.apply_static(self._new_button, ComponentKind.BUTTON)
        self._new_button.clicked.connect(self._start_new)
        self._new_button.setEnabled(False)
        left.addWidget(self._new_button)
        self._list = QListWidget()
        self._list.setProperty("tbTaskList", True)
        self._list.setFixedWidth(180)
        self._list.currentRowChanged.connect(self._on_select)
        left.addWidget(self._list, 1)
        layout.addLayout(left)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(QLabel("标签名称:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入标签名称")
        self._name_edit.setAccessibleName("标签名称")
        self._name_edit.textEdited.connect(self._rename_selected)
        self._name_edit.textChanged.connect(self._clear_feedback)
        ComponentStyle.apply_static(self._name_edit, ComponentKind.INPUT)
        right.addWidget(self._name_edit)
        right.addWidget(QLabel("颜色:"))
        color_row = QGridLayout()
        color_row.setSpacing(4)
        for index, color in enumerate(PRESET_COLORS):
            button = QPushButton()
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            button.setFixedSize(44, 32)
            button.setIconSize(QSize(16, 16))
            button.setAutoDefault(False)
            button.setCheckable(True)
            button.setAccessibleName(f"标签颜色 {color}")
            self._color_buttons[color] = button
            self._apply_color_button(button, color)
            button.clicked.connect(lambda checked, value=color: self._on_color_pick(value))
            color_row.addWidget(button, index // 4, index % 4)
        right.addLayout(color_row)

        button_row = QHBoxLayout()
        self._add_button = QPushButton("添加标签")
        self._add_button.clicked.connect(self._on_add)
        button_row.addWidget(self._add_button)
        self._delete_button = QPushButton("删除")
        self._delete_button.clicked.connect(self._on_delete)
        self._delete_button.setEnabled(False)
        button_row.addWidget(self._delete_button)
        button_row.addStretch()
        right.addLayout(button_row)
        hint = QLabel("修改仅在点击“保存并关闭”后生效。")
        hint.setWordWrap(True)
        right.addWidget(hint)
        self._feedback = QLabel()
        self._feedback.setWordWrap(True)
        self._feedback.setTextFormat(Qt.TextFormat.PlainText)
        self._feedback.setAccessibleName("标签输入检查结果")
        ComponentStyle.apply_static(self._feedback, ComponentKind.NOTIFICATION)
        self._feedback.hide()
        right.addWidget(self._feedback)
        right.addStretch()

        bottom = QHBoxLayout()
        ok_button = QPushButton("保存并关闭")
        ok_button.setDefault(True)
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        for button in (self._add_button, self._delete_button, ok_button, cancel_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(ok_button, SemanticState.PRIMARY)
        ComponentStyle.apply_state(self._delete_button, SemanticState.ERROR)
        bottom.addStretch()
        bottom.addWidget(ok_button)
        bottom.addWidget(cancel_button)
        right.addLayout(bottom)
        layout.addLayout(right)

    def _refresh_list(self) -> None:
        with QSignalBlocker(self._list):
            self._list.clear()
            for label_id, info in self._labels.items():
                item = QListWidgetItem(f"● {info['name'] or '（名称未填写）'}")
                item.setData(Qt.ItemDataRole.UserRole, label_id)
                item.setForeground(readable_user_color(info.get("color"), self._list.palette()))
                self._list.addItem(item)
                if label_id == self._selected_id:
                    self._list.setCurrentItem(item)

    def _start_new(self) -> None:
        with QSignalBlocker(self._list):
            self._list.setCurrentRow(-1)
        self._on_select(-1)
        self._name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _rename_selected(self, name: str) -> None:
        if self._selected_id is None:
            self._new_name = name
        else:
            self._labels[self._selected_id]["name"] = name.strip()
            item = self._list.currentItem()
            if item is not None:
                item.setText(f"● {name.strip() or '（名称未填写）'}")

    def _clear_feedback(self) -> None:
        self._feedback.clear()
        self._feedback.hide()
        self._feedback.setAccessibleDescription("")

    def _show_name_error(self) -> None:
        self._feedback.setText("请输入标签名称后再保存。")
        self._feedback.show()
        ComponentStyle.apply_state(self._feedback, SemanticState.ERROR)
        update_accessible_state(self._feedback, self._feedback.text())
        self._name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _on_select(self, row: int) -> None:
        if row < 0:
            self._selected_id = None
            self._name_edit.setText(self._new_name)
            self._selected_color = self._new_color
            self._delete_button.setEnabled(False)
            self._new_button.setEnabled(False)
            self._add_button.show()
            self._refresh_color_buttons()
            return
        item = self._list.item(row)
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        info = self._labels[self._selected_id]
        self._name_edit.setText(info["name"])
        self._selected_color = info["color"]
        self._delete_button.setEnabled(True)
        self._new_button.setEnabled(True)
        self._add_button.hide()
        self._refresh_color_buttons()

    def _on_color_pick(self, color: str) -> None:
        self._selected_color = color
        self._refresh_color_buttons()
        if self._selected_id:
            self._labels[self._selected_id]["color"] = color
            self._refresh_list()
        else:
            self._new_color = color

    def _on_add(self) -> None:
        if self._selected_id is not None:
            return
        name = self._name_edit.text().strip()
        if not name:
            self._show_name_error()
            return
        self._selected_id = uuid.uuid4().hex[:8]
        self._labels[self._selected_id] = {"name": name, "color": self._selected_color}
        self._new_name = ""
        self._refresh_list()
        self._on_select(self._list.currentRow())

    def _on_delete(self) -> None:
        if not self._selected_id:
            return
        del self._labels[self._selected_id]
        self._selected_id = None
        self._refresh_list()
        self._start_new()

    def accept(self) -> None:
        for label_id, info in self._labels.items():
            if not info["name"].strip():
                self._selected_id = label_id
                self._refresh_list()
                self._on_select(self._list.currentRow())
                self._show_name_error()
                return
        if self._selected_id is None:
            self._new_name = self._name_edit.text()
        if self._new_name.strip():
            self._selected_id = uuid.uuid4().hex[:8]
            self._labels[self._selected_id] = {"name": self._new_name.strip(), "color": self._new_color}
            self._new_name = ""
            self._refresh_list()
            self._on_select(self._list.currentRow())
        super().accept()

    def get_label_library(self) -> dict:
        return self._labels

    def _apply_color_button(self, button: QPushButton, color: str) -> None:
        candidate = QColor(color)
        selected = color == self._selected_color
        dpr = button.devicePixelRatioF()
        pixmap = QPixmap(round(16 * dpr), round(16 * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(candidate)
        if selected:
            foreground = QColor("white" if contrast_ratio("#ffffff", color) >= 4.5 else "black")
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(foreground, 2))
            painter.drawLine(3, 8, 6, 11)
            painter.drawLine(6, 11, 13, 4)
            painter.end()
        button.setIcon(QIcon(pixmap))
        button.setChecked(selected)
        button.setAccessibleDescription("已选择" if selected else "未选择")

    def _refresh_color_buttons(self) -> None:
        for color, button in self._color_buttons.items():
            self._apply_color_button(button, color)

    def _apply_theme(self, _snapshot) -> None:
        self._refresh_list()
        self._refresh_color_buttons()
