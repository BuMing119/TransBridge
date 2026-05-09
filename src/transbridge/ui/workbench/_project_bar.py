"""项目工具栏：项目名/版本切换/保存/管理按钮。"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QMenu, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, QTimer


class ProjectBar(QWidget):
    """工作台顶部项目工具栏——项目+版本选择+保存+管理按钮。"""

    new_project_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    variant_switch_requested = pyqtSignal(str)   # variant_name
    save_requested = pyqtSignal()
    variant_add_requested = pyqtSignal()
    variant_copy_requested = pyqtSignal()
    variant_delete_requested = pyqtSignal(str)   # variant_name
    project_rename_requested = pyqtSignal(str)   # new_name

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)

        # ── 项目名（可编辑） ──
        layout.addWidget(QLabel("项目:"))
        self._project_label = QLabel("无项目")
        self._project_label.setStyleSheet("font-weight: bold; color: #1565C0;")
        layout.addWidget(self._project_label)

        self._rename_btn = QPushButton("...")
        self._rename_btn.setFixedWidth(28)
        self._rename_btn.setFlat(True)
        self._rename_btn.setToolTip("修改项目名称")
        self._rename_btn.clicked.connect(self._on_rename_project)
        layout.addWidget(self._rename_btn)

        # ── 版本下拉 ──
        layout.addWidget(QLabel(" 版本:"))
        self._variant_combo = QComboBox()
        self._variant_combo.setMinimumWidth(100)
        self._variant_combo.currentIndexChanged.connect(self._on_variant_selected)
        layout.addWidget(self._variant_combo)

        # ── 版本管理按钮 ──
        self._variant_menu_btn = QPushButton("+")
        self._variant_menu_btn.setFixedWidth(28)
        self._variant_menu_btn.setFlat(True)
        self._variant_menu_btn.setToolTip("管理版本")
        self._variant_menu_btn.clicked.connect(self._on_variant_menu)
        layout.addWidget(self._variant_menu_btn)

        # ── 保存按钮 ──
        self._save_btn = QPushButton("保存")
        self._save_btn.setToolTip("保存当前版本的翻译数据 (Ctrl+S)")
        self._save_btn.setStyleSheet(
            "QPushButton { color: #1565C0; font-weight: bold; padding: 3px 10px; }"
        )
        self._save_btn.clicked.connect(self.save_requested.emit)
        layout.addWidget(self._save_btn)

        layout.addStretch()

        # ── 项目管理按钮 ──
        self._btn_new = QPushButton("+ 新建项目")
        self._btn_new.setFlat(True)
        self._btn_new.clicked.connect(self.new_project_requested.emit)
        layout.addWidget(self._btn_new)

        self._btn_open = QPushButton("打开项目")
        self._btn_open.setFlat(True)
        self._btn_open.clicked.connect(self.open_project_requested.emit)
        layout.addWidget(self._btn_open)

        # 监听状态变化
        ctx.workspace_changed.connect(self.refresh)
        ctx.variant_changed.connect(self._on_external_variant_change)

    # ── 版本下拉 ──────────────────────────────────────────────

    def _on_variant_selected(self, index: int):
        if index < 0:
            return
        name = self._variant_combo.currentData()
        if name and name != self._ctx.active_variant:
            self.variant_switch_requested.emit(name)

    def _on_external_variant_change(self, name: str):
        self._variant_combo.blockSignals(True)
        try:
            for i in range(self._variant_combo.count()):
                if self._variant_combo.itemData(i) == name:
                    self._variant_combo.setCurrentIndex(i)
                    break
        finally:
            self._variant_combo.blockSignals(False)

    # ── 版本管理菜单 ──────────────────────────────────────────

    def _on_variant_menu(self):
        menu = QMenu(self)
        menu.addAction("新建版本...", lambda: self.variant_add_requested.emit())
        menu.addAction("复制当前版本...", lambda: self.variant_copy_requested.emit())

        proj = self._ctx.active_project
        variants = proj.variants if proj else []
        if len(variants) > 1:
            del_menu = menu.addMenu("删除版本")
            for v in variants:
                vname = v["name"]
                del_menu.addAction(vname, lambda n=vname: self.variant_delete_requested.emit(n))

        menu.addSeparator()
        menu.addAction("管理快照...", lambda: None)  # 预留

        menu.exec(self._variant_menu_btn.mapToGlobal(
            self._variant_menu_btn.rect().bottomLeft()))

    # ── 重命名项目 ────────────────────────────────────────────

    def _on_rename_project(self):
        proj = self._ctx.active_project
        if not proj:
            return
        name, ok = QInputDialog.getText(
            self, "修改项目名称", "新名称:", text=proj.name)
        if ok and name.strip() and name.strip() != proj.name:
            self.project_rename_requested.emit(name.strip())

    # ── 刷新 ──────────────────────────────────────────────────

    def refresh(self):
        proj = self._ctx.active_project
        variant = self._ctx.active_variant

        self._variant_combo.blockSignals(True)
        try:
            self._variant_combo.clear()
            if proj and proj.variants:
                for v in proj.variants:
                    self._variant_combo.addItem(v["name"], v["name"])
                if variant:
                    for i in range(self._variant_combo.count()):
                        if self._variant_combo.itemData(i) == variant:
                            self._variant_combo.setCurrentIndex(i)
                            break
        finally:
            self._variant_combo.blockSignals(False)

        if proj:
            self._project_label.setText(proj.name)
            self._rename_btn.setVisible(True)
            self._variant_combo.setVisible(True)
            self._variant_menu_btn.setVisible(True)
            self._save_btn.setVisible(True)
            self._btn_new.setVisible(False)
            self._btn_open.setVisible(False)
        else:
            self._project_label.setText("无项目 - 请新建或打开项目")
            self._rename_btn.setVisible(False)
            self._variant_combo.setVisible(False)
            self._variant_menu_btn.setVisible(False)
            self._save_btn.setVisible(False)
            self._btn_new.setVisible(True)
            self._btn_open.setVisible(True)

    # ── 保存反馈 ──────────────────────────────────────────────

    def flash_saved(self):
        self._save_btn.setStyleSheet(
            "QPushButton { color: white; background: #4CAF50; font-weight: bold; "
            "padding: 3px 10px; border-radius: 4px; }"
        )
        self._save_btn.setText("已保存")
        QTimer.singleShot(1500, self._reset_save_style)

    def _reset_save_style(self):
        self._save_btn.setStyleSheet(
            "QPushButton { color: #1565C0; font-weight: bold; padding: 3px 10px; }"
        )
        self._save_btn.setText("保存")

    def set_save_dirty(self, dirty: bool):
        if dirty:
            self._save_btn.setStyleSheet(
                "QPushButton { color: #E65100; font-weight: bold; padding: 3px 10px; }"
            )
            self._save_btn.setText("保存 *")
        else:
            self._save_btn.setStyleSheet(
                "QPushButton { color: #1565C0; font-weight: bold; padding: 3px 10px; }"
            )
            self._save_btn.setText("保存")
