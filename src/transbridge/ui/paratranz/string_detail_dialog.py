"""
StringDetailDialog：词条详情/编辑对话框。
左侧可折叠筛选 + 可分页词条导航列表，右侧编辑区。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QDialog, QSplitter, QFrame,
    QListWidget, QListWidgetItem, QStyledItemDelegate, QApplication, QStyle,
    QPushButton, QLabel, QComboBox, QTextEdit,
    QFormLayout, QCheckBox, QRadioButton, QButtonGroup, QMessageBox,
    QProgressDialog,
)
from PyQt6.QtCore import Qt, QSize, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QCloseEvent

from transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI
from ..workers import ApiWorker
from ._strings_common import _STAGE_LABELS, _KEY_ROLE, _extract_list
from .string_dialogs import _SyncTranslationDialog


class _NavItemDelegate(QStyledItemDelegate):
    """导航列表项：上行原文（正常大小），下行键名（缩小 + 淡化）。
    根据高亮背景亮度自动切换文字颜色，确保选中状态下可读。"""

    def paint(self, painter, option, index):
        opt = option.__class__(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        QApplication.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter
        )

        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        r = option.rect.adjusted(6, 4, -6, -4)
        orig = index.data(Qt.ItemDataRole.DisplayRole) or ""
        key = index.data(_KEY_ROLE) or ""

        split = r.height() * 6 // 10
        orig_rect = QRect(r.x(), r.y(), r.width(), split)
        key_rect = QRect(r.x(), r.y() + split, r.width(), r.height() - split)

        palette = option.palette
        if selected:
            painter.fillRect(option.rect, palette.text())
            text_color = palette.base().color()
            key_color  = QColor(palette.base().color())
            key_color.setAlpha(180)
        else:
            text_color = palette.text().color()
            key_color  = QColor("#888888")

        # 原文（正常）
        painter.setPen(text_color)
        painter.setFont(option.font)
        painter.drawText(
            orig_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(
                orig, Qt.TextElideMode.ElideRight, orig_rect.width()
            ),
        )

        # 键名（缩小 + 淡化）
        key_font = QFont(option.font)
        key_font.setPointSize(max(option.font.pointSize() - 1, 8))
        painter.setFont(key_font)
        painter.setPen(key_color)
        painter.drawText(
            key_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(
                key, Qt.TextElideMode.ElideRight, key_rect.width()
            ),
        )
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width() if option.rect.width() > 0 else 200, 52)


class StringDetailDialog(QDialog):
    """词条详情/编辑对话框：左侧可折叠筛选 + 可分页词条列表，右侧编辑。"""

    strings_updated = pyqtSignal()  # 保存或同步成功后发射，通知外部刷新

    def __init__(self, strings: list, current_index: int, ctx, project_id: int,
                 file_id=None, stage=None, page: int = 1, page_size: int = 50,
                 parent=None):
        super().__init__(parent)
        self._strings = list(strings)
        self._current_idx = current_index
        self._filtered_indices: list[int] = []
        self._ctx = ctx
        self._pid = project_id
        self._file_id = file_id
        self._stage = stage
        self._current_api_stage = stage  # 当前 API 请求使用的 stage（由 UI 筛选动态覆盖）
        self._page = page
        self._page_size = page_size
        self._workers: list[ApiWorker] = []
        self._close_pending = False
        self._close_progress: QProgressDialog | None = None
        self._modified = False
        self._nav_gen = 0
        self.setWindowTitle("词条详情")
        self.resize(980, 600)
        self._init_ui()
        self._apply_nav_filter()       # 初始化导航列表
        self._show(current_index)

    # ─────────────────────────────────────────────────────── UI 构建 ──

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_nav_panel())
        splitter.addWidget(self._build_edit_panel())
        splitter.setSizes([240, 720])
        layout.addWidget(splitter)

    def _build_nav_panel(self) -> QWidget:
        nav_widget = QWidget()
        nav_widget.setMinimumWidth(180)
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 4, 0)
        nav_layout.setSpacing(2)

        # ── 折叠筛选面板 ──
        self._filter_toggle = QPushButton("▶ 筛选条件")
        self._filter_toggle.setCheckable(True)
        self._filter_toggle.setFixedHeight(26)
        self._filter_toggle.toggled.connect(self._on_filter_toggle)
        nav_layout.addWidget(self._filter_toggle)

        self._filter_panel = self._build_filter_panel()
        self._filter_panel.setVisible(False)
        nav_layout.addWidget(self._filter_panel)

        # ── 导航列表 ──
        self._nav_list = QListWidget()
        self._nav_list.setItemDelegate(_NavItemDelegate(self._nav_list))
        self._nav_list.itemClicked.connect(self._on_nav_clicked)
        nav_layout.addWidget(self._nav_list, stretch=1)

        # ── 翻页控件 ──
        page_row = QHBoxLayout()
        self._prev_page_btn = QPushButton("< 上页")
        self._prev_page_btn.setFixedHeight(24)
        self._prev_page_btn.clicked.connect(self._load_prev_page)
        self._page_lbl = QLabel()
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._next_page_btn = QPushButton("下页 >")
        self._next_page_btn.setFixedHeight(24)
        self._next_page_btn.clicked.connect(self._load_next_page)
        page_row.addWidget(self._prev_page_btn)
        page_row.addWidget(self._page_lbl, stretch=1)
        page_row.addWidget(self._next_page_btn)
        nav_layout.addLayout(page_row)

        return nav_widget

    def _build_filter_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 翻译状态（多选，不互斥）
        layout.addWidget(QLabel("翻译状态："))
        self._stage_checks: dict[int, QCheckBox] = {}
        stages = [
            (0, "未翻译"), (1, "已翻译"), (2, "有疑问"),
            (3, "已检查"), (5, "已审核"), (9, "已锁定"), (-1, "已隐藏"),
        ]
        row1 = QHBoxLayout()
        row2 = QHBoxLayout()
        for i, (val, name) in enumerate(stages):
            cb = QCheckBox(name)
            cb.setToolTip(name)
            cb.stateChanged.connect(lambda _: self._on_filter_changed())
            self._stage_checks[val] = cb
            (row1 if i < 4 else row2).addWidget(cb)
        row1.addStretch()
        row2.addStretch()
        layout.addLayout(row1)
        layout.addLayout(row2)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # 修改者（单选，互斥）
        layout.addWidget(QLabel("修改者："))
        self._modifier_group = QButtonGroup(panel)
        self._modifier_group.setExclusive(True)
        mod_row = QHBoxLayout()
        for id_, label in [(0, "全部"), (1, "最后由我修改"), (2, "最后由他人修改")]:
            rb = QRadioButton(label)
            rb.setToolTip(label)
            self._modifier_group.addButton(rb, id_)
            if id_ == 0:
                rb.setChecked(True)
            mod_row.addWidget(rb)
        mod_row.addStretch()
        self._modifier_group.buttonClicked.connect(lambda _: self._apply_nav_filter())
        layout.addLayout(mod_row)

        # 清除筛选
        clear_btn = QPushButton("清除筛选")
        clear_btn.setFixedHeight(22)
        clear_btn.clicked.connect(self._clear_filter)
        layout.addWidget(clear_btn)

        return panel

    def _build_edit_panel(self) -> QWidget:
        right = QWidget()
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)

        form = QFormLayout()
        self._det_key = QLabel()
        self._det_key.setWordWrap(True)

        self._det_orig = QTextEdit()
        self._det_orig.setReadOnly(True)
        self._det_orig.setMinimumHeight(60)
        self._det_orig.setMaximumHeight(150)

        self._det_ctx = QLabel()
        self._det_ctx.setWordWrap(True)
        form.addRow("键名:", self._det_key)
        form.addRow("原文:", self._det_orig)
        form.addRow("上下文:", self._det_ctx)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText("译文（可编辑）")
        self._trans_edit.setMinimumHeight(80)
        form.addRow("译文:", self._trans_edit)

        self._stage_edit = QComboBox()
        for val, name in _STAGE_LABELS.items():
            if val != -2:
                self._stage_edit.addItem(name, val)
        form.addRow("状态:", self._stage_edit)

        right_layout.addLayout(form)
        right_layout.addStretch()

        btn_row = QHBoxLayout()
        self._prev_btn = QPushButton("◀ 上一条")
        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn = QPushButton("下一条 ▶")
        self._next_btn.clicked.connect(self._go_next)
        self._save_btn = QPushButton("保存")
        self._save_btn.setFixedWidth(80)
        self._save_btn.clicked.connect(self._save)
        btn_row.addWidget(self._prev_btn)
        btn_row.addWidget(self._next_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        right_layout.addLayout(btn_row)

        return right

    # ─────────────────────────────────────────────────── 筛选逻辑 ──

    def _on_filter_toggle(self, checked: bool):
        self._filter_toggle.setText("▼ 筛选条件" if checked else "▶ 筛选条件")
        self._filter_panel.setVisible(checked)

    def _on_filter_changed(self):
        """stage 复选框变化时，更新 API stage 参数并从服务端第 1 页重新加载。"""
        selected = {v for v, cb in self._stage_checks.items() if cb.isChecked()}
        if len(selected) == 1:
            self._current_api_stage = next(iter(selected))   # 单选 → 服务端过滤
        elif len(selected) == 0:
            self._current_api_stage = self._stage            # 无选 → 恢复主列表原始过滤
        else:
            self._current_api_stage = None                   # 多选 → 加载全部，前端再筛
        self._load_page(1)

    def _clear_filter(self):
        for cb in self._stage_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        first_btn = self._modifier_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self._on_filter_changed()

    def _get_filtered_indices(self) -> list[int]:
        selected_stages = {v for v, cb in self._stage_checks.items() if cb.isChecked()}
        modifier_id = self._modifier_group.checkedId()
        my_uid = (self._ctx.current_user or {}).get("id")

        result = []
        for i, s in enumerate(self._strings):
            # 翻译状态筛选（无勾选则全显示）
            if selected_stages and s.get("stage", 0) not in selected_stages:
                continue
            # 修改者筛选
            if modifier_id in (1, 2) and my_uid is not None:
                user = s.get("user") or {}
                uid = user.get("uid") or user.get("id")
                if modifier_id == 1 and uid != my_uid:
                    continue
                if modifier_id == 2 and uid == my_uid:
                    continue
            result.append(i)
        return result

    def _apply_nav_filter(self):
        """重新计算筛选结果并刷新导航列表。"""
        self._filtered_indices = self._get_filtered_indices()

        self._nav_list.clear()
        for fidx in self._filtered_indices:
            s = self._strings[fidx]
            item = QListWidgetItem(s.get("original") or "（无原文）")
            item.setData(_KEY_ROLE, s.get("key", ""))
            orig_preview = (s.get("original") or "")[:200]
            item.setToolTip(f"键名：{s.get('key', '')}\n原文：{orig_preview}")
            self._nav_list.addItem(item)

        # 页码标签
        count, total = len(self._filtered_indices), len(self._strings)
        suffix = f"  {count}/{total} 条" if count != total else f"  {total} 条"
        self._page_lbl.setText(f"第 {self._page} 页{suffix}")
        self._prev_page_btn.setEnabled(self._page > 1)
        self._next_page_btn.setEnabled(total >= self._page_size)

        # 保持当前词条选中，或跳到第一条
        if self._current_idx in self._filtered_indices:
            self._show(self._current_idx)
        elif self._filtered_indices:
            self._show(self._filtered_indices[0])
        else:
            self._det_key.setText("—")
            self._det_orig.setPlainText("（无匹配词条）")
            self._det_ctx.setText("—")
            self._trans_edit.setPlainText("")
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)

    # ──────────────────────────────────────────────────── 导航 ──

    def _on_nav_clicked(self, item: QListWidgetItem):
        nav_row = self._nav_list.row(item)
        if 0 <= nav_row < len(self._filtered_indices):
            self._show(self._filtered_indices[nav_row])

    def _show(self, str_idx: int):
        """显示 self._strings[str_idx] 的内容，并同步导航列表选中状态。"""
        if str_idx < 0 or str_idx >= len(self._strings):
            return
        self._current_idx = str_idx

        # 在导航列表中定位
        for nav_row, fidx in enumerate(self._filtered_indices):
            if fidx == str_idx:
                self._nav_list.setCurrentRow(nav_row)
                break

        s = self._strings[str_idx]
        self._det_key.setText(s.get("key", "—"))
        self._det_orig.setPlainText(s.get("original", "—"))
        self._det_ctx.setText(s.get("context") or "—")
        self._trans_edit.setPlainText(s.get("translation") or "")
        stage = s.get("stage", 0)
        for i in range(self._stage_edit.count()):
            if self._stage_edit.itemData(i) == stage:
                self._stage_edit.setCurrentIndex(i)
                break

        # 上一条/下一条基于当前筛选后的列表
        try:
            pos = self._filtered_indices.index(str_idx)
            self._prev_btn.setEnabled(pos > 0)
            self._next_btn.setEnabled(pos < len(self._filtered_indices) - 1)
        except ValueError:
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)

    def _go_prev(self):
        try:
            pos = self._filtered_indices.index(self._current_idx)
            if pos > 0:
                self._show(self._filtered_indices[pos - 1])
        except ValueError:
            pass

    def _go_next(self):
        try:
            pos = self._filtered_indices.index(self._current_idx)
            if pos < len(self._filtered_indices) - 1:
                self._show(self._filtered_indices[pos + 1])
        except ValueError:
            pass

    # ──────────────────────────────────────────────── 翻页 ──

    def _load_page(self, page: int):
        config = self._ctx.config
        self._nav_gen += 1
        gen = self._nav_gen

        self._prev_page_btn.setEnabled(False)
        self._next_page_btn.setEnabled(False)
        self._page_lbl.setText("加载中…")

        def _fetch():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return _extract_list(api.list_strings(
                self._pid, page=page, page_size=self._page_size,
                file=self._file_id, stage=self._current_api_stage,
            ))

        def _on_done(strings):
            if self._nav_gen != gen:
                return
            if not strings:
                self._page_lbl.setText(f"第 {self._page} 页（已是末页）")
                self._prev_page_btn.setEnabled(self._page > 1)
                self._next_page_btn.setEnabled(False)
                return
            self._page = page
            self._strings = strings
            self._apply_nav_filter()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: self._page_lbl.setText("加载失败"))
        w.start()
        self._workers.append(w)

    def _load_prev_page(self):
        if self._page > 1:
            self._load_page(self._page - 1)

    def _load_next_page(self):
        self._load_page(self._page + 1)

    # ──────────────────────────────────────────────── 保存 ──

    def _save(self):
        s = self._strings[self._current_idx]
        new_translation = self._trans_edit.toPlainText()
        new_stage = self._stage_edit.currentData()
        config = self._ctx.config

        self._save_btn.setEnabled(False)
        self._save_btn.setText("保存中…")

        def _do():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.update_string(
                self._pid, s.get("id"),
                {"translation": new_translation, "stage": new_stage},
            )

        def _on_done(_):
            s["translation"] = new_translation
            s["stage"] = new_stage
            self._modified = True
            self._apply_nav_filter()
            if new_stage > 0:
                self._find_sync_candidates(s, new_translation, new_stage)
            else:
                self._save_btn.setEnabled(True)
                self._save_btn.setText("保存")
                self.strings_updated.emit()
                QMessageBox.information(self, "成功", "保存成功")

        def _on_error(e):
            self._save_btn.setEnabled(True)
            self._save_btn.setText("保存")
            QMessageBox.critical(self, "保存失败", e)

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _find_sync_candidates(self, saved_string: dict, new_translation: str, new_stage: int):
        """保存成功后，在本地已加载词条中搜索相同原文的低状态条目，有匹配则提示同步。"""
        current_id = saved_string.get("id")
        current_original = saved_string.get("original", "")

        matches = [
            item for item in self._strings
            if (item.get("original") == current_original
                and item.get("id") != current_id
                and 0 <= item.get("stage", 0) < new_stage)
        ]

        self._save_btn.setEnabled(True)
        self._save_btn.setText("保存")

        if not matches:
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", "保存成功")
            return

        dlg = _SyncTranslationDialog(
            matches, current_original, new_translation, new_stage, self
        )
        if dlg.exec():
            selected_ids = dlg.get_selected_ids()
            if selected_ids:
                self._do_batch_sync(selected_ids, new_translation, new_stage)
            else:
                self.strings_updated.emit()
                QMessageBox.information(self, "成功", "保存成功")
        else:
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", "保存成功")

    def _do_batch_sync(self, ids: list, translation: str, stage: int):
        config = self._ctx.config
        pid = self._pid

        self._save_btn.setEnabled(False)
        self._save_btn.setText("同步中…")

        def _sync():
            api = ParatranzStringsAPI(token=config.token, config=config)
            for string_id in ids:
                api.update_string(pid, string_id, {"translation": translation, "stage": stage})
            return len(ids)

        def _on_done(_):
            id_set = set(ids)
            for item in self._strings:
                if item.get("id") in id_set:
                    item["translation"] = translation
                    item["stage"] = stage
            self._modified = True
            self._apply_nav_filter()
            self._save_btn.setEnabled(True)
            self._save_btn.setText("保存")
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", f"保存成功，已同步 {len(ids)} 条词条")

        def _on_error(e):
            self._save_btn.setEnabled(True)
            self._save_btn.setText("保存")
            self._apply_nav_filter()
            QMessageBox.warning(
                self, "同步失败",
                f"当前词条已保存成功，但批量同步失败：\n{e}"
            )

        w = ApiWorker(_sync)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def closeEvent(self, event: QCloseEvent):
        """后台任务完成后再关闭，同时保持 Qt 事件循环可响应。"""
        running = [worker for worker in self._workers if worker.isRunning()]
        if not running:
            event.accept()
            return

        event.ignore()
        if self._close_pending:
            return
        self._close_pending = True
        self.setEnabled(False)
        self._close_progress = QProgressDialog(
            "正在等待后台同步完成…",
            "",
            0,
            0,
            self,
        )
        self._close_progress.setCancelButton(None)
        self._close_progress.setWindowTitle("正在关闭")
        self._close_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._close_progress.show()
        for worker in running:
            worker.finished.connect(self._finish_close_if_idle)

    def _finish_close_if_idle(self) -> None:
        if any(worker.isRunning() for worker in self._workers):
            return
        if self._close_progress is not None:
            self._close_progress.close()
            self._close_progress = None
        self._close_pending = False
        self.close()

    def was_modified(self) -> bool:
        return self._modified
