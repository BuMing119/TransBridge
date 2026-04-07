from pathlib import Path
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QRadioButton, QLabel, QLineEdit, QCheckBox, QFileDialog, QMessageBox,
    QButtonGroup, QFrame, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QWidget, QCheckBox as QtCheckBox, QComboBox,
)

from src.transbridge.converter.translation_entry_collection_export import (
    export_to_categorized_json_files,
    get_categorized_file_names,
)
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader, ConflictInfo, FileMaps, FileMaps
from .base import OpCard


@dataclass
class BatchUploadResult:
    """批量上传结果汇总。"""
    success_count: int = 0
    failed_count: int = 0
    details: list[str] = field(default_factory=list)


class _BatchConfirmDialog(QDialog):
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
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        btn_box.button(QDialogButtonBox.StandardButton.Yes).setText("确认")
        btn_box.button(QDialogButtonBox.StandardButton.No).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


class _SlotSelectDialog(QDialog):
    """插件槽位选择对话框，用于批量操作时选择要操作的插件。"""

    def __init__(self, title: str, slots: dict, parent=None):
        """
        Args:
            slots: ctx.slots 字典 {key: CollectionSlot}
        """
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        self.setMinimumHeight(200)
        self.setMaximumHeight(400)

        layout = QVBoxLayout(self)

        hint = QLabel("选择要操作的插件：")
        hint.setStyleSheet("color: #555;")
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
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(4)

        self._checkboxes: dict[str, QtCheckBox] = {}
        for key, slot in slots.items():
            label = slot.label or Path(key).stem
            cb = QtCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { spacing: 4px; }")
            cb._slot_key = key  # 存储 slot key
            container_layout.addWidget(cb)
            self._checkboxes[key] = cb
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 状态标签
        self._status_label = QLabel(f"已选 {len(slots)} 个插件")
        self._status_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._status_label)

        # 按钮
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
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
        """返回选中的 slot 列表。"""
        return [self._checkboxes[key]._slot_key for key, cb in self._checkboxes.items() if cb.isChecked()]


class _BatchResultDialog(QDialog):
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
        scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(2)

        for item in items:
            lbl = QLabel(item)
            lbl.setStyleSheet("color: #333;")
            container_layout.addWidget(lbl)
        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # 提示信息
        footer = QLabel(f"共 {len(items)} 个项目")
        footer.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(footer)

        # 按钮
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)


