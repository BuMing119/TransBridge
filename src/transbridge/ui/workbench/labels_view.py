"""Label-management view for the Workbench preview."""

from __future__ import annotations

import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

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

    def __init__(self, label_library: dict, parent=None):
        super().__init__(parent)
        self._labels = {label_id: dict(info) for label_id, info in label_library.items()}
        self._selected_id: str | None = None
        self._selected_color = PRESET_COLORS[0]
        self.setWindowTitle("管理标签")
        self.resize(420, 320)
        self._init_ui()
        self._refresh_list()

    def _init_ui(self) -> None:
        layout = QHBoxLayout(self)
        self._list = QListWidget()
        self._list.setFixedWidth(180)
        self._list.currentRowChanged.connect(self._on_select)
        layout.addWidget(self._list)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(QLabel("标签名称:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入标签名称")
        right.addWidget(self._name_edit)
        right.addWidget(QLabel("颜色:"))
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for color in PRESET_COLORS:
            button = QPushButton()
            button.setFixedSize(24, 24)
            button.setStyleSheet(
                f"background: {color}; border-radius: 12px; border: 2px solid "
                f"{'#333' if color == self._selected_color else 'transparent'};"
            )
            button.clicked.connect(lambda checked, value=color: self._on_color_pick(value))
            color_row.addWidget(button)
        right.addLayout(color_row)

        button_row = QHBoxLayout()
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._on_add)
        button_row.addWidget(add_button)
        self._delete_button = QPushButton("删除")
        self._delete_button.clicked.connect(self._on_delete)
        self._delete_button.setEnabled(False)
        button_row.addWidget(self._delete_button)
        right.addLayout(button_row)
        right.addStretch()

        bottom = QHBoxLayout()
        ok_button = QPushButton("确定")
        ok_button.clicked.connect(self.accept)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        bottom.addStretch()
        bottom.addWidget(ok_button)
        bottom.addWidget(cancel_button)
        right.addLayout(bottom)
        layout.addLayout(right)

    def _refresh_list(self) -> None:
        self._list.clear()
        for label_id, info in self._labels.items():
            item = QListWidgetItem(f"● {info['name']}")
            item.setData(Qt.ItemDataRole.UserRole, label_id)
            item.setForeground(QColor(info["color"]))
            self._list.addItem(item)

    def _on_select(self, row: int) -> None:
        if row < 0:
            self._selected_id = None
            self._name_edit.clear()
            self._delete_button.setEnabled(False)
            return
        item = self._list.item(row)
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        info = self._labels[self._selected_id]
        self._name_edit.setText(info["name"])
        self._selected_color = info["color"]
        self._delete_button.setEnabled(True)

    def _on_color_pick(self, color: str) -> None:
        self._selected_color = color
        if self._selected_id:
            self._labels[self._selected_id]["color"] = color
            self._refresh_list()

    def _on_add(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            return
        self._labels[uuid.uuid4().hex[:8]] = {"name": name, "color": self._selected_color}
        self._name_edit.clear()
        self._refresh_list()

    def _on_delete(self) -> None:
        if not self._selected_id:
            return
        del self._labels[self._selected_id]
        self._selected_id = None
        self._name_edit.clear()
        self._delete_button.setEnabled(False)
        self._refresh_list()

    def get_label_library(self) -> dict:
        return self._labels
