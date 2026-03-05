"""
StringsTab: 词条管理标签页，支持筛选、浏览及批量操作。
双击词条后弹出 StringDetailDialog 进行查看和编辑。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QDialog, QSplitter, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QListWidget, QListWidgetItem, QStyledItemDelegate, QApplication, QStyle,
    QPushButton, QLabel, QComboBox, QSpinBox, QTextEdit, QLineEdit,
    QMessageBox, QFormLayout, QCheckBox, QRadioButton, QButtonGroup,
)
from PyQt6.QtCore import Qt, QSize, QRect
from PyQt6.QtGui import QColor, QFont

from src.transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI
from src.transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from ..workers import ApiWorker

_STAGE_LABELS = {
    -2: "全部",
    0: "未翻译",
    1: "已翻译",
    2: "有疑问",
    3: "已检查",
    5: "已审核",
    9: "已锁定",
    -1: "已隐藏",
}
_STAGE_COLORS = {
    0: "#9E9E9E", 1: "#2196F3", 2: "#FF9800",
    3: "#00BCD4", 5: "#4CAF50", 9: "#B71C1C", -1: "#757575",
}
_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


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
            # 以系统文字色为背景、背景色为文字——与正常态"对调"
            # 浅色主题：深色背景+白字；深色主题：浅色背景+深字；均高对比
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
        self._page = page
        self._page_size = page_size
        self._workers: list[ApiWorker] = []
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
            cb.stateChanged.connect(lambda _: self._apply_nav_filter())
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

    def _clear_filter(self):
        for cb in self._stage_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        first_btn = self._modifier_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self._apply_nav_filter()

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
                file=self._file_id, stage=self._stage,
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
            QMessageBox.information(self, "成功", "保存成功")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.critical(self, "保存失败", e))
        w.start()
        self._workers.append(w)

    def was_modified(self) -> bool:
        return self._modified


# ═══════════════════════════════════════════════════════════════════════


class StringsTab(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._strings: list[dict] = []
        self._files: list[dict] = []
        self._project_id: int | None = None
        self._files_gen = 0
        self._strings_gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("文件:"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(140)
        filter_row.addWidget(self._file_combo)

        filter_row.addWidget(QLabel("状态:"))
        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            self._stage_combo.addItem(name, val)
        filter_row.addWidget(self._stage_combo)

        filter_row.addWidget(QLabel("页码:"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setFixedWidth(60)
        filter_row.addWidget(self._page_spin)

        filter_row.addWidget(QLabel("每页:"))
        self._page_size_combo = QComboBox()
        for sz in (50, 100, 200, 500):
            self._page_size_combo.addItem(str(sz), sz)
        filter_row.addWidget(self._page_size_combo)

        refresh_btn = QPushButton("查询")
        refresh_btn.clicked.connect(self.load_strings)
        filter_row.addWidget(refresh_btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # 工具栏
        toolbar = QHBoxLayout()
        self._create_btn = QPushButton("创建词条")
        self._create_btn.clicked.connect(self._create_string)
        self._create_btn.setEnabled(False)
        self._batch_stage_btn = QPushButton("批量设置状态")
        self._batch_stage_btn.clicked.connect(self._batch_set_stage)
        self._batch_del_btn = QPushButton("批量删除")
        self._batch_del_btn.setStyleSheet("color: red;")
        self._batch_del_btn.clicked.connect(self._batch_delete)
        hint = QLabel("双击词条可查看详情/编辑")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(self._create_btn)
        for btn in (self._batch_stage_btn, self._batch_del_btn):
            btn.setEnabled(False)
            toolbar.addWidget(btn)
        toolbar.addStretch()
        toolbar.addWidget(hint)
        layout.addLayout(toolbar)

        # 词条列表
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["键名", "原文", "译文", "状态", "所属文件", "操作者", "更新时间"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for i in (3, 4, 5, 6):
            self._table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._strings = []
        self._table.setRowCount(0)
        self._file_combo.clear()
        self._file_combo.addItem("全部文件", None)
        self._create_btn.setEnabled(bool(self._project_id) and self._ctx.is_admin())
        if self._project_id:
            self._load_files_for_filter()
            self.load_strings()

    def _load_files_for_filter(self):
        config = self._ctx.config
        pid = self._project_id
        self._files_gen += 1
        gen = self._files_gen

        def _fetch():
            api = ParatranzFilesAPI(token=config.token, config=config)
            data = api.list_files(pid)
            return data if isinstance(data, list) else []

        def _on_done(files):
            if self._files_gen != gen:
                return
            self._files = files
            self._file_combo.clear()
            self._file_combo.addItem("全部文件", None)
            for f in files:
                self._file_combo.addItem(f.get("name", str(f.get("id"))), f.get("id"))

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda _: None)
        w.start()
        self._workers.append(w)

    def load_strings(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        file_id = self._file_combo.currentData()
        stage_val = self._stage_combo.currentData()
        stage = stage_val if stage_val != -2 else None
        page = self._page_spin.value()
        page_size = self._page_size_combo.currentData()
        self._strings_gen += 1
        gen = self._strings_gen

        def _fetch():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return _extract_list(api.list_strings(pid, page=page, page_size=page_size,
                                                   file=file_id, stage=stage))

        def _on_done(strings):
            if self._strings_gen != gen:
                return
            self._on_strings_loaded(strings)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_strings_loaded(self, strings: list):
        self._strings = strings
        self._table.setRowCount(len(strings))
        for row, s in enumerate(strings):
            stage = s.get("stage", 0)
            stage_text = _STAGE_LABELS.get(stage, str(stage))
            file_info = s.get("file") or {}
            user_info = s.get("user") or {}
            cells = [
                s.get("key", ""),
                s.get("original", "")[:80],
                s.get("translation", "")[:80],
                stage_text,
                file_info.get("name", "—") if isinstance(file_info, dict) else "—",
                user_info.get("nickname", "—") if isinstance(user_info, dict) else "—",
                str(s.get("updatedAt", ""))[:10],
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, s)
                if col == 3:
                    item.setForeground(QColor(_STAGE_COLORS.get(stage, "#000000")))
                self._table.setItem(row, col, item)

    def _on_selection_changed(self):
        rows = {i.row() for i in self._table.selectedItems()}
        is_admin = self._ctx.is_admin()
        self._batch_stage_btn.setEnabled(len(rows) >= 1 and is_admin)
        self._batch_del_btn.setEnabled(len(rows) >= 1 and is_admin)

    def _on_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if not self._strings or row >= len(self._strings):
            return
        file_id = self._file_combo.currentData()
        stage_val = self._stage_combo.currentData()
        stage = stage_val if stage_val != -2 else None
        page = self._page_spin.value()
        page_size = self._page_size_combo.currentData()
        dlg = StringDetailDialog(
            self._strings, row, self._ctx, self._project_id,
            file_id=file_id, stage=stage, page=page, page_size=page_size,
            parent=self,
        )
        dlg.exec()
        if dlg.was_modified():
            self.load_strings()

    def _batch_set_stage(self):
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]
        QMessageBox.information(self, "提示", f"批量操作将对 {len(ids)} 条词条生效，功能待完善。")

    def _batch_delete(self):
        rows = list({i.row() for i in self._table.selectedItems()})
        ids = [self._table.item(r, 0).data(Qt.ItemDataRole.UserRole).get("id") for r in rows]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 条词条吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        config = self._ctx.config
        pid = self._project_id

        def _delete():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.batch_strings(pid, "delete", ids)

        w = ApiWorker(_delete)
        w.result.connect(lambda _: self.load_strings())
        w.error.connect(lambda e: QMessageBox.critical(self, "删除失败", e))
        w.start()
        self._workers.append(w)

    def _create_string(self):
        dlg = _CreateStringDialog(self._files, self)
        if not dlg.exec():
            return
        data = dlg.get_data()
        config = self._ctx.config
        pid = self._project_id

        def _create():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.create_string(pid, data)

        w = ApiWorker(_create)
        w.result.connect(lambda _: self.load_strings())
        w.error.connect(lambda e: QMessageBox.critical(self, "创建失败", e))
        w.start()
        self._workers.append(w)


class _CreateStringDialog(QDialog):
    def __init__(self, files: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建词条")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._key = QLineEdit()
        self._key.setPlaceholderText("必填，唯一键名")
        form.addRow("键名 *", self._key)

        self._original = QLineEdit()
        self._original.setPlaceholderText("必填，原文")
        form.addRow("原文 *", self._original)

        self._translation = QTextEdit()
        self._translation.setMaximumHeight(70)
        self._translation.setPlaceholderText("可选，译文")
        form.addRow("译文", self._translation)

        self._file_combo = QComboBox()
        self._file_combo.addItem("不指定文件", None)
        for f in files:
            self._file_combo.addItem(f.get("name", str(f.get("id"))), f.get("id"))
        form.addRow("所属文件", self._file_combo)

        self._stage_combo = QComboBox()
        for val, name in _STAGE_LABELS.items():
            if val != -2:
                self._stage_combo.addItem(name, val)
        form.addRow("状态", self._stage_combo)

        self._context = QLineEdit()
        self._context.setPlaceholderText("可选，上下文说明")
        form.addRow("上下文", self._context)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("创建")
        ok_btn.clicked.connect(self._validate)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _validate(self):
        if not self._key.text().strip():
            QMessageBox.warning(self, "提示", "键名不能为空")
            return
        if not self._original.text().strip():
            QMessageBox.warning(self, "提示", "原文不能为空")
            return
        self.accept()

    def get_data(self) -> dict:
        data = {
            "key": self._key.text().strip(),
            "original": self._original.text().strip(),
            "translation": self._translation.toPlainText().strip(),
            "stage": self._stage_combo.currentData(),
        }
        file_id = self._file_combo.currentData()
        if file_id is not None:
            data["file"] = file_id
        ctx = self._context.text().strip()
        if ctx:
            data["context"] = ctx
        return data
