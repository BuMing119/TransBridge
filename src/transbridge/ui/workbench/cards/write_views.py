"""Dialog views for write-card interactions."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SlotSelectDialog(QDialog):
    """插件槽位选择对话框，用于批量操作时选择要操作的插件。"""

    def __init__(self, title: str, slots: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        hint = QLabel("选择要操作的插件：")
        hint.setAccessibleName("写回选择说明")
        layout.addWidget(hint)

        # 全选/全不选 按钮
        btn_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        btn_row.addWidget(self._btn_all)
        btn_row.addWidget(self._btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(4)

        self._checkboxes: dict[str, QCheckBox] = {}
        for key, slot in slots.items():
            label = slot.label or Path(key).stem
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { spacing: 4px; }")
            cb._slot_key = key
            container_layout.addWidget(cb)
            self._checkboxes[key] = cb
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 状态标签
        self._status_label = QLabel(f"已选 {len(slots)} 个插件")
        self._status_label.setAccessibleName("写回选择状态")
        layout.addWidget(self._status_label)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 连接信号
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        for cb in self._checkboxes.values():
            cb.stateChanged.connect(self._update_status)

    def _select_all(self):
        for cb in self._checkboxes.values():
            cb.setChecked(True)

    def _select_none(self):
        for cb in self._checkboxes.values():
            cb.setChecked(False)

    def _update_status(self):
        count = sum(1 for cb in self._checkboxes.values() if cb.isChecked())
        self._status_label.setText(f"已选 {count} 个插件")
        self._ok_btn.setEnabled(count > 0)

    def selected_slots(self) -> list:
        """返回选中的 slot key 列表。"""
        return [cb._slot_key for cb in self._checkboxes.values() if cb.isChecked()]


class BatchConfirmDialog(QDialog):
    """批量操作确认对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setAccessibleName("批量写回确认汇总")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        btn_box.button(QDialogButtonBox.StandardButton.Yes).setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.No).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class BatchResultDialog(QDialog):
    """批量操作结果对话框，带滚动区域。"""

    def __init__(self, title: str, header: str, items: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        # 标题说明
        header_lbl = QLabel(header)
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setAccessibleName("批量写回结果汇总")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


class WriteTargetDialog(QDialog):
    """写回目标选择对话框。"""

    def __init__(self, eet_path: str | None, xt_path: str | None, has_esp: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择写回目标")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        group = QButtonGroup(self)

        # ── ESP ───────────────────────────────────────────────
        self._rb_esp = QRadioButton("写回 ESP 插件")
        self._rb_esp.setChecked(has_esp)
        self._rb_esp.setEnabled(has_esp)
        if has_esp:
            esp_desc = QLabel("将译文写入插件副本，输出汉化版 ESP 文件。")
        else:
            esp_desc = QLabel("当前集合由 EET XML 构建，无法写回 ESP 插件。")
        esp_desc.setStyleSheet("margin-left: 20px;")
        group.addButton(self._rb_esp)
        layout.addWidget(self._rb_esp)
        layout.addWidget(esp_desc)
        layout.addSpacing(6)

        # ── EET XML ───────────────────────────────────────────
        self._rb_eet = QRadioButton("写回 EET XML")
        eet_desc = QLabel("将译文更新到 EET XML 文件中。")
        eet_desc.setStyleSheet("margin-left: 20px;")
        eet_path_row = QHBoxLayout()
        eet_path_lbl = QLabel("路径：")
        eet_path_lbl.setStyleSheet("margin-left: 20px;")
        eet_path_lbl.setFixedWidth(40)
        self._eet_input = QLineEdit(eet_path or "")
        self._eet_input.setPlaceholderText("选择 EET XML 文件…")
        self._eet_input.setEnabled(False)
        self._eet_browse = QPushButton("浏览")
        self._eet_browse.setFixedWidth(50)
        self._eet_browse.setEnabled(False)
        self._eet_browse.clicked.connect(self._browse_eet)
        eet_path_row.addWidget(eet_path_lbl)
        eet_path_row.addWidget(self._eet_input)
        eet_path_row.addWidget(self._eet_browse)
        group.addButton(self._rb_eet)
        layout.addWidget(self._rb_eet)
        layout.addWidget(eet_desc)
        layout.addLayout(eet_path_row)
        layout.addSpacing(6)

        # ── XT XML ────────────────────────────────────────────
        self._rb_xt = QRadioButton("导出 XT XML")
        xt_desc = QLabel("将译文更新到 XT XML 文件中。")
        xt_desc.setStyleSheet("margin-left: 20px;")
        xt_path_row = QHBoxLayout()
        xt_path_lbl = QLabel("路径：")
        xt_path_lbl.setStyleSheet("margin-left: 20px;")
        xt_path_lbl.setFixedWidth(40)
        self._xt_input = QLineEdit(xt_path or "")
        self._xt_input.setPlaceholderText("选择 XT XML 文件…")
        self._xt_input.setEnabled(False)
        self._xt_browse = QPushButton("浏览")
        self._xt_browse.setFixedWidth(50)
        self._xt_browse.setEnabled(False)
        self._xt_browse.clicked.connect(self._browse_xt)
        xt_path_row.addWidget(xt_path_lbl)
        xt_path_row.addWidget(self._xt_input)
        xt_path_row.addWidget(self._xt_browse)
        group.addButton(self._rb_xt)
        layout.addWidget(self._rb_xt)
        layout.addWidget(xt_desc)
        layout.addLayout(xt_path_row)
        layout.addSpacing(6)

        # ── DSD JSON ──────────────────────────────────────────
        self._rb_dsd = QRadioButton("导出 DSD JSON")
        dsd_desc = QLabel("导出为 DSD 格式 JSON，用于 xEdit 脚本等外部工具。")
        dsd_desc.setStyleSheet("margin-left: 20px;")
        group.addButton(self._rb_dsd)
        layout.addWidget(self._rb_dsd)
        layout.addWidget(dsd_desc)
        layout.addSpacing(8)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认写回")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._rb_eet.toggled.connect(self._on_mode_changed)
        self._rb_xt.toggled.connect(self._on_mode_changed)
        self._eet_input.textChanged.connect(self._update_ok)
        self._xt_input.textChanged.connect(self._update_ok)

        # 无 ESP 时默认选中 EET
        if not has_esp:
            self._rb_eet.setChecked(True)
            self._on_mode_changed()

    def _on_mode_changed(self):
        eet = self._rb_eet.isChecked()
        xt = self._rb_xt.isChecked()
        self._eet_input.setEnabled(eet)
        self._eet_browse.setEnabled(eet)
        self._xt_input.setEnabled(xt)
        self._xt_browse.setEnabled(xt)
        self._update_ok()

    def _update_ok(self):
        if self._rb_eet.isChecked():
            self._ok_btn.setEnabled(bool(self._eet_input.text().strip()))
        elif self._rb_xt.isChecked():
            self._ok_btn.setEnabled(bool(self._xt_input.text().strip()))
        else:
            self._ok_btn.setEnabled(True)

    def _browse_eet(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 EET XML 文件", self._eet_input.text(), "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._eet_input.setText(path)

    def _browse_xt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 XT XML 文件", self._xt_input.text(), "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._xt_input.setText(path)

    @property
    def target(self) -> str:
        if self._rb_eet.isChecked():
            return "eet"
        if self._rb_xt.isChecked():
            return "xt"
        if self._rb_dsd.isChecked():
            return "dsd"
        return "esp"

    @property
    def eet_path(self) -> str:
        return self._eet_input.text().strip()

    @property
    def xt_path(self) -> str:
        return self._xt_input.text().strip()
