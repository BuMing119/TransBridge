"""
步骤3：操作面板。
四个独立操作卡片：导出 JSON、上传 ParaTranz、下载合并、写回 ESP。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QFileDialog, QMessageBox, QProgressBar,
    QDialog, QDialogButtonBox, QRadioButton, QLineEdit,
    QListWidget, QListWidgetItem, QStackedWidget, QCheckBox,
)
from PyQt6.QtCore import Qt

from src.transbridge.converter.translation_entry_collection_export import (
    export_to_categorized_json_files,
)
from src.transbridge.paratranz.workflow.uploader import ParaTranzUploader
from src.transbridge.paratranz.workflow.downloader import ParaTranzDownloader
from src.transbridge.writer.plugin_writer import PluginWriter
from ..workers import ApiWorker
from .project_prompt_overlay import ProjectPromptOverlay


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


class _FileSelectDialog(QDialog):
    """下载合并文件选择对话框：从项目文件列表中勾选要参与合并的文件。"""

    def __init__(self, config, project_id: int, parent=None):
        super().__init__(parent)
        self._config = config
        self._project_id = project_id
        self._workers = []
        self._file_data: list[dict] = []

        self.setWindowTitle("选择要合并的文件")
        self.setMinimumSize(520, 380)

        layout = QVBoxLayout(self)

        tool_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("全不选")
        self._btn_all.setEnabled(False)
        self._btn_none.setEnabled(False)
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        tool_row.addWidget(self._btn_all)
        tool_row.addWidget(self._btn_none)
        tool_row.addStretch()
        layout.addLayout(tool_row)

        self._stack = QStackedWidget()

        loading_widget = QWidget()
        loading_layout = QVBoxLayout(loading_widget)
        loading_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label = QLabel("正在获取文件列表…")
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loading_layout.addWidget(self._loading_label)
        self._stack.addWidget(loading_widget)  # index 0

        self._list_widget = QListWidget()
        self._list_widget.itemChanged.connect(self._on_item_changed)
        self._stack.addWidget(self._list_widget)  # index 1

        layout.addWidget(self._stack)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("确认合并")
        self._ok_btn.setEnabled(False)
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._load_files()

    def _load_files(self):
        from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
        config = self._config
        project_id = self._project_id

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            return api.list_files(project_id) or []

        w = ApiWorker(_fetch)
        w.result.connect(self._on_files_loaded)
        w.error.connect(self._on_load_error)
        w.start()
        self._workers.append(w)

    def _on_files_loaded(self, files: list):
        self._file_data = files
        self._list_widget.blockSignals(True)
        for f in files:
            name = f.get("name", str(f.get("id", "")))
            total = f.get("total", 0)
            translated = f.get("translated", 0)
            label = f"{name}    {total} 条  已译 {translated} 条"
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, f["id"])
            self._list_widget.addItem(item)
        self._list_widget.blockSignals(False)
        self._stack.setCurrentIndex(1)
        self._btn_all.setEnabled(True)
        self._btn_none.setEnabled(True)
        self._update_status()

    def _on_load_error(self, msg: str):
        self._loading_label.setText(f"获取文件列表失败：{msg}")

    def _select_all(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Checked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _select_none(self):
        self._list_widget.blockSignals(True)
        for i in range(self._list_widget.count()):
            self._list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list_widget.blockSignals(False)
        self._update_status()

    def _on_item_changed(self, _item):
        self._update_status()

    def _update_status(self):
        count = 0
        total_strings = 0
        file_totals = {f["id"]: f.get("total", 0) for f in self._file_data}
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                count += 1
                total_strings += file_totals.get(item.data(Qt.ItemDataRole.UserRole), 0)
        self._status_label.setText(f"已选 {count} 个文件，共 {total_strings} 条词条")
        self._ok_btn.setEnabled(count > 0)

    def selected_file_ids(self) -> list[int]:
        result = []
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result


class _OpCard(QGroupBox):
    """单个操作卡片：标题 + 说明 + 操作按钮。"""

    def __init__(self, title: str, desc: str, btn_text: str, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        lbl = QLabel(desc)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #555;")
        layout.addWidget(lbl)
        layout.addStretch()
        self.btn = QPushButton(btn_text)
        self.btn.setFixedHeight(30)
        layout.addWidget(self.btn)


class Step3OpsWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._prev_project: dict | None = None
        self._reverting: bool = False
        self._init_ui()

        # overlay 在 _init_ui 之后创建，确保层叠在最上方
        self._overlay = ProjectPromptOverlay(self)
        self._overlay.go_to_pt.connect(lambda: ctx.navigate_to.emit(1))

        ctx.collection_changed.connect(self._on_collection_changed)
        ctx.project_selected.connect(self._on_project_changed)
        self._update_button_states()

    def _init_ui(self):
        box = QGroupBox("步骤3：操作")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)

        box_layout = QVBoxLayout(box)

        # 操作目标指示条
        indicator_row = QHBoxLayout()
        self._project_indicator = QLabel("操作目标：未选择项目")
        self._project_indicator.setStyleSheet("color: #888; font-size: 12px;")
        indicator_row.addWidget(self._project_indicator)
        indicator_row.addStretch()
        box_layout.addLayout(indicator_row)

        # 警告条（初始隐藏，手动关闭）
        self._warning_widget = QWidget()
        self._warning_widget.setStyleSheet(
            "QWidget { background: #fff3cd; border: 1px solid #ffc107; border-radius: 3px; }"
        )
        warning_row = QHBoxLayout(self._warning_widget)
        warning_row.setContentsMargins(8, 4, 4, 4)
        warning_row.setSpacing(4)
        self._warning_label = QLabel()
        self._warning_label.setStyleSheet(
            "color: #7d4e00; font-size: 12px; border: none; background: transparent;"
        )
        self._warning_label.setWordWrap(True)
        warning_row.addWidget(self._warning_label, stretch=1)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { color: #7d4e00; border: none; background: transparent; font-size: 14px; }"
            "QPushButton:hover { color: #000; }"
        )
        close_btn.clicked.connect(self._warning_widget.hide)
        warning_row.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)
        self._warning_widget.hide()
        box_layout.addWidget(self._warning_widget)

        row = QHBoxLayout()
        box_layout.addLayout(row)

        self._card_upload = _OpCard(
            "上传到 ParaTranz",
            "将集合上传到当前已选 ParaTranz 项目，可选分类或普通上传（需先在管理模式中选中项目）。",
            "上传",
        )
        self._card_upload.btn.clicked.connect(self._do_upload)

        self._card_download = _OpCard(
            "从 ParaTranz 下载合并",
            "从当前已选项目拉取译文，按 key 合并到本地集合（stage >= 1 的词条）。",
            "下载合并",
        )
        self._card_download.btn.clicked.connect(self._do_download)

        self._card_write = _OpCard(
            "写回 ESP 插件",
            "将集合中的译文写入插件副本，输出汉化版 ESP 文件。",
            "选择输出路径",
        )
        self._card_write.btn.clicked.connect(self._do_write_esp)

        for card in (self._card_upload, self._card_download, self._card_write):
            row.addWidget(card)

        # 共享进度区域（卡片行下方）
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.hide()
        self._progress_lbl = QLabel("")
        self._progress_lbl.hide()
        box_layout.addWidget(self._progress_bar)
        box_layout.addWidget(self._progress_lbl)

    # ── State management ──────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())

    def _on_collection_changed(self, _):
        self._update_button_states()

    def _on_project_changed(self, new_project):
        # 回滚分支：本次是因用户拒绝切换而触发的回滚信号，直接更新状态即可
        if self._reverting:
            self._reverting = False
            self._prev_project = new_project
            self._warning_widget.hide()
            self._update_button_states()
            self._update_project_indicator(new_project)
            return

        prev = self._prev_project
        self._prev_project = new_project
        self._update_button_states()
        self._update_project_indicator(new_project)
        self._check_and_warn(prev, new_project)

    def _update_project_indicator(self, project):
        if project:
            name = project.get("name", "未知项目")
            self._project_indicator.setText(f"操作目标：{name}")
            self._project_indicator.setStyleSheet(
                "color: #1a5276; font-size: 12px; font-weight: bold;"
            )
        else:
            self._project_indicator.setText("操作目标：未选择项目")
            self._project_indicator.setStyleSheet("color: #888; font-size: 12px;")

    def _check_and_warn(self, prev_project, new_project):
        if new_project is None:
            return

        new_id = new_project.get("id")
        new_name = new_project.get("name", "?")
        prev_id = prev_project.get("id") if prev_project else None
        is_switching = prev_project is not None and prev_id != new_id

        # 集合已加载时切换项目 → 弹窗确认
        if is_switching and self._ctx.collection is not None:
            prev_name = prev_project.get("name", "?")
            answer = QMessageBox.question(
                self,
                "切换工作台项目",
                f"工作台当前关联的是「{prev_name}」，\n是否将操作目标切换到「{new_name}」？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.No:
                self._reverting = True
                self._ctx.current_project = prev_project
                return

        # 选中了非参与项目 → 显示警告（按钮已由 _update_button_states 禁用）
        mine_ids = self._ctx.mine_project_ids
        if bool(mine_ids) and new_id not in mine_ids:
            self._show_warning(f"您可能不是「{new_name}」的成员，上传/下载操作已禁用")

    def _show_warning(self, msg: str):
        self._warning_label.setText(f"⚠  {msg}")
        self._warning_widget.show()

    def _update_button_states(self):
        has_collection = self._ctx.collection is not None
        project = self._ctx.current_project
        has_project = project is not None

        mine_ids = self._ctx.mine_project_ids
        is_member = (
            not bool(mine_ids)  # mine_ids 尚未加载，无法判断，不做限制
            or (has_project and project.get("id") in mine_ids)
        )

        self._card_upload.btn.setEnabled(has_collection and has_project and is_member)
        self._card_download.btn.setEnabled(has_collection and has_project and is_member)
        self._card_write.btn.setEnabled(has_collection)

        show_overlay = has_collection and not has_project
        self._overlay.setVisible(show_overlay)
        if show_overlay:
            self._overlay.raise_()

    # ── Operations ────────────────────────────────────────────

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
        config = self._ctx.config

        backup_dir = None
        if backup_enabled:
            backup_dir = QFileDialog.getExistingDirectory(self, "选择本地备份目录")
            if not backup_dir:
                return  # 用户取消了目录选择，中止整个操作

        if mode == "categorized":
            def _upload_factory(progress_cb):
                if backup_dir:
                    export_to_categorized_json_files(collection, backup_dir)
                uploader = ParaTranzUploader(config)
                return uploader.upload_collection(
                    collection, project_id=project_id, progress_callback=progress_cb)
        else:
            def _upload_factory(progress_cb):
                uploader = ParaTranzUploader(config)
                return uploader.upload_collection_as_single(
                    collection, project_id=project_id,
                    filename=filename, progress_callback=progress_cb)

        def _on_done(result):
            QMessageBox.information(
                self, "上传完成",
                f"新建：{result.created} 个\n更新：{result.updated} 个\n跳过：{result.skipped} 个",
            )

        self._run_worker(fn_factory=_upload_factory, on_result=_on_done,
                         on_error=lambda e: QMessageBox.critical(self, "上传失败", e),
                         progress_total=0, progress_msg="正在上传到 ParaTranz…")

    def _do_download(self):
        collection = self._ctx.collection
        project = self._ctx.current_project
        if not collection or not project:
            return
        project_id = project.get("id")
        config = self._ctx.config

        dlg = _FileSelectDialog(config, project_id, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        file_ids = dlg.selected_file_ids()

        def _download_factory(progress_cb):
            downloader = ParaTranzDownloader(config)
            return downloader.download_to_collection(
                project_id, collection,
                file_ids=file_ids, progress_callback=progress_cb)

        def _on_done(result):
            self._ctx.collection_changed.emit(self._ctx.collection)
            QMessageBox.information(
                self, "下载合并完成",
                f"已合并：{result.merged} 条\n"
                f"未匹配：{result.skipped_no_match} 条\n"
                f"跳过（stage 不足）：{result.skipped_low_stage} 条",
            )

        self._run_worker(fn_factory=_download_factory, on_result=_on_done,
                         on_error=lambda e: QMessageBox.critical(self, "下载失败", e),
                         progress_total=0, progress_msg="正在从 ParaTranz 下载合并…")

    def _do_write_esp(self):
        from sse_plugin_interface.plugin import SSEPlugin

        collection = self._ctx.collection
        if not collection:
            return

        esp_path = self._ctx.esp_path
        if not esp_path:
            QMessageBox.warning(self, "未找到插件路径",
                                "请先在步骤1中解析插件文件，再执行写回操作。")
            return

        src = Path(esp_path)
        default_name = src.stem + "_translated" + src.suffix
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存汉化插件", str(src.parent / default_name),
            "ESP/ESM 文件 (*.esp *.esm);;所有文件 (*)",
        )
        if not save_path:
            return

        def _write():
            plugin = SSEPlugin.from_file(src)
            writer = PluginWriter(plugin)
            count = writer.apply_collection(collection)
            writer.write(save_path)
            return count

        def _on_done(count):
            QMessageBox.information(
                self, "写回完成",
                f"已写入 {count} 条译文，保存至：\n{save_path}",
            )

        self._run_worker(_write, on_result=_on_done,
                         on_error=lambda e: QMessageBox.critical(self, "写回失败", e),
                         progress_total=0, progress_msg="正在写回 ESP 插件…")

    # ── Progress helpers ───────────────────────────────────────

    def _show_progress(self, total: int, msg: str = ""):
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(0)
        else:
            self._progress_bar.setRange(0, 0)  # 不确定模式
        self._progress_lbl.setText(msg)
        self._progress_bar.show()
        self._progress_lbl.show()

    def _update_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
        self._progress_lbl.setText(msg)

    def _hide_progress(self):
        self._progress_bar.hide()
        self._progress_lbl.hide()
        self._progress_bar.setValue(0)
        self._progress_lbl.setText("")

    # ── Helper ────────────────────────────────────────────────

    def _run_worker(self, fn=None, *, fn_factory=None, on_result, on_error,
                    progress_total: int = 0, progress_msg: str = ""):
        btn_states = {
            self._card_upload.btn: self._card_upload.btn.isEnabled(),
            self._card_download.btn: self._card_download.btn.isEnabled(),
            self._card_write.btn: self._card_write.btn.isEnabled(),
        }
        for btn in btn_states:
            btn.setEnabled(False)

        self._show_progress(progress_total, progress_msg)

        def _restore():
            self._hide_progress()
            for btn, state in btn_states.items():
                btn.setEnabled(state)
            self._update_button_states()

        if fn_factory is not None:
            # Create worker with a placeholder, then wire the progress callback into fn_factory
            # before start() so the background thread sees the real callback.
            _cb_ref = [None]
            def _wrapped():
                return fn_factory(_cb_ref[0])
            w = ApiWorker(_wrapped)
            _cb_ref[0] = w.make_progress_callback()
        else:
            w = ApiWorker(fn)

        w.result.connect(on_result)
        w.error.connect(on_error)
        w.progress.connect(self._update_progress)
        w.finished.connect(_restore)
        w.start()
        self._workers.append(w)
        return w
