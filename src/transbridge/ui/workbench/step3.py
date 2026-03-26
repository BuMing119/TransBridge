"""
步骤3：操作面板。
三个独立操作卡片：上传 ParaTranz、下载合并、写回 ESP。
支持批量操作：当加载多个插件时，显示批量操作区域。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QProgressBar, QCheckBox, QScrollArea, QFrame,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from ..workers import ApiWorker
from .project_prompt_overlay import ProjectPromptOverlay
from .cards.upload_card import UploadCard
from .cards.download_card import DownloadCard
from .cards.write_card import WriteCard


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

        # ── 批量操作区域 ───────────────────────────────────────
        self._batch_widget = QWidget()
        batch_layout = QVBoxLayout(self._batch_widget)
        batch_layout.setContentsMargins(0, 0, 0, 8)
        batch_layout.setSpacing(4)

        # 标题行
        batch_header = QHBoxLayout()
        self._batch_title = QLabel("批量操作")
        self._batch_title.setStyleSheet("font-weight: bold; color: #1a5276;")
        batch_header.addWidget(self._batch_title)

        self._btn_select_all = QPushButton("全选")
        self._btn_select_all.setFixedHeight(22)
        self._btn_select_all.setStyleSheet("padding: 0 8px;")
        self._btn_select_all.clicked.connect(self._select_all_slots)

        self._btn_select_none = QPushButton("全不选")
        self._btn_select_none.setFixedHeight(22)
        self._btn_select_none.setStyleSheet("padding: 0 8px;")
        self._btn_select_none.clicked.connect(self._select_none_slots)

        batch_header.addWidget(self._btn_select_all)
        batch_header.addWidget(self._btn_select_none)
        batch_header.addStretch()
        batch_layout.addLayout(batch_header)

        # 插件选择区域（横向滚动）
        self._slot_scroll = QScrollArea()
        self._slot_scroll.setWidgetResizable(True)
        self._slot_scroll.setFixedHeight(50)
        self._slot_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._slot_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._slot_scroll.setStyleSheet("QScrollArea { border: 1px solid #ddd; border-radius: 3px; background: #fafafa; }")

        self._slot_container = QWidget()
        self._slot_layout = QHBoxLayout(self._slot_container)
        self._slot_layout.setContentsMargins(8, 4, 8, 4)
        self._slot_layout.setSpacing(12)
        self._slot_layout.addStretch()
        self._slot_scroll.setWidget(self._slot_container)
        batch_layout.addWidget(self._slot_scroll)

        # 批量操作按钮行
        batch_btn_row = QHBoxLayout()
        self._btn_batch_upload = QPushButton("批量上传")
        self._btn_batch_upload.setFixedHeight(28)
        self._btn_batch_upload.clicked.connect(self._on_batch_upload)

        self._btn_batch_download = QPushButton("批量下载")
        self._btn_batch_download.setFixedHeight(28)
        self._btn_batch_download.clicked.connect(self._on_batch_download)

        self._btn_batch_write = QPushButton("批量写回")
        self._btn_batch_write.setFixedHeight(28)
        self._btn_batch_write.clicked.connect(self._on_batch_write)

        batch_btn_row.addWidget(self._btn_batch_upload)
        batch_btn_row.addWidget(self._btn_batch_download)
        batch_btn_row.addWidget(self._btn_batch_write)
        batch_btn_row.addStretch()
        batch_layout.addLayout(batch_btn_row)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        batch_layout.addWidget(sep)

        box_layout.addWidget(self._batch_widget)
        self._batch_widget.hide()  # 默认隐藏

        # 存储 slot checkbox 映射
        self._slot_checkboxes: dict[str, QCheckBox] = {}

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
        from PyQt6.QtWidgets import QMessageBox
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
            self._update_overlay_geometry()

        # 更新批量操作区域
        self._update_batch_visibility()

    # ── Batch operations ────────────────────────────────────────

    def _update_batch_visibility(self):
        """根据 slots 数量更新批量操作区域的可见性和内容。"""
        slots = self._ctx.slots
        slot_count = len(slots)

        # 清除旧的 checkbox
        for cb in self._slot_checkboxes.values():
            cb.deleteLater()
        self._slot_checkboxes.clear()

        # 多于 1 个槽位时显示批量操作区域
        if slot_count > 1:
            self._batch_widget.show()
            self._batch_title.setText(f"批量操作 ({slot_count} 个插件已加载)")

            # 创建新的 checkbox
            for key, slot in slots.items():
                label = slot.label or Path(key).stem
                cb = QCheckBox(label)
                cb.setChecked(True)
                cb.setStyleSheet("QCheckBox { spacing: 4px; }")
                self._slot_layout.insertWidget(self._slot_layout.count() - 1, cb)  # 在 stretch 之前插入
                self._slot_checkboxes[key] = cb

            # 更新批量按钮状态
            has_project = self._ctx.current_project is not None
            mine_ids = self._ctx.mine_project_ids
            is_member = (
                not bool(mine_ids)
                or (has_project and self._ctx.current_project.get("id") in mine_ids)
            )
            self._btn_batch_upload.setEnabled(has_project and is_member)
            self._btn_batch_download.setEnabled(has_project and is_member)
            self._btn_batch_write.setEnabled(True)
        else:
            self._batch_widget.hide()

    def _select_all_slots(self):
        """全选所有槽位。"""
        for cb in self._slot_checkboxes.values():
            cb.setChecked(True)

    def _select_none_slots(self):
        """取消选择所有槽位。"""
        for cb in self._slot_checkboxes.values():
            cb.setChecked(False)

    def _get_selected_slots(self) -> list:
        """获取用户选中的槽位列表。"""
        selected = []
        for key, cb in self._slot_checkboxes.items():
            if cb.isChecked() and key in self._ctx.slots:
                selected.append(self._ctx.slots[key])
        return selected

    def _on_batch_upload(self):
        """批量上传到 ParaTranz。"""
        selected_slots = self._get_selected_slots()
        if not selected_slots:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量上传。")
            return

        project = self._ctx.current_project
        if not project:
            QMessageBox.warning(self, "未选择项目", "请先在 ParaTranz 管理面板中选择目标项目。")
            return

        self._card_upload.do_batch_upload(selected_slots, project)

    def _on_batch_download(self):
        """批量从 ParaTranz 下载。"""
        selected_slots = self._get_selected_slots()
        if not selected_slots:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量下载。")
            return

        project = self._ctx.current_project
        if not project:
            QMessageBox.warning(self, "未选择项目", "请先在 ParaTranz 管理面板中选择目标项目。")
            return

        self._card_download.do_batch_download(selected_slots, project)

    def _on_batch_write(self):
        """批量写回插件。"""
        selected_slots = self._get_selected_slots()
        if not selected_slots:
            QMessageBox.warning(self, "未选择插件", "请至少选择一个插件进行批量写回。")
            return

        # 过滤出有 plugin 实例的槽位
        valid_slots = [s for s in selected_slots if s.plugin is not None]
        if not valid_slots:
            QMessageBox.warning(self, "无可写回插件", "所选插件均无 plugin 实例，无法写回。")
            return

        self._card_write.do_batch_write(valid_slots)

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
