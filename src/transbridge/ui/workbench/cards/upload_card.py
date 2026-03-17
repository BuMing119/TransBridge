from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QRadioButton, QLabel, QLineEdit, QCheckBox, QFileDialog, QMessageBox,
    QButtonGroup, QFrame,
)

from src.transbridge.converter.translation_entry_collection_export import (
    export_to_categorized_json_files,
)
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader
from .base import OpCard


class _UploadModeDialog(QDialog):
    """上传模式选择对话框：分类上传 vs 普通上传。"""

    def __init__(self, esp_path: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择上传模式")
        self.setMinimumWidth(380)

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

        self._rb_trans_none = QRadioButton("仅更新原文（不改动 ParaTranz 上已有的译文）")
        self._rb_trans_none.setChecked(True)
        self._rb_trans_safe = QRadioButton("同时导入译文（不覆盖 ParaTranz 上已人工编辑的词条）")
        self._rb_trans_force = QRadioButton("强制覆盖译文（覆盖 ParaTranz 上所有译文，包括已人工编辑的）")

        self._trans_group = QButtonGroup(self)
        for rb in (self._rb_trans_none, self._rb_trans_safe, self._rb_trans_force):
            rb.setStyleSheet("margin-left: 12px;")
            self._trans_group.addButton(rb)
            layout.addWidget(rb)

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

        self._rb_plain.toggled.connect(self._on_mode_changed)
        self._fn_edit.textChanged.connect(self._update_ok)

    def _on_mode_changed(self, plain_checked: bool):
        self._fn_edit.setEnabled(plain_checked)
        self._chk_backup.setEnabled(not plain_checked)
        self._update_ok()

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
        if self._rb_trans_force.isChecked():
            return "force"
        if self._rb_trans_safe.isChecked():
            return "safe"
        return "none"


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

        if mode == "categorized":
            def _upload_factory(progress_cb):
                if backup_dir:
                    export_to_categorized_json_files(collection, backup_dir)
                uploader = ParaTranzUploader(config)
                return uploader.upload_collection(
                    collection, project_id=project_id,
                    translation_mode=translation_mode, progress_callback=progress_cb)
        else:
            def _upload_factory(progress_cb):
                uploader = ParaTranzUploader(config)
                return uploader.upload_collection_as_single(
                    collection, project_id=project_id,
                    filename=filename, translation_mode=translation_mode,
                    progress_callback=progress_cb)

        def _on_done(result):
            msg = f"新建：{result.created} 个\n更新：{result.updated} 个\n跳过：{result.skipped} 个"
            if translation_mode != "none":
                msg += f"\n导入译文：{result.translation_updated} 个"
            QMessageBox.information(self, "上传完成", msg)

        self._run_worker(
            fn_factory=_upload_factory,
            on_result=_on_done,
            on_error=lambda e: QMessageBox.critical(self, "上传失败", e),
            progress_total=0,
            progress_msg="正在上传到 ParaTranz…",
        )
