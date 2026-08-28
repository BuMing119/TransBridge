"""
步骤3：操作面板。
三个独立操作卡片：上传 ParaTranz、下载合并、写回 ESP。
批量操作已集成到各卡片中。
"""

from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..workers import ApiWorker
from .cards.download_card import DownloadCard
from .cards.upload_card import UploadCard
from .cards.write_card import WriteCard
from .project_prompt_overlay import ProjectPromptOverlay


class Step3OpsWidget(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._init_ui()

        # overlay 在 _init_ui 之后创建，确保层叠在最上方
        self._overlay = ProjectPromptOverlay(self)
        self._overlay.go_to_pt.connect(lambda: ctx.navigate_to.emit(1))

        ctx.collection_changed.connect(self._on_collection_changed)
        ctx.paratranz_binding_changed.connect(self._on_binding_changed)
        self._update_button_states()
        self._on_binding_changed(getattr(ctx, "paratranz_binding", None))

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
        self._warning_label.setStyleSheet("color: #7d4e00; font-size: 12px; border: none; background: transparent;")
        self._warning_label.setWordWrap(True)
        warning_row.addWidget(self._warning_label, stretch=1)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { color: #7d4e00; border: none; background: transparent; font-size: 14px; }"
            "QPushButton:hover { color: #000; }"
        )
        close_btn.clicked.connect(self._warning_widget.hide)
        warning_row.addWidget(close_btn)
        self._warning_widget.hide()
        box_layout.addWidget(self._warning_widget)

        row = QHBoxLayout()
        box_layout.addLayout(row)

        self._card_upload = UploadCard(self._ctx, self._run_worker)
        self._card_download = DownloadCard(self._ctx, self._run_worker)
        self._card_write = WriteCard(self._ctx, self._run_worker)

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
        self._update_overlay_geometry()

    def _update_overlay_geometry(self):
        from PyQt6.QtCore import QPoint

        card_pos = self._card_upload.mapTo(self, QPoint(0, 0))
        write_pos = self._card_write.mapTo(self, QPoint(0, 0))
        overlay_y = card_pos.y() if card_pos.y() > 0 else 0
        overlay_width = write_pos.x() if write_pos.x() > 0 else self.width() * 2 // 3
        self._overlay.setGeometry(0, overlay_y, overlay_width, self.height() - overlay_y)

    def _on_collection_changed(self, _):
        self._update_button_states()

    def _on_binding_changed(self, _binding):
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        self._warning_widget.hide()
        self._update_button_states()
        self._update_project_indicator(bound_paratranz_project(self._ctx))

    def _update_project_indicator(self, project):
        if project:
            name = project.get("name", "未知项目")
            self._project_indicator.setText(f"操作目标：{name}")
            self._project_indicator.setStyleSheet("color: #1a5276; font-size: 12px; font-weight: bold;")
        else:
            self._project_indicator.setText("操作目标：未选择项目")
            self._project_indicator.setStyleSheet("color: #888; font-size: 12px;")

    def _show_warning(self, msg: str):
        self._warning_label.setText(f"⚠  {msg}")
        self._warning_widget.show()

    def _update_button_states(self):
        has_collection = self._ctx.collection is not None
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        project = bound_paratranz_project(self._ctx)
        has_project = project is not None

        # 目标未绑定时仍允许进入计划；计划内会要求填写本次临时项目 ID。
        self._card_upload.btn.setEnabled(has_collection)
        self._card_download.btn.setEnabled(has_collection)
        self._card_write.btn.setEnabled(has_collection)

        # 更新批量按钮可见性
        self._card_upload.update_batch_visibility()
        self._card_download.update_batch_visibility()
        self._card_write.update_batch_visibility()

        # 批量按钮也需要根据项目状态启用/禁用
        slots = self._ctx.slots
        if len(slots) > 1:
            self._card_upload.batch_btn.setEnabled(has_project)
            self._card_download.batch_btn.setEnabled(has_project)
            self._card_write.batch_btn.setEnabled(True)

        self._overlay.hide()

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

    # ── Worker helper ─────────────────────────────────────────

    def _run_worker(
        self, fn=None, *, fn_factory=None, on_result, on_error, progress_total: int = 0, progress_msg: str = ""
    ):
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
