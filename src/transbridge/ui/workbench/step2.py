"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及全部词条预览表格。
双击词条可查看详情。支持多选列和筛选栏。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QDialog, QPushButton, QLineEdit, QComboBox, QCheckBox,
)
from PyQt6.QtCore import Qt
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


# ────────────────────────────── 词条详情对话框 ──────────────────────────────

class _EntryDetailDialog(QDialog):
    """放大版词条表格对话框：支持按 Key / 原文 / 译文 实时筛选。"""

    def __init__(self, entries: list[TranslationEntry], current_index: int,
                 initial_selected: list[TranslationEntry] | None = None, parent=None):
        super().__init__(parent)
        self._entries = entries
        self._selected_entries = []  # 用户确定的选中词条
        self._current_selected_ids = {e.id for e in initial_selected if e.id} if initial_selected else set()
        self.setWindowTitle(f"词条预览（共 {len(entries)} 条）")
        self.resize(1100, 680)
        self._init_ui()
        self._apply_filter()
        self._scroll_to_entry(current_index)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Key:"))
        self._filter_key = QLineEdit()
        self._filter_key.setPlaceholderText("按 Key 筛选…")
        self._filter_key.setClearButtonEnabled(True)
        self._filter_key.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_key)

        filter_row.addWidget(QLabel("原文:"))
        self._filter_orig = QLineEdit()
        self._filter_orig.setPlaceholderText("按原文筛选…")
        self._filter_orig.setClearButtonEnabled(True)
        self._filter_orig.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_orig)

        filter_row.addWidget(QLabel("译文:"))
        self._filter_trans = QLineEdit()
        self._filter_trans.setPlaceholderText("按译文筛选…")
        self._filter_trans.setClearButtonEnabled(True)
        self._filter_trans.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_trans)

        clear_btn = QPushButton("清除")
        clear_btn.setFixedWidth(52)
        clear_btn.clicked.connect(self._clear_filter)
        filter_row.addWidget(clear_btn)
        layout.addLayout(filter_row)

        # 全选复选框：全选/全不选（仅当前筛选行）
        self._header_check = QCheckBox()
        self._header_check.setToolTip("全选/全不选（仅筛选后可见行）")
        self._header_check.stateChanged.connect(self._on_header_check_changed)

        # 第二行筛选栏：翻译状态 + 类型 + 全选
        filter_row2 = QHBoxLayout()
        filter_row2.setSpacing(6)
        filter_row2.addWidget(QLabel("翻译状态:"))
        self._filter_stage = QComboBox()
        self._filter_stage.addItems(["全部", "未翻译", "已翻译", "仅机翻"])
        self._filter_stage.setFixedWidth(90)
        self._filter_stage.currentIndexChanged.connect(self._apply_filter)
        filter_row2.addWidget(self._filter_stage)

        filter_row2.addWidget(QLabel("类型:"))
        self._filter_category = QComboBox()
        self._filter_category.addItem("全部类型")
        for cat in _ALL_CATEGORIES:
            self._filter_category.addItem(cat)
        self._filter_category.setFixedWidth(100)
        self._filter_category.currentIndexChanged.connect(self._apply_filter)
        filter_row2.addWidget(self._filter_category)

        filter_row2.addStretch()
        filter_row2.addWidget(QLabel("全选:"))
        filter_row2.addWidget(self._header_check)
        layout.addLayout(filter_row2)

        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._count_lbl)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["", "Key", "原文", "译文", "上下文"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 30)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)

        layout.addWidget(self._table, stretch=1)

        # 连接复选框变化信号
        self._table.itemChanged.connect(self._on_item_changed)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(self._on_ok_clicked)
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _apply_filter(self):
        key_kw = self._filter_key.text().lower()
        orig_kw = self._filter_orig.text().lower()
        trans_kw = self._filter_trans.text().lower()
        stage_idx = self._filter_stage.currentIndex()   # 0=全部 1=未翻译 2=已翻译 3=仅机翻
        cat_text = self._filter_category.currentText()
        filter_cat = None if cat_text == "全部类型" else cat_text

        matched = []
        for e in self._entries:
            # 翻译状态筛选
            if stage_idx == 1 and (e.translation or e.stage >= 1):
                continue
            elif stage_idx == 2 and (not e.translation or e.stage < 1):
                continue
            elif stage_idx == 3 and e.stage != 1:
                continue

            # 类型筛选
            if filter_cat and _entry_category(e) != filter_cat:
                continue

            # 关键词筛选
            if key_kw and key_kw not in (e.id or "").lower():
                continue
            if orig_kw and orig_kw not in (e.original or "").lower():
                continue
            if trans_kw and trans_kw not in (e.translation or "").lower():
                continue

            matched.append(e)

        self._table.setRowCount(len(matched))
        for row, entry in enumerate(matched):
            # 第0列：复选框
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            # 根据当前选中ID集合设置复选框状态
            if entry.id and entry.id in self._current_selected_ids:
                check_item.setCheckState(Qt.CheckState.Checked)
            else:
                check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, entry)

            # 第1列：Key
            key_item = QTableWidgetItem(entry.id or "")
            key_item.setData(Qt.ItemDataRole.UserRole, entry)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # 第2列：原文
            orig_item = QTableWidgetItem(entry.original or "")
            orig_item.setData(Qt.ItemDataRole.UserRole, entry)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # 第3列：译文
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text if trans_text else "（无译文）")
            trans_item.setData(Qt.ItemDataRole.UserRole, entry)
            trans_item.setFlags(trans_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if trans_text:
                trans_item.setForeground(QColor("#4CAF50"))
            else:
                trans_item.setForeground(QColor("#9E9E9E"))

            # 第4列：上下文
            ctx_item = QTableWidgetItem(entry.context or "")
            ctx_item.setData(Qt.ItemDataRole.UserRole, entry)
            ctx_item.setFlags(ctx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self._table.setItem(row, 0, check_item)
            self._table.setItem(row, 1, key_item)
            self._table.setItem(row, 2, orig_item)
            self._table.setItem(row, 3, trans_item)
            self._table.setItem(row, 4, ctx_item)

        # 重置表头复选框为未选中
        self._header_check.blockSignals(True)
        self._header_check.setChecked(False)
        self._header_check.blockSignals(False)

        # 更新计数标签
        self._update_selection_count()

    def _clear_filter(self):
        self._filter_key.clear()
        self._filter_orig.clear()
        self._filter_trans.clear()
        self._filter_stage.setCurrentIndex(0)
        self._filter_category.setCurrentIndex(0)

    def _scroll_to_entry(self, original_index: int):
        if not self._entries or original_index >= len(self._entries):
            return
        target = self._entries[original_index]
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)  # 第1列是Key列
            if item and item.data(Qt.ItemDataRole.UserRole) is target:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                break

    # ── 多选功能 ──────────────────────────────────────────────────────────────

    def get_selected_entries(self) -> list[TranslationEntry]:
        """返回当前勾选的词条列表。"""
        # 基于当前选中ID集合返回所有选中的词条，不仅仅是当前显示的
        result = []
        id_to_entry = {e.id: e for e in self._entries if e.id}
        for entry_id in self._current_selected_ids:
            if entry_id in id_to_entry:
                result.append(id_to_entry[entry_id])
        return result

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == 0:  # 复选框列
            entry = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry, TranslationEntry) and entry.id:
                if item.checkState() == Qt.CheckState.Checked:
                    self._current_selected_ids.add(entry.id)
                else:
                    self._current_selected_ids.discard(entry.id)
            self._update_selection_count()

    def _on_header_check_changed(self, state: int):
        checked = state == Qt.CheckState.Checked.value
        # 临时断开信号避免递归
        self._table.itemChanged.disconnect(self._on_item_changed)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                # 同时更新当前选中ID集合
                entry = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(entry, TranslationEntry) and entry.id:
                    if checked:
                        self._current_selected_ids.add(entry.id)
                    else:
                        self._current_selected_ids.discard(entry.id)
        self._table.itemChanged.connect(self._on_item_changed)
        self._update_selection_count()

    def _update_selection_count(self):
        """更新计数标签，显示筛选数量和选中数量。"""
        total = len(self._entries)
        shown = self._table.rowCount()  # 当前显示的行数（筛选后）
        selected = len(self.get_selected_entries())

        if shown == total:
            text = f"共 {total} 条 | 已选 {selected} 条"
        else:
            text = f"显示 {shown} / {total} 条 | 已选 {selected} 条"

        self._count_lbl.setText(text)

    def _on_ok_clicked(self):
        """确定按钮点击：保存当前选中词条并关闭对话框。"""
        self._selected_entries = self.get_selected_entries()
        self.accept()

    def get_confirmed_selection(self) -> list[TranslationEntry]:
        """返回用户确定的选中词条列表（点击确定后）。"""
        return self._selected_entries