class _ConflictResolveDialog(QDialog):
    """
    上传前冲突解决对话框。
    每个冲突文件显示一个下拉框，让用户选择要更新 ParaTranz 中哪个同名文件。
    """

    def __init__(self, conflicts: list[ConflictInfo], parent=None):
        super().__init__(parent)
        self.setWindowTitle("检测到同名文件冲突")
        self.setMinimumWidth(520)
        self.setMinimumHeight(200)
        self.setMaximumHeight(500)

        layout = QVBoxLayout(self)

        hint = QLabel(
            f"检测到 {len(conflicts)} 个文件在 ParaTranz 中存在多个同名副本。\n"
            "请为每个文件选择要更新的目标文件："
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        layout.addWidget(sep)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setSpacing(8)
        container_layout.setContentsMargins(4, 4, 4, 4)

        self._combos: dict[str, QComboBox] = {}  # local_name -> QComboBox

        for conflict in conflicts:
            row = QHBoxLayout()
            name_lbl = QLabel(conflict.local_name)
            name_lbl.setMinimumWidth(180)
            name_lbl.setStyleSheet("font-weight: bold;")
            row.addWidget(name_lbl)

            combo = QComboBox()
            for f in conflict.candidates:
                folder = f.get("folder", "") or "根目录"
                label = f"{folder}  (id={f['id']})"
                combo.addItem(label, userData=f["id"])
            row.addWidget(combo, stretch=1)

            container_layout.addLayout(row)
            self._combos[conflict.local_name] = combo

        container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("继续上传")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def resolved_path_mapping(self, conflicts: list[ConflictInfo]) -> dict[str, int]:
        """返回用户选择的 {local_name: file_id} 映射。"""
        result = {}
        for conflict in conflicts:
            combo = self._combos[conflict.local_name]
            result[conflict.local_name] = combo.currentData()
        return result


class _UploadModeDialog(QDialog):
    """上传模式选择对话框：分类上传 vs 普通上传。"""

    def __init__(self, esp_path: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择上传模式")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._rb_cat = QRadioButton("分类上传（推荐）")
        self._rb_cat.setChecked(True)
        cat_desc = QLabel("按词条类型拆分为多个文件分别上传。")
        cat_desc.setStyleSheet("color: #555; margin-left: 20px;")

        self._chk_backup = QCheckBox("同时导出本地备份")
        self._chk_backup.setStyleSheet("margin-left: 20px;")

        self._rb_plain = QRadioButton("普通上传")
        plain_desc = QLabel("全部词条合并为单个 JSON 文件上传。")
        plain_desc.setStyleSheet("color: #555; margin-left: 20px;")

        fn_row = QHBoxLayout()
        fn_label = QLabel("文件名：")
        fn_label.setStyleSheet("margin-left: 20px;")
        default_fn = (Path(esp_path).stem + ".json") if esp_path else "collection.json"
        self._fn_edit = QLineEdit(default_fn)
        self._fn_edit.setEnabled(False)
        fn_row.addWidget(fn_label)
        fn_row.addWidget(self._fn_edit)

        layout.addWidget(self._rb_cat)
        layout.addWidget(cat_desc)
        layout.addWidget(self._chk_backup)
        layout.addSpacing(8)
        layout.addWidget(self._rb_plain)
        layout.addWidget(plain_desc)
        layout.addLayout(fn_row)
        layout.addSpacing(8)

        # ── 已存在文件处理方式 ──────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        layout.addWidget(sep)

        section_label = QLabel("已存在文件处理方式（仅对 ParaTranz 中已有同名文件时生效）：")
        section_label.setStyleSheet("color: #555; font-size: 12px;")
        layout.addWidget(section_label)

        # 主选项组
        self._rb_orig_only = QRadioButton("仅更新原文（不改动 ParaTranz 上已有的译文）")
        self._rb_orig_only.setChecked(True)

        self._rb_trans_only = QRadioButton("仅导入译文（不更新原文；新建文件将被跳过）")

        # 仅译文的子选项
        self._rb_trans_safe = QRadioButton("安全导入（不覆盖已人工编辑的词条）")
        self._rb_trans_safe.setChecked(True)
        self._rb_trans_force = QRadioButton("强制覆盖（覆盖所有译文，包括已人工编辑的）")
        self._rb_trans_safe.setStyleSheet("margin-left: 32px;")
        self._rb_trans_force.setStyleSheet("margin-left: 32px;")

        self._rb_both = QRadioButton("更新原文并导入译文（安全模式，不覆盖已人工编辑的词条）")

        self._main_mode_group = QButtonGroup(self)
        for rb in (self._rb_orig_only, self._rb_trans_only, self._rb_both):
            rb.setStyleSheet("margin-left: 12px;")
            self._main_mode_group.addButton(rb)
            layout.addWidget(rb)
            if rb is self._rb_trans_only:
                layout.addWidget(self._rb_trans_safe)
                layout.addWidget(self._rb_trans_force)

        self._trans_sub_group = QButtonGroup(self)
        self._trans_sub_group.addButton(self._rb_trans_safe)
        self._trans_sub_group.addButton(self._rb_trans_force)

        layout.addSpacing(8)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认上传")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._rb_plain.toggled.connect(self._on_upload_mode_changed)
        self._rb_trans_only.toggled.connect(self._on_trans_only_toggled)
        self._fn_edit.textChanged.connect(self._update_ok)

        self._on_trans_only_toggled(False)

    def _on_upload_mode_changed(self, plain_checked: bool):
        self._fn_edit.setEnabled(plain_checked)
        self._chk_backup.setEnabled(not plain_checked)
        self._update_ok()

    def _on_trans_only_toggled(self, checked: bool):
        self._rb_trans_safe.setEnabled(checked)
        self._rb_trans_force.setEnabled(checked)

    def _update_ok(self):
        if self._rb_plain.isChecked():
            self._ok_btn.setEnabled(bool(self._fn_edit.text().strip()))
        else:
            self._ok_btn.setEnabled(True)

    @property
    def mode(self) -> str:
        return "plain" if self._rb_plain.isChecked() else "categorized"

    @property
    def filename(self) -> str:
        return self._fn_edit.text().strip()

    @property
    def backup_enabled(self) -> bool:
        return self._chk_backup.isChecked() and self._chk_backup.isEnabled()

    @property
    def translation_mode(self) -> str:
        if self._rb_trans_only.isChecked():
            return "trans_force" if self._rb_trans_force.isChecked() else "trans_safe"
        if self._rb_both.isChecked():
            return "both"
        return "orig_only"


class _BatchUploadModeDialog(QDialog):
    """批量上传模式选择对话框（简化版，无文件名输入）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量上传设置")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # 说明文字
        hint = QLabel("每个插件将作为单个 JSON 文件上传（不分类）。")
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        layout.addWidget(sep)

        # 已存在文件处理方式
        section_label = QLabel("已存在文件处理方式（仅对 ParaTranz 中已有同名文件时生效）：")
        section_label.setStyleSheet("color: #555; font-size: 12px;")
        layout.addWidget(section_label)

        # 主选项组
        self._rb_orig_only = QRadioButton("仅更新原文（不改动 ParaTranz 上已有的译文）")
        self._rb_orig_only.setChecked(True)

        self._rb_trans_only = QRadioButton("仅导入译文（不更新原文；新建文件将被跳过）")

        # 仅译文的子选项
        self._rb_trans_safe = QRadioButton("安全导入（不覆盖已人工编辑的词条）")
        self._rb_trans_safe.setChecked(True)
        self._rb_trans_force = QRadioButton("强制覆盖（覆盖所有译文，包括已人工编辑的）")
        self._rb_trans_safe.setStyleSheet("margin-left: 32px;")
        self._rb_trans_force.setStyleSheet("margin-left: 32px;")

        self._rb_both = QRadioButton("更新原文并导入译文（安全模式，不覆盖已人工编辑的词条）")

        self._main_mode_group = QButtonGroup(self)
        for rb in (self._rb_orig_only, self._rb_trans_only, self._rb_both):
            rb.setStyleSheet("margin-left: 12px;")
            self._main_mode_group.addButton(rb)
            layout.addWidget(rb)
            if rb is self._rb_trans_only:
                layout.addWidget(self._rb_trans_safe)
                layout.addWidget(self._rb_trans_force)

        self._trans_sub_group = QButtonGroup(self)
        self._trans_sub_group.addButton(self._rb_trans_safe)
        self._trans_sub_group.addButton(self._rb_trans_force)

        layout.addSpacing(8)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("开始上传")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._rb_trans_only.toggled.connect(self._on_trans_only_toggled)
        self._on_trans_only_toggled(False)

    def _on_trans_only_toggled(self, checked: bool):
        self._rb_trans_safe.setEnabled(checked)
        self._rb_trans_force.setEnabled(checked)

    @property
    def translation_mode(self) -> str:
        if self._rb_trans_only.isChecked():
            return "trans_force" if self._rb_trans_force.isChecked() else "trans_safe"
        if self._rb_both.isChecked():
            return "both"
        return "orig_only"


class _FileSelectionDialog(QDialog):
    """分类上传时，让用户选择哪些文件参与上传（默认全选）。"""

    def __init__(self, file_infos: list[tuple[str, int]], parent=None):
        """
        Args:
            file_infos: list of (filename, entry_count)
        """
        super().__init__(parent)
        self.setWindowTitle("选择要上传的文件")
        self.setMinimumWidth(420)
        self.setMinimumHeight(300)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        hint = QLabel(f"共 {len(file_infos)} 个分类文件，请选择要上传的文件（默认全选）：")
        hint.setStyleSheet("color: #555;")
        layout.addWidget(hint)

        # 全选 / 全不选 按钮行
        btn_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        btn_row.addWidget(self._btn_all)
        btn_row.addWidget(self._btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 文件列表
        self._list = QListWidget()
        for filename, count in file_infos:
            item = QListWidgetItem(f"{filename}  ({count} 条)")
            item.setData(Qt.ItemDataRole.UserRole, filename)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        layout.addWidget(self._list)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认上传")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        self._list.itemChanged.connect(self._update_ok)
        self._update_ok()

    def _select_all(self):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)

    def _select_none(self):
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def _update_ok(self):
        self._ok_btn.setEnabled(any(
            self._list.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self._list.count())
        ))

    @property
    def selected_files(self) -> set[str]:
        return {
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        }


class UploadCard(OpCard):

    def __init__(self, ctx, run_worker, parent=None):
        super().__init__(
            "上传到 ParaTranz",
            "将集合上传到当前已选 ParaTranz 项目，可选分类或普通上传（需先在管理模式中选中项目）。",
            "上传",
            parent,
        )
        self._ctx = ctx
        self._run_worker = run_worker
        self.btn.clicked.connect(self._do_upload)
        self.batch_btn.clicked.connect(self._do_batch_upload)

    def update_batch_visibility(self):
        """更新批量按钮可见性（由 step3 调用）。"""
        slots = self._ctx.slots
        self.set_batch_visible(len(slots) > 1)

    def _do_batch_upload(self):
        """批量上传入口。"""
        slots = self._ctx.slots
        if len(slots) <= 1:
            return

        project = self._ctx.current_project
        if not project:
            QMessageBox.warning(self, "未选择项目", "请先在 ParaTranz 管理面板中选择目标项目。")
            return

        # 弹出插件选择对话框
        dlg = _SlotSelectDialog("批量上传 - 选择插件", slots, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_keys = dlg.selected_slots()
        if not selected_keys:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量上传。")
            return

        selected_slots = [slots[k] for k in selected_keys]

        # 弹出上传模式选择对话框
        mode_dlg = _BatchUploadModeDialog(parent=self)
        if mode_dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.do_batch_upload(selected_slots, project, mode_dlg.translation_mode)

    def do_batch_upload(self, selected_slots: list, project: dict, translation_mode: str = "orig_only"):
        """执行批量上传。"""
        project_id = project.get("id")
        project_name = project.get("name", "?")

        # 确认对话框
        slot_names = [s.label or Path(s.esp_path).stem for s in selected_slots]
        items = [f"• {name}.json" for name in slot_names]
        header = f"即将上传到项目「{project_name}」\n每个插件将作为单个 JSON 文件上传（不分类）。"

        dlg = _BatchConfirmDialog("确认批量上传", header, items, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        config = self._ctx.config

        def _batch_upload_factory(progress_cb):
            results = BatchUploadResult()
            uploader = ParaTranzUploader(config)
            total = len(selected_slots)

            for i, slot in enumerate(selected_slots):
                if progress_cb:
                    slot_name = slot.label or Path(slot.esp_path).stem
                    progress_cb(i, total, f"正在上传 {slot_name}…")

                filename = (Path(slot.esp_path).stem + ".json") if slot.esp_path else f"{slot.label}.json"
                try:
                    result = uploader.upload_collection_as_single(
                        slot.collection,
                        project_id=project_id,
                        filename=filename,
                        translation_mode=translation_mode,
                    )
                    results.success_count += 1
                    msg = f"✓ {filename}: 新建 {result.created}, 更新 {result.updated}"
                    if translation_mode != "orig_only" and result.translation_updated > 0:
                        msg += f", 导入译文 {result.translation_updated}"
                    results.details.append(msg)
                except Exception as e:
                    results.failed_count += 1
                    results.details.append(f"✗ {filename}: {e}")

            if progress_cb:
                progress_cb(total, total, "上传完成")
            return results

        def _on_done(result: BatchUploadResult):
            header = (
                f"成功：{result.success_count} 个\n"
                f"失败：{result.failed_count} 个"
            )

            # 检查是否有冲突警告
            has_conflicts = any(
                hasattr(r, 'name_conflicts') and r.name_conflicts
                for r in getattr(result, 'individual_results', [])
            )
            if has_conflicts:
                QMessageBox.warning(
                    self,
                    "文件名冲突警告",
                    "部分插件检测到文件名冲突。\n"
                    "请在 ParaTranz 中检查文件是否被移动或存在重复。"
                )

            dlg = _BatchResultDialog(
                "批量上传完成", header, result.details, parent=self
            )
            dlg.exec()

        self._run_worker(
            fn_factory=_batch_upload_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "批量上传失败", e),
            progress_total=len(selected_slots),
            progress_msg="正在批量上传…",
        )

    def _do_upload(self):
        collection = self._ctx.collection
        project = self._ctx.current_project
        if not collection or not project:
            return
        project_id = project.get("id")

        dlg = _UploadModeDialog(self._ctx.esp_path, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        mode = dlg.mode
        filename = dlg.filename
        backup_enabled = dlg.backup_enabled
        translation_mode = dlg.translation_mode
        config = self._ctx.config

        backup_dir = None
        if backup_enabled:
            backup_dir = QFileDialog.getExistingDirectory(self, "选择本地备份目录")
            if not backup_dir:
                return

        def _on_done(result):
            parts = [f"新建：{result.created} 个", f"更新原文：{result.updated} 个", f"跳过：{result.skipped} 个"]
            if translation_mode != "orig_only":
                parts.append(f"导入译文：{result.translation_updated} 个")
            QMessageBox.information(self, "上传完成", "\n".join(parts))

        if mode == "categorized":
            # 预计算文件列表，让用户选择
            file_infos = get_categorized_file_names(collection)
            file_filter = None
            if len(file_infos) > 1:
                sel_dlg = _FileSelectionDialog(file_infos, parent=self)
                if sel_dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                file_filter = sel_dlg.selected_files

            local_names = file_filter if file_filter is not None else {name for name, _ in file_infos}

            # Phase 1: 预检冲突（后台查询 ParaTranz 文件列表）
            def _detect_factory(progress_cb):
                uploader = ParaTranzUploader(config)
                return uploader.detect_conflicts(project_id, local_names, progress_callback=progress_cb)

            def _on_conflicts_detected(result: tuple):
                conflicts, file_maps = result  # 解构：冲突列表 + 已获取的文件映射
                file_id_override: dict[str, int] = {}

                if conflicts:
                    dlg = _ConflictResolveDialog(conflicts, parent=self)
                    if dlg.exec() != QDialog.DialogCode.Accepted:
                        return  # 用户取消
                    file_id_override = dlg.resolved_path_mapping(conflicts)

                # Phase 2: 实际上传，传入 prefetched_maps 避免重复 API 调用
                def _upload_factory(progress_cb):
                    if backup_dir:
                        export_to_categorized_json_files(collection, backup_dir)
                    uploader = ParaTranzUploader(config)
                    return uploader.upload_collection(
                        collection, project_id=project_id,
                        file_filter=file_filter,
                        translation_mode=translation_mode,
                        file_id_override=file_id_override or None,
                        prefetched_maps=file_maps,
                        progress_callback=progress_cb,
                    )

                self._run_worker(
                    fn_factory=_upload_factory,
                    on_result=_on_done,
                    on_error=lambda e: QMessageBox.critical(self, "上传失败", str(e)),
                    progress_total=0,
                    progress_msg="正在上传到 ParaTranz…",
                )

            self._run_worker(
                fn_factory=_detect_factory,
                on_result=_on_conflicts_detected,
                on_error=lambda e: QMessageBox.critical(self, "冲突检测失败", str(e)),
                progress_total=0,
                progress_msg="正在检测文件冲突…",
            )
        else:
            def _upload_factory(progress_cb):
                uploader = ParaTranzUploader(config)
                return uploader.upload_collection_as_single(
                    collection, project_id=project_id,
                    filename=filename, translation_mode=translation_mode,
                    progress_callback=progress_cb)

            self._run_worker(
                fn_factory=_upload_factory,
                on_result=_on_done,
                on_error=lambda e: QMessageBox.critical(self, "上传失败", str(e)),
                progress_total=0,
                progress_msg="正在上传到 ParaTranz…",
            )
