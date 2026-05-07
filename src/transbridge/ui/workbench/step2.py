"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及全部词条预览表格。
支持多选标签筛选、文本搜索、行内编辑、Ctrl/Shift行选。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QPushButton, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.converter.translation_entry import TranslationEntry

_STAGE_COLORS = {
    0: "#9E9E9E",
    1: "#2196F3",
    2: "#FF9800",
    3: "#00BCD4",
    5: "#4CAF50",
    9: "#B71C1C",
    -1: "#616161",
}

# context → 显示类别名称的映射（复用 export 中的分类逻辑）
_CONTEXT_TO_CATEGORY: dict[str, str] = {}
_CAT_MAP = {
    "人名": {"NPC_:FULL", "NPC_:SHRT", "TACT:FULL"},
    "地名": {"CELL:FULL", "DOOR:FULL", "LCTN:FULL", "REFR:FULL", "WRLD:FULL"},
    "书名": {"BOOK:FULL"},
    "书籍内容": {"BOOK:DESC"},
    "互动": {"FLOR:RNAM", "FURN:FULL", "HAZD:FULL"},
    "任务日志": {"QUST:FULL", "QUST:NNAM"},
    "法术技能": {
        "ENCH:FULL", "EXPL:FULL", "MESG:DESC", "MESG:FULL", "MESG:ITXT",
        "MGEF:DNAM", "MGEF:FULL", "PERK:FULL", "SHOU:FULL",
        "SPEL:DESC", "SPEL:FULL",
    },
    "物品": {
        "ACTI:FULL", "ACTI:RNAM", "ALCH:FULL", "AMMO:FULL",
        "ARMO:DESC", "ARMO:FULL", "CONT:FULL", "INGR:FULL",
        "KEYM:FULL", "MISC:FULL", "SLGM:FULL", "TREE:FULL",
        "WEAP:DESC", "WEAP:FULL",
    },
}
for _cat, _ctxs in _CAT_MAP.items():
    for _ctx in _ctxs:
        _CONTEXT_TO_CATEGORY[_ctx] = _cat


_ALL_CATEGORIES = ["人名", "地名", "书名", "书籍内容", "物品", "法术技能", "对话", "互动", "任务日志", "其他"]


def _entry_category(entry: TranslationEntry) -> str:
    ctx = entry.context or ""
    base = ctx.split("|")[0] if "|" in ctx else ctx
    rec = base.split(":")[0]
    if rec in ("INFO", "DIAL"):
        return "对话"
    return _CONTEXT_TO_CATEGORY.get(base, "其他")


class _StatCard(QWidget):
    """简单的统计数字卡片（标题 + 大数字）。"""

    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        lbl_title = QLabel(title)
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_value = QLabel("—")
        self._lbl_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_value.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {color};"
        )
        layout.addWidget(lbl_title)
        layout.addWidget(self._lbl_value)
        self.setStyleSheet(
            "QWidget { border: 1px solid #ddd; border-radius: 4px; background: #fafafa; }"
        )

    def set_value(self, v):
        self._lbl_value.setText(str(v))


# ────────────────────────────── 步骤2 主 Widget ──────────────────────────────

# 表格列常量（复选框作为选中标记列）
_COL_CHECK = 0
_COL_KEY   = 1
_COL_ORIG  = 2
_COL_TRANS = 3
_COL_CTX   = 4
_NUM_COLS  = 5