# ────────────────────────────── 步骤2 主 Widget ──────────────────────────────

# 表格列常量（含复选框列）
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
        self._filtered_entries: list[TranslationEntry] | None = None  # None=显示全部，否则显示过滤后的
        self._selected_entry_ids: set[str] = set()  # 选中的词条ID集合
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)

    # ── 初始化 UI ────────────────────────────────────────────────────────────

    def _init_ui(self):
        box = QGroupBox("步骤2：解析结果预览")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(box)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(8)
        layout.addWidget(self._progress)

        # 四格统计卡
        cards_row = QHBoxLayout()
        self._card_total = _StatCard("总词条", "#212121")
        self._card_done = _StatCard("已有译文", "#4CAF50")
        self._card_migrate = _StatCard("迁移", "#2196F3")
        self._card_untranslated = _StatCard("未翻译", "#F44336")
        for c in (self._card_total, self._card_done, self._card_migrate, self._card_untranslated):
            cards_row.addWidget(c)
        layout.addLayout(cards_row)


        # 提示标签
        hint = QLabel("双击词条可查看详情")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        # 词条表格（含复选框列）
        self._table = QTableWidget(0, _NUM_COLS)
        self._table.setHorizontalHeaderLabels(["", "Key", "原文", "译文", "类型"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_CHECK, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_CHECK, 30)
        hh.setSectionResizeMode(_COL_KEY,   QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(_COL_ORIG,  QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_TRANS, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(_COL_CTX,   QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)

        # 表头复选框：全选/全不选
        self._header_check = QCheckBox()
        self._header_check.setToolTip("全选/全不选（仅筛选后可见行）")
        self._header_check.stateChanged.connect(self._on_header_check_changed)
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self._table, stretch=1)

        # 复选框变化信号（只连接一次）
        self._table.itemChanged.connect(self._on_item_changed)

        # 底部计数标签
        self._count_lbl = QLabel("已选 0 条 / 共 0 条")
        self._count_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._count_lbl)

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_selected_entries(self) -> list[TranslationEntry]:
        """返回当前勾选的词条列表，供 AI 翻译浮窗使用。"""
        result = []
        # 通过选中ID集合查找对应的词条对象
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
            self._filtered_entries = None  # 重置过滤状态
            self._selected_entry_ids.clear()  # 清除选中状态
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
        self._filtered_entries = None  # 重置过滤状态

        # 过滤选中集合，只保留仍然存在的词条ID
        existing_ids = {e.id for e in collection if e.id}
        self._selected_entry_ids = {eid for eid in self._selected_entry_ids if eid in existing_ids}

        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    def _populate_table(self):
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_CTX, QHeaderView.ResizeMode.Interactive)
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)

        # 确定要显示的词条列表
        entries_to_show = self._filtered_entries if self._filtered_entries is not None else self._entries
        self._table.setRowCount(len(entries_to_show))

        for row, entry in enumerate(entries_to_show):
            # Col 0: 复选框
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            # 根据选中集合设置复选框状态
            if entry.id and entry.id in self._selected_entry_ids:
                check_item.setCheckState(Qt.CheckState.Checked)
            else:
                check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, entry)
            self._table.setItem(row, _COL_CHECK, check_item)

            # Col 1: Key
            key_item = QTableWidgetItem(entry.id[:60] if entry.id else "")
            key_item.setData(Qt.ItemDataRole.UserRole, entry)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Col 2: 原文
            orig_item = QTableWidgetItem(entry.original[:80] if entry.original else "")
            orig_item.setData(Qt.ItemDataRole.UserRole, entry)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Col 3: 译文
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text[:80] if trans_text else "（无译文）")
            trans_item.setData(Qt.ItemDataRole.UserRole, entry)
            trans_item.setFlags(trans_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
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

    def _apply_filter_to_table(self, selected_entries: list[TranslationEntry] | None):
        """
        应用过滤到表格。
        - selected_entries: None 表示显示全部词条，否则只显示选中的词条
        """
        self._filtered_entries = selected_entries

        # 更新选中词条ID集合
        if selected_entries is None:
            # 显示全部词条，清除所有选中状态
            self._selected_entry_ids.clear()
        else:
            # 使用选中的词条更新选中集合
            new_selected_ids = {e.id for e in selected_entries if e.id}
            self._selected_entry_ids = new_selected_ids

        self._populate_table()
        self._update_count_label()


    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_item_changed(self, item: QTableWidgetItem):
        if item.column() == _COL_CHECK:
            entry = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(entry, TranslationEntry) and entry.id:
                if item.checkState() == Qt.CheckState.Checked:
                    self._selected_entry_ids.add(entry.id)
                else:
                    self._selected_entry_ids.discard(entry.id)
            self._update_count_label()

    def _on_header_check_changed(self, state: int):
        checked = state == Qt.CheckState.Checked.value
        self._table.itemChanged.disconnect(self._on_item_changed)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, _COL_CHECK)
            if item:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                # 同时更新选中集合
                entry = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(entry, TranslationEntry) and entry.id:
                    if checked:
                        self._selected_entry_ids.add(entry.id)
                    else:
                        self._selected_entry_ids.discard(entry.id)
        self._table.itemChanged.connect(self._on_item_changed)
        self._update_count_label()

    def _on_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if not self._entries or row >= len(self._entries):
            return
        # 获取当前选中的词条传递给对话框
        current_selected = self.get_selected_entries()
        dlg = _EntryDetailDialog(self._entries, row, initial_selected=current_selected, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            selected = dlg.get_confirmed_selection()
            if selected:  # 有选中的词条，只显示这些
                self._apply_filter_to_table(selected)
            else:  # 没有选中的词条，显示全部
                self._apply_filter_to_table(None)

    def _update_count_label(self):
        selected = len(self.get_selected_entries())
        total = len(self._entries)
        shown = self._table.rowCount()  # 当前显示的行数（过滤后）

        if shown == total:
            self._count_lbl.setText(f"已选 {selected} 条 / 共 {total} 条")
        else:
            self._count_lbl.setText(f"已选 {selected} 条 / 显示 {shown} 条（共 {total} 条）")
