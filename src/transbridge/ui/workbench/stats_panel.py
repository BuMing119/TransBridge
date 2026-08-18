"""
CollectionStatsPanel: 工作台左侧集合统计面板。
显示总词条数、已翻译数、来源文件名以及按分类的树形统计。
"""

from collections import defaultdict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem, QGroupBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.context_categories import EXPORT_CATEGORIES, ROUND2_PREFIXES

# 从 EXPORT_CATEGORIES 派生：context → 分类名（去掉 .json 后缀）
_CONTEXT_TO_CATEGORY: dict[str, str] = {
    ctx: filename.removesuffix(".json")
    for filename, contexts in EXPORT_CATEGORIES.items()
    for ctx in contexts
}


def _get_category(context: str) -> str:
    ctx = context.split("|")[0] if "|" in context else context
    if ctx in _CONTEXT_TO_CATEGORY:
        return _CONTEXT_TO_CATEGORY[ctx]
    if any(ctx.startswith(p) for p in ROUND2_PREFIXES):
        return "对话"
    return "其他"


class CollectionStatsPanel(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)

    def _init_ui(self):
        self.setMinimumWidth(200)
        self.setMaximumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        # 摘要区
        summary_box = QGroupBox("集合摘要")
        summary_layout = QVBoxLayout(summary_box)
        self._lbl_total = QLabel("总词条数：—")
        self._lbl_translated = QLabel("已翻译：—")
        self._lbl_source = QLabel("来源：—")
        for lbl in (self._lbl_total, self._lbl_translated, self._lbl_source):
            lbl.setWordWrap(True)
            summary_layout.addWidget(lbl)
        layout.addWidget(summary_box)

        # 分类树
        tree_box = QGroupBox("按分类统计")
        tree_layout = QVBoxLayout(tree_box)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["分类", "词条", "已译"])
        self._tree.setColumnWidth(0, 110)
        self._tree.setColumnWidth(1, 45)
        self._tree.setColumnWidth(2, 45)
        tree_layout.addWidget(self._tree)
        layout.addWidget(tree_box, stretch=1)

        self._show_empty()

    def _show_empty(self):
        self._lbl_total.setText("总词条数：—")
        self._lbl_translated.setText("已翻译：—")
        self._lbl_source.setText("未加载，请先解析插件")
        self._tree.clear()

    def refresh(self, collection: TranslationEntryCollection | None):
        if collection is None:
            self._show_empty()
            return

        total = len(collection)
        translated = sum(1 for e in collection if e.stage >= 1 and e.translation)

        pct = f"{translated / total * 100:.0f}%" if total else "—"
        self._lbl_total.setText(f"总词条数：{total}")
        self._lbl_translated.setText(f"已翻译：{translated}（{pct}）")
        self._lbl_source.setText("来源：已加载")

        # 统计分类
        cat_total: dict[str, int] = defaultdict(int)
        cat_done: dict[str, int] = defaultdict(int)
        for entry in collection:
            cat = _get_category(entry.context or "")
            cat_total[cat] += 1
            if entry.stage >= 1 and entry.translation:
                cat_done[cat] += 1

        self._tree.clear()
        order = ["人名", "物品", "对话", "书籍_书名", "书籍_内容", "互动",
                 "任务日志", "地名与门", "法术_龙吼_技能", "其他"]
        for cat in order:
            if cat not in cat_total:
                continue
            t = cat_total[cat]
            d = cat_done[cat]
            item = QTreeWidgetItem([cat, str(t), str(d)])
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight)
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
            color = QColor("#4CAF50") if d == t else QColor("#FF9800")
            item.setForeground(2, color)
            self._tree.addTopLevelItem(item)