class Step2PreviewWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._entries: list[TranslationEntry] = []  # 全部词条
        self._category_filters: set[str] = set()  # 多选分类标签
        self._stage_filters: set[int] = set()  # 多选翻译状态标签（0=未翻译,1=有疑问,2=已翻译）
        self._selected_entry_ids: set[str] = set()  # 选中的词条ID集合
        self._last_clicked_row: int | None = None  # Shift 范围选择锚点
        self._tag_buttons: dict[str | int | None, QPushButton] = {}  # 标签按钮
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._populate_table)
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)

    # ── 初始化 UI ────────────────────────────────────────────────────────────

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(8)
        outer.addWidget(self._progress)

        # 四格统计卡
        cards_row = QHBoxLayout()
        self._card_total = _StatCard("总词条", "#212121")
        self._card_done = _StatCard("已有译文", "#4CAF50")
        self._card_migrate = _StatCard("迁移", "#2196F3")
        self._card_untranslated = _StatCard("未翻译", "#F44336")
        for c in (self._card_total, self._card_done, self._card_migrate, self._card_untranslated):
            cards_row.addWidget(c)
        outer.addLayout(cards_row)

        # 分类筛选标签行
        self._tags_widget = QWidget()
        tags_layout = QHBoxLayout(self._tags_widget)
        tags_layout.setContentsMargins(0, 0, 0, 0)
        tags_layout.setSpacing(4)
        tags_layout.addWidget(QLabel("分类："))
        self._tags_container = QHBoxLayout()
        self._tags_container.setSpacing(3)
        tags_layout.addLayout(self._tags_container)
        tags_layout.addStretch()
        self._tags_widget.hide()
        outer.addWidget(self._tags_widget)

        # 翻译状态筛选标签行
        self._stage_tags_widget = QWidget()
        stage_tags_layout = QHBoxLayout(self._stage_tags_widget)
        stage_tags_layout.setContentsMargins(0, 0, 0, 0)
        stage_tags_layout.setSpacing(4)
        stage_tags_layout.addWidget(QLabel("状态："))
        self._stage_tags_container = QHBoxLayout()
        self._stage_tags_container.setSpacing(3)
        stage_tags_layout.addLayout(self._stage_tags_container)
        stage_tags_layout.addStretch()
        self._stage_tags_widget.hide()
        outer.addWidget(self._stage_tags_widget)

        # 文本搜索栏
        self._search_widget = QWidget()
        search_layout = QHBoxLayout(self._search_widget)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)
        search_layout.addWidget(QLabel("Key:"))
        self._search_key = QLineEdit()
        self._search_key.setPlaceholderText("按 Key 筛选…")
        self._search_key.setClearButtonEnabled(True)
        self._search_key.textChanged.connect(self._search_timer.start)
        search_layout.addWidget(self._search_key)
        search_layout.addWidget(QLabel("原文:"))
        self._search_orig = QLineEdit()
        self._search_orig.setPlaceholderText("按原文筛选…")
        self._search_orig.setClearButtonEnabled(True)
        self._search_orig.textChanged.connect(self._search_timer.start)
        search_layout.addWidget(self._search_orig)
        search_layout.addWidget(QLabel("译文:"))
        self._search_trans = QLineEdit()
        self._search_trans.setPlaceholderText("按译文筛选…")
        self._search_trans.setClearButtonEnabled(True)
        self._search_trans.textChanged.connect(self._search_timer.start)
        search_layout.addWidget(self._search_trans)
        clear_search_btn = QPushButton("清除")
        clear_search_btn.setFixedWidth(52)
        clear_search_btn.clicked.connect(self._clear_search)
        search_layout.addWidget(clear_search_btn)
        self._search_widget.hide()
        outer.addWidget(self._search_widget)

        # 词条表格（复选框标记选中 + 点击行任意位置切换）
        self._table = QTableWidget(0, _NUM_COLS)
        self._table.setHorizontalHeaderLabels(["", "Key", "原文", "译文", "类型"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_CHECK, 28)
        hh.setSectionResizeMode(_COL_KEY,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ORIG,  QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_TRANS, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_CTX,   QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.cellClicked.connect(self._on_cell_clicked)

        outer.addWidget(self._table, stretch=1)

        # 底部计数标签
        self._count_lbl = QLabel("已选 0 条 / 共 0 条")
        self._count_lbl.setStyleSheet("color: #888; font-size: 11px;")
        outer.addWidget(self._count_lbl)

        # 操作进度区域（解析/上传/下载/写回共用）
        self._op_progress = QProgressBar()
        self._op_progress.setFixedHeight(14)
        self._op_progress.hide()
        outer.addWidget(self._op_progress)
        self._op_progress_lbl = QLabel("")
        self._op_progress_lbl.setStyleSheet("color: #555; font-size: 12px;")
        self._op_progress_lbl.hide()
        outer.addWidget(self._op_progress_lbl)

    # ── 操作进度接口 ──────────────────────────────────────────────────────────

    def show_progress(self, total: int, msg: str = ""):
        if total > 0:
            self._op_progress.setRange(0, total)
            self._op_progress.setValue(0)
        else:
            self._op_progress.setRange(0, 0)
        self._op_progress_lbl.setText(msg)
        self._op_progress.show()
        self._op_progress_lbl.show()

    def update_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._op_progress.setRange(0, total)
            self._op_progress.setValue(current)
        self._op_progress_lbl.setText(msg)

    def hide_progress(self):
        self._op_progress.hide()
        self._op_progress_lbl.hide()
        self._op_progress.setValue(0)
        self._op_progress_lbl.setText("")

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_selected_entries(self) -> list[TranslationEntry]:
        """返回当前选中的词条列表（持久化在 _selected_entry_ids 中）。"""
        result = []
        id_to_entry = {e.id: e for e in self._entries if e.id}
        for entry_id in self._selected_entry_ids:
            if entry_id in id_to_entry:
                result.append(id_to_entry[entry_id])
        return result

    def get_filtered_count(self) -> int:
        """返回当前筛选后显示的条数。"""
        return self._table.rowCount()  # 当前表格实际显示的行数（过滤后）

    # ── 进度 / 刷新 ───────────────────────────────────────────────────────────

    def set_parsing(self, parsing: bool):
        if parsing:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(100)

    def refresh(self, collection: TranslationEntryCollection | None):
        self._progress.setRange(0, 100)
        if collection is None:
            self._card_total.set_value("—")
            self._card_done.set_value("—")
            self._card_migrate.set_value("—")
            self._card_untranslated.set_value("—")
            self._entries = []
            self._category_filters.clear()
            self._stage_filters.clear()
            self._selected_entry_ids.clear()
            self._last_clicked_row = None
            self._tags_widget.hide()
            self._stage_tags_widget.hide()
            self._search_widget.hide()
            self._table.setRowCount(0)
            self._update_count_label()
            return

        total = len(collection)
        done = sum(1 for e in collection if e.stage >= 1 and e.translation)
        untranslated = sum(1 for e in collection if e.stage == 0 and not e.translation)

        self._progress.setValue(100)
        self._card_total.set_value(total)
        self._card_done.set_value(done)
        self._card_migrate.set_value(getattr(self._ctx, "migrate_count", 0))
        self._card_untranslated.set_value(untranslated)

        self._entries = list(collection)
        self._category_filters.clear()
        self._stage_filters.clear()

        existing_ids = {e.id for e in collection if e.id}
        self._selected_entry_ids = {eid for eid in self._selected_entry_ids if eid in existing_ids}
        self._last_clicked_row = None

        self._build_category_tags()
        self._build_stage_tags()
        self._search_widget.show()
        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    # ── 标签样式 ────────────────────────────────────────────────────────────

    _TAG_NORMAL = (
        "QPushButton { background: #f0f0f0; border: 1px solid #ccc; border-radius: 8px; "
        "padding: 2px 10px; font-size: 12px; color: #333; }"
        "QPushButton:hover { background: #e0e0e0; }"
    )
    _TAG_ACTIVE = (
        "QPushButton { background: #2196F3; border: 1px solid #1976D2; border-radius: 8px; "
        "padding: 2px 10px; font-size: 12px; color: white; font-weight: bold; }"
    )

    # ── 分类筛选标签 ────────────────────────────────────────────────────────

    def _build_category_tags(self):
        from collections import Counter
        while self._tags_container.count():
            item = self._tags_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._entries:
            self._tags_widget.hide()
            return

        counter = Counter()
        for e in self._entries:
            counter[_entry_category(e)] += 1
        total = len(self._entries)

        # 「全部」标签 — 点击清除所有分类筛选
        all_btn = QPushButton(f"全部 {total}")
        all_btn.setStyleSheet(self._TAG_ACTIVE if not self._category_filters else self._TAG_NORMAL)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._on_category_tag_clicked(None))
        self._tags_container.addWidget(all_btn)

        for cat in _ALL_CATEGORIES:
            global_count = counter.get(cat, 0)
            if global_count == 0:
                continue
            label = f"{cat} {global_count}"
            btn = QPushButton(label)
            btn.setStyleSheet(self._TAG_ACTIVE if cat in self._category_filters else self._TAG_NORMAL)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, c=cat: self._on_category_tag_clicked(c))
            self._tags_container.addWidget(btn)
            self._tag_buttons[cat] = btn

        self._tags_widget.show()

    def _on_category_tag_clicked(self, category: str | None):
        if category is None:
            self._category_filters.clear()
        elif category in self._category_filters:
            self._category_filters.discard(category)
        else:
            self._category_filters.add(category)
        self._build_category_tags()
        self._populate_table()

    # ── 翻译状态标签 ────────────────────────────────────────────────────────

    _STAGE_LABELS = {0: "未翻译", 1: "有疑问", 2: "已翻译"}

    def _build_stage_tags(self):
        from collections import Counter
        while self._stage_tags_container.count():
            item = self._stage_tags_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._entries:
            self._stage_tags_widget.hide()
            return

        counter = Counter()
        for e in self._entries:
            if e.stage == 0 and not e.translation:
                counter[0] += 1  # 未翻译
            elif e.stage == 1:
                counter[1] += 1  # 机翻
            elif e.stage >= 2 or e.translation:
                counter[2] += 1  # 已翻译

        total = len(self._entries)

        # 「全部」标签
        all_btn = QPushButton(f"全部 {total}")
        all_btn.setStyleSheet(self._TAG_ACTIVE if not self._stage_filters else self._TAG_NORMAL)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._on_stage_tag_clicked(None))
        self._stage_tags_container.addWidget(all_btn)

        for stage_val in [0, 1, 2]:
            count = counter.get(stage_val, 0)
            if count == 0:
                continue
            label = f"{self._STAGE_LABELS[stage_val]} {count}"
            btn = QPushButton(label)
            btn.setStyleSheet(self._TAG_ACTIVE if stage_val in self._stage_filters else self._TAG_NORMAL)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, s=stage_val: self._on_stage_tag_clicked(s))
            self._stage_tags_container.addWidget(btn)
            self._tag_buttons[stage_val] = btn

        self._stage_tags_widget.show()

    def _on_stage_tag_clicked(self, stage: int | None):
        if stage is None:
            self._stage_filters.clear()
        elif stage in self._stage_filters:
            self._stage_filters.discard(stage)
        else:
            self._stage_filters.add(stage)
        self._build_stage_tags()
        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    def _populate_table(self):
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_CTX, QHeaderView.ResizeMode.Interactive)
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)

        entries_to_show = self._apply_all_filters()
        self._table.setRowCount(len(entries_to_show))

        for row, entry in enumerate(entries_to_show):
            # Col 0: 复选框（选中标记）
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            if entry.id and entry.id in self._selected_entry_ids:
                check_item.setCheckState(Qt.CheckState.Checked)
            else:
                check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, entry)
            self._table.setItem(row, _COL_CHECK, check_item)

            # Col 1: Key
            key_item = QTableWidgetItem(entry.key or "")
            key_item.setData(Qt.ItemDataRole.UserRole, entry)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Col 2: 原文
            orig_item = QTableWidgetItem(entry.original[:80] if entry.original else "")
            orig_item.setData(Qt.ItemDataRole.UserRole, entry)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Col 3: 译文（可编辑）
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text[:80] if trans_text else "（无译文）")
            trans_item.setData(Qt.ItemDataRole.UserRole, entry)
            if trans_text:
                trans_item.setForeground(QColor("#4CAF50"))
            else:
                trans_item.setForeground(QColor("#9E9E9E"))

            # Col 4: 类型
            ctx_item = QTableWidgetItem(_entry_category(entry))
            ctx_item.setData(Qt.ItemDataRole.UserRole, entry)
            ctx_item.setFlags(ctx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self._table.setItem(row, _COL_KEY,   key_item)
            self._table.setItem(row, _COL_ORIG,  orig_item)
            self._table.setItem(row, _COL_TRANS, trans_item)
            self._table.setItem(row, _COL_CTX,   ctx_item)

        self._table.setUpdatesEnabled(True)
        self._table.blockSignals(False)
        self._table.resizeColumnToContents(_COL_KEY)
        self._table.resizeColumnToContents(_COL_CTX)
        hh.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_CTX, QHeaderView.ResizeMode.Interactive)
        self._update_count_label()

    def _apply_all_filters(self) -> list[TranslationEntry]:
        """叠加所有筛选条件（分类 + 状态 + 搜索），返回过滤后的词条列表。"""
        result = list(self._entries)

        # 分类筛选（多选 AND）
        if self._category_filters:
            result = [e for e in result if _entry_category(e) in self._category_filters]

        # 翻译状态筛选（多选 AND）
        if self._stage_filters:
            filtered = []
            for e in result:
                if 0 in self._stage_filters and e.stage == 0 and not e.translation:
                    filtered.append(e)
                elif 1 in self._stage_filters and e.stage == 1:
                    filtered.append(e)
                elif 2 in self._stage_filters and (e.stage >= 2 or (e.stage >= 1 and e.translation)):
                    filtered.append(e)
            result = filtered

        # 文本搜索（AND 叠加）
        key_kw = self._search_key.text().lower()
        orig_kw = self._search_orig.text().lower()
        trans_kw = self._search_trans.text().lower()
        if key_kw:
            result = [e for e in result if key_kw in (e.key or "").lower()]
        if orig_kw:
            result = [e for e in result if orig_kw in (e.original or "").lower()]
        if trans_kw:
            result = [e for e in result if trans_kw in (e.translation or "").lower()]

        return result

    def _clear_search(self):
        self._search_key.clear()
        self._search_orig.clear()
        self._search_trans.clear()
        self._populate_table()


    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_double_clicked(self, item: QTableWidgetItem):
        """双击进入编辑模式：只有译文列可编辑。"""
        if item.column() == _COL_TRANS:
            self._table.editItem(item)

    def _on_item_changed(self, item: QTableWidgetItem):
        """复选框变化或译文编辑后更新状态。"""
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return

        if item.column() == _COL_CHECK:
            # 复选框点击：切换选中状态
            if item.checkState() == Qt.CheckState.Checked:
                self._selected_entry_ids.add(entry.id)
            else:
                self._selected_entry_ids.discard(entry.id)
            self._update_count_label()
        elif item.column() == _COL_TRANS:
            # 译文编辑完成
            new_text = item.text().strip()
            if new_text == "（无译文）":
                new_text = ""
            entry.translation = new_text if new_text else ""
            if entry.translation and entry.stage < 2:
                entry.stage = 2
            self._table.blockSignals(True)
            if entry.translation:
                item.setForeground(QColor("#4CAF50"))
                item.setText(entry.translation[:80] if len(entry.translation) > 80 else entry.translation)
            else:
                item.setForeground(QColor("#9E9E9E"))
                item.setText("（无译文）")
            self._table.blockSignals(False)

    def _on_cell_clicked(self, row: int, col: int):
        """点击行任意单元格切换该行复选框（Ctrl追加，Shift范围）。"""
        if col == _COL_TRANS:
            return  # 译文列留给双击编辑
        item = self._table.item(row, _COL_CHECK)
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return

        modifiers = Qt.KeyboardModifier.NoModifier
        try:
            from PyQt6.QtWidgets import QApplication
            modifiers = QApplication.keyboardModifiers()
        except Exception:
            pass

        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)

        if shift and self._last_clicked_row is not None:
            # Shift：范围选择
            start = min(self._last_clicked_row, row)
            end = max(self._last_clicked_row, row)
            for r in range(start, end + 1):
                ri = self._table.item(r, _COL_CHECK)
                if ri:
                    re = ri.data(Qt.ItemDataRole.UserRole)
                    if isinstance(re, TranslationEntry) and re.id:
                        ri.setCheckState(Qt.CheckState.Checked)
                        self._selected_entry_ids.add(re.id)
        elif ctrl:
            # Ctrl：切换当前行
            current = item.checkState() == Qt.CheckState.Checked
            item.setCheckState(Qt.CheckState.Unchecked if current else Qt.CheckState.Checked)
            if current:
                self._selected_entry_ids.discard(entry.id)
            else:
                self._selected_entry_ids.add(entry.id)
        else:
            # 普通点击：单选切换
            current = item.checkState() == Qt.CheckState.Checked
            self._table.blockSignals(True)
            for r in range(self._table.rowCount()):
                ri = self._table.item(r, _COL_CHECK)
                if ri:
                    re = ri.data(Qt.ItemDataRole.UserRole)
                    if isinstance(re, TranslationEntry) and re.id:
                        ri.setCheckState(Qt.CheckState.Unchecked)
                        self._selected_entry_ids.discard(re.id)
            self._table.blockSignals(False)
            if not current:
                item.setCheckState(Qt.CheckState.Checked)
                self._selected_entry_ids.add(entry.id)
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
                self._selected_entry_ids.discard(entry.id)

        self._last_clicked_row = row
        self._update_count_label()

    def _update_count_label(self):
        selected = len(self.get_selected_entries())
        total = len(self._entries)
        shown = self._table.rowCount()

        if shown == total:
            self._count_lbl.setText(f"已选 {selected} 条 / 共 {total} 条")
        else:
            self._count_lbl.setText(f"已选 {selected} 条 / 显示 {shown} 条（共 {total} 条）")
