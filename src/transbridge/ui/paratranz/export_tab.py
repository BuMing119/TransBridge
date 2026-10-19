"""
ExportTab: 导出管理标签页，显示最近导出信息、触发新导出、下载压缩包。
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from transbridge.paratranz.workflow.artifact import ArtifactWorkflow
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, ElidedLabel

from ..workers import ApiWorker


class ExportTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._project_id: int | None = None
        self._download_allowed = True  # 由项目 download 字段决定
        self._gen = 0
        self._operation_gen = 0
        self._busy = False
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)
        ctx.paratranz_permissions_changed.connect(self._refresh_permissions)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 最近导出信息
        info_box = QGroupBox("最近导出信息")
        form = QFormLayout(info_box)
        self._lbl_time = QLabel("—")
        self._lbl_total = QLabel("—")
        self._lbl_translated = QLabel("—")
        self._lbl_reviewed = QLabel("—")
        self._lbl_duration = QLabel("—")
        for label, widget in (
            ("导出时间", self._lbl_time),
            ("词条总数", self._lbl_total),
            ("已翻译", self._lbl_translated),
            ("已审核", self._lbl_reviewed),
            ("压缩耗时", self._lbl_duration),
        ):
            form.addRow(label + ":", widget)
        self._info_status = QLabel("选择项目查看导出信息")
        self._info_status.setTextFormat(Qt.TextFormat.PlainText)
        self._info_status.setWordWrap(True)
        form.addRow(self._info_status)
        self._refresh_btn = QPushButton("刷新导出信息")
        ComponentStyle.apply_static(self._refresh_btn, ComponentKind.BUTTON)
        self._refresh_btn.setEnabled(False)
        self._refresh_btn.clicked.connect(self.load_artifacts)
        form.addRow(self._refresh_btn)
        layout.addWidget(info_box)

        # 操作区
        action_box = QGroupBox("操作")
        action_layout = QVBoxLayout(action_box)

        self._status_slot = QWidget()
        status_layout = QVBoxLayout(self._status_slot)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(2)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.hide()
        status_layout.addWidget(self._progress_bar)

        self._status_lbl = ElidedLabel("")
        status_layout.addWidget(self._status_lbl)
        self._status_slot.setFixedHeight(
            self._progress_bar.sizeHint().height() + status_layout.spacing() + self._status_lbl.sizeHint().height()
        )
        action_layout.addWidget(self._status_slot)

        btn_row = QHBoxLayout()
        self._trigger_btn = QPushButton("触发新导出")
        self._trigger_btn.setFixedHeight(32)
        self._trigger_btn.clicked.connect(self._trigger_export)
        self._download_btn = QPushButton("下载导出包")
        self._download_btn.setFixedHeight(32)
        self._download_btn.clicked.connect(self._download_artifacts)
        for button in (self._trigger_btn, self._download_btn):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        btn_row.addWidget(self._trigger_btn)
        btn_row.addWidget(self._download_btn)
        btn_row.addStretch()
        action_layout.addLayout(btn_row)
        layout.addWidget(action_box)

        layout.addStretch()

        self._set_buttons_enabled(False)

    def _on_project_changed(self, project: dict | None):
        self._gen += 1
        self._operation_gen += 1
        self._project_id = project.get("id") if project else None
        self._busy = False
        self._progress_bar.hide()
        self._set_empty()
        if self._project_id:
            self._set_buttons_enabled(True)
            self._check_download_permission(project)
            self.load_artifacts()

    def _check_download_permission(self, project: dict):
        self._download_btn.setToolTip("")
        download = project.get("download", 0)
        is_admin = self._ctx.is_admin()
        is_member = self._ctx.is_member()
        if download == 0:
            self._download_allowed = True
            self._download_btn.setToolTip("")
        elif download == 1:
            self._download_allowed = is_member
            if not is_member:
                self._download_btn.setToolTip("仅项目成员可下载")
        else:  # 2 = 私密
            self._download_allowed = is_admin
            if not is_admin:
                self._download_btn.setToolTip("仅管理员可下载")
        self._download_btn.setEnabled(bool(self._project_id) and self._download_allowed)

        # 触发导出仅管理员可用
        self._trigger_btn.setEnabled(bool(self._project_id) and is_admin)
        if self._busy:
            self._set_buttons_enabled(False)

    def _refresh_permissions(self):
        project = self._ctx.current_project
        if project and project.get("id") == self._project_id:
            self._check_download_permission(project)
        else:
            self._set_buttons_enabled(False)

    def _set_empty(self):
        for lbl in (self._lbl_time, self._lbl_total, self._lbl_translated, self._lbl_reviewed, self._lbl_duration):
            lbl.setText("—")
        self._set_status("")
        self._info_status.setText("选择项目查看导出信息")
        self._refresh_btn.setEnabled(False)
        self._set_buttons_enabled(False)

    def _set_buttons_enabled(self, enabled: bool):
        self._trigger_btn.setEnabled(enabled)
        self._download_btn.setEnabled(enabled)

    def load_artifacts(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        self._gen += 1
        gen = self._gen
        self._info_status.setText("正在加载导出信息…")
        self._refresh_btn.setEnabled(False)

        def _fetch():
            api = ParatranzExportAPI(token=config.token, config=config)
            return api.get_artifacts(pid)

        def _on_done(artifact):
            if self._gen != gen:
                return
            self._refresh_btn.setEnabled(True)
            self._on_artifacts_loaded(artifact)

        def _on_error(error):
            if self._gen == gen:
                self._info_status.setText(f"获取最新导出信息失败：{error}")
                self._refresh_btn.setEnabled(True)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _on_artifacts_loaded(self, artifact):
        if not artifact:
            for label in (
                self._lbl_time,
                self._lbl_total,
                self._lbl_translated,
                self._lbl_reviewed,
                self._lbl_duration,
            ):
                label.setText("—")
            self._info_status.setText("暂无导出记录")
            return
        self._info_status.clear()
        self._lbl_time.setText(str(artifact.get("createdAt", "—"))[:19])
        self._lbl_total.setText(str(artifact.get("total", "—")))
        self._lbl_translated.setText(str(artifact.get("translated", "—")))
        self._lbl_reviewed.setText(str(artifact.get("reviewed", "—")))
        duration = artifact.get("duration")
        self._lbl_duration.setText(f"{duration} ms" if duration else "—")

    def _set_busy(self, busy: bool, msg: str = ""):
        self._busy = busy
        self._progress_bar.setVisible(busy)
        self._set_status(msg)
        if busy:
            self._set_buttons_enabled(False)
        else:
            self._trigger_btn.setEnabled(bool(self._project_id) and self._ctx.is_admin())
            self._download_btn.setEnabled(bool(self._project_id) and self._download_allowed)

    def _set_status(self, message: str) -> None:
        self._status_lbl.set_full_text(message)
        self._status_lbl.setToolTip(message)
        self._status_lbl.setAccessibleDescription(message)

    def _trigger_export(self):
        if self._busy or not self._project_id:
            return
        pid = self._project_id
        save_path, _ = QFileDialog.getSaveFileName(self, "选择保存位置", "export.zip", "ZIP 文件 (*.zip)")
        if not save_path or self._project_id != pid:
            return

        config = self._ctx.config
        self._operation_gen += 1
        generation = self._operation_gen

        self._trigger_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._set_busy(True, "正在触发导出，请稍候…")

        def _progress(msg: str):
            w.progress.emit(0, 0, msg)

        def _do():
            workflow = ArtifactWorkflow(config)
            return workflow.trigger_and_download(pid, save_path, progress_callback=_progress)

        def _on_done(path):
            if not self._is_current_operation(pid, generation):
                return
            self._set_busy(False, f"已下载至：{path}")
            self.load_artifacts()

        def _on_error(e):
            if self._is_current_operation(pid, generation):
                self._set_busy(False, f"失败：{e}")

        w = ApiWorker(_do)
        w.progress.connect(
            lambda _current, _total, message: (
                self._set_status(message) if self._is_current_operation(pid, generation) else None
            )
        )
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        self._workers.append(w)
        w.start()

    def _download_artifacts(self):
        if self._busy or not self._project_id:
            return
        pid = self._project_id
        save_path, _ = QFileDialog.getSaveFileName(self, "保存导出包", "export.zip", "ZIP 文件 (*.zip)")
        if not save_path or self._project_id != pid:
            return
        config = self._ctx.config
        self._operation_gen += 1
        generation = self._operation_gen

        self._trigger_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._set_busy(True, "正在下载导出包…")

        def _download():
            api = ParatranzExportAPI(token=config.token, config=config)
            api.download_artifacts(pid, save_path)
            return save_path

        def _on_done(p):
            if self._is_current_operation(pid, generation):
                self._set_busy(False, f"已保存至：{p}")

        def _on_error(e):
            if not self._is_current_operation(pid, generation):
                return
            self._set_busy(False)
            QMessageBox.critical(self, "下载失败", e)

        w = ApiWorker(_download)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _is_current_operation(self, project_id: int, generation: int) -> bool:
        return self._project_id == project_id and self._operation_gen == generation
