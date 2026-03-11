"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及全部词条预览表格。
双击词条可查看详情。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QDialog, QPushButton, QLineEdit,
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

    def __init__(self, entries: list[TranslationEntry], current_index: int, parent=None):
        super().__init__(parent)
        self._entries = entries
        self.setWindowTitle(f"词条预览（共 {len(entries)} 条）")
        self.resize(1100, 640)
        self._init_ui()
        self._apply_filter()
        # 筛选后定位到双击的行
        self._scroll_to_entry(current_index)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # 筛选栏
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

        # 结果计数标签
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._count_lbl)

        # 表格
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Key", "原文", "译文", "上下文"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, stretch=1)

        # 底部关闭按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _apply_filter(self):
        key_kw = self._filter_key.text().lower()
        orig_kw = self._filter_orig.text().lower()
        trans_kw = self._filter_trans.text().lower()

        matched = [
            e for e in self._entries
            if (not key_kw or key_kw in (e.id or "").lower())
            and (not orig_kw or orig_kw in (e.original or "").lower())
            and (not trans_kw or trans_kw in (e.translation or "").lower())
        ]

        self._table.setRowCount(len(matched))
        for row, entry in enumerate(matched):
            key_item = QTableWidgetItem(entry.id or "")
            orig_item = QTableWidgetItem(entry.original or "")
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text if trans_text else "（无译文）")
            if trans_text:
                trans_item.setForeground(QColor("#4CAF50"))
            else:
                trans_item.setForeground(QColor("#9E9E9E"))
            ctx_item = QTableWidgetItem(entry.context or "")
            for item in (key_item, orig_item, trans_item, ctx_item):
                item.setData(Qt.ItemDataRole.UserRole, entry)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, orig_item)
            self._table.setItem(row, 2, trans_item)
            self._table.setItem(row, 3, ctx_item)

        total = len(self._entries)
        shown = len(matched)
        self._count_lbl.setText(
            f"显示 {shown} / {total} 条" if shown != total else f"共 {total} 条"
        )

    def _clear_filter(self):
        self._filter_key.clear()
        self._filter_orig.clear()
        self._filter_trans.clear()

    def _scroll_to_entry(self, original_index: int):
        """尝试在当前过滤结果中定位到 original_index 对应的词条。"""
        if not self._entries or original_index >= len(self._entries):
            return
        target = self._entries[original_index]
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) is target:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                break


# ────────────────────────────── 步骤2 主 Widget ──────────────────────────────

class Step2PreviewWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._entries: list[TranslationEntry] = []
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)

    def _init_ui(self):
        box = QGroupBox("步骤2：解析结果预览")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(box)

        # 进度条（解析期间可见）
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

        # 词条列表（全部）
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Key", "原文", "译文", "类型"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, stretch=1)

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
            self._table.setRowCount(0)
            return

        total = len(collection)
        done = sum(1 for e in collection if e.stage >= 1 and e.translation)
        untranslated = sum(1 for e in collection if e.stage == 0 and not e.translation)

        self._progress.setValue(100)
        self._card_total.set_value(total)
        self._card_done.set_value(done)
        self._card_migrate.set_value(getattr(self._ctx, "migrate_count", 0))
        self._card_untranslated.set_value(untranslated)

        # 存储全部词条并填充表格
        self._entries = list(collection)

        hh = self._table.horizontalHeader()
        # 批量填充期间切换到 Interactive 模式，避免每次 setItem 都触发全列宽度重算（O(n²)）
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            key_item = QTableWidgetItem(entry.id[:60] if entry.id else "")
            orig_item = QTableWidgetItem(entry.original[:80] if entry.original else "")
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text[:80])
            if trans_text:
                trans_item.setForeground(QColor("#4CAF50"))
            else:
                trans_item.setForeground(QColor("#9E9E9E"))
                trans_item.setText("（无译文）")
            ctx_item = QTableWidgetItem(entry.context or "")
            for item in (key_item, orig_item, trans_item, ctx_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, key_item)
            self._table.setItem(row, 1, orig_item)
            self._table.setItem(row, 2, trans_item)
            self._table.setItem(row, 3, ctx_item)
        self._table.setUpdatesEnabled(True)
        # 填充完成后统一计算一次列宽，再恢复原模式
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(3)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

    def _on_double_clicked(self, item: QTableWidgetItem):
        row = item.row()
        if not self._entries or row >= len(self._entries):
            return
        dlg = _EntryDetailDialog(self._entries, row, parent=self)
        dlg.exec()