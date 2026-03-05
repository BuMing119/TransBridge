"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及前 10 条词条预览表格。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection

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


class Step2PreviewWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
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
        self._card_migrate = _StatCard("EET/XT 迁移", "#2196F3")
        self._card_untranslated = _StatCard("未翻译", "#F44336")
        for c in (self._card_total, self._card_done, self._card_migrate, self._card_untranslated):
            cards_row.addWidget(c)
        layout.addLayout(cards_row)

        # 词条预览表格（最多 10 行）
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Key", "原文", "译文", "类型"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setMaximumHeight(220)
        layout.addWidget(self._table)

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
            self._table.setRowCount(0)
            return

        total = len(collection)
        done = sum(1 for e in collection if e.stage >= 1 and e.translation)
        untranslated = sum(1 for e in collection if e.stage == 0 and not e.translation)

        self._progress.setValue(100)
        self._card_total.set_value(total)
        self._card_done.set_value(done)
        self._card_migrate.set_value(0)  # EET/XT 迁移数暂无单独统计
        self._card_untranslated.set_value(untranslated)

        # 填充表格（前 10 行）
        preview = list(collection)[:10]
        self._table.setRowCount(len(preview))
        for row, entry in enumerate(preview):
            key_item = QTableWidgetItem(entry.id[:40] if entry.id else "")
            orig_item = QTableWidgetItem(entry.original[:60] if entry.original else "")
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text[:60])
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
