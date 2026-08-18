"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及全部词条预览表格。
支持多选标签筛选、文本搜索、行内编辑、三态标记（★/?/✓）。
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QPushButton, QLineEdit, QDialog, QListWidget, QListWidgetItem,
    QAbstractItemView, QMenu,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QActionGroup

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry import (
    TranslationEntry,
    STAGE_LABELS, STAGE_COLORS,
    STAGE_LOCKED, STAGE_HIDDEN, STAGE_TRANSLATED,
)

# context → 显示类别名称的映射（复用 export 中的分类逻辑）
_CONTEXT_TO_CATEGORY: dict[str, str] = {}
_ROW_BATCH_SIZE = 250
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


# ────────────────────────────── 标签管理对话框 ──────────────────────────────

class _LabelManagerDialog(QDialog):
    """标签管理对话框：创建/编辑/删除标签，设置名称和颜色。"""

    def __init__(self, label_library: dict, parent=None):
        super().__init__(parent)
        self._labels = {lid: dict(info) for lid, info in label_library.items()}
        self._selected_id: str | None = None
        self._selected_color: str = _PRESET_COLORS[0]
        self.setWindowTitle("管理标签")
        self.resize(420, 320)
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        # 左侧列表
        self._list = QListWidget()
        self._list.setFixedWidth(180)
        self._list.currentRowChanged.connect(self._on_select)
        layout.addWidget(self._list)
        # 右侧编辑
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(QLabel("标签名称:"))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("输入标签名称")
        right.addWidget(self._name_edit)
        right.addWidget(QLabel("颜色:"))
        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        for c in _PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(24, 24)
            btn.setStyleSheet(
                f"background: {c}; border-radius: 12px; border: 2px solid "
                f"{'#333' if c == self._selected_color else 'transparent'};"
            )
            btn.clicked.connect(lambda checked, col=c: self._on_color_pick(col))
            color_row.addWidget(btn)
        right.addLayout(color_row)
        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._on_add)
        btn_row.addWidget(add_btn)
        self._delete_btn = QPushButton("删除")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        btn_row.addWidget(self._delete_btn)
        right.addLayout(btn_row)
        right.addStretch()
        # 确认/取消
        bottom = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom.addStretch()
        bottom.addWidget(ok_btn)
        bottom.addWidget(cancel_btn)
        right.addLayout(bottom)
        layout.addLayout(right)

    def _refresh_list(self):
        self._list.clear()
        for lid, info in self._labels.items():
            item = QListWidgetItem(f"● {info['name']}")
            item.setData(Qt.ItemDataRole.UserRole, lid)
            item.setForeground(QColor(info["color"]))
            self._list.addItem(item)

    def _on_select(self, row: int):
        if row < 0:
            self._selected_id = None
            self._name_edit.clear()
            self._delete_btn.setEnabled(False)
            return
        item = self._list.item(row)
        lid = item.data(Qt.ItemDataRole.UserRole)
        self._selected_id = lid
        info = self._labels[lid]
        self._name_edit.setText(info["name"])
        self._selected_color = info["color"]
        self._delete_btn.setEnabled(True)

    def _on_color_pick(self, color: str):
        self._selected_color = color
        if self._selected_id:
            self._labels[self._selected_id]["color"] = color
            self._refresh_list()

    def _on_add(self):
        import uuid
        name = self._name_edit.text().strip()
        if not name:
            return
        lid = uuid.uuid4().hex[:8]
        self._labels[lid] = {"name": name, "color": self._selected_color}
        self._name_edit.clear()
        self._refresh_list()

    def _on_delete(self):
        if not self._selected_id:
            return
        del self._labels[self._selected_id]
        self._selected_id = None
        self._name_edit.clear()
        self._delete_btn.setEnabled(False)
        self._refresh_list()

    def get_label_library(self) -> dict:
        return self._labels


# ────────────────────────────── 步骤2 主 Widget ──────────────────────────────

# 表格列常量（标记列替代复选框列）
_COL_MARK  = 0
_COL_KEY   = 1
_COL_ORIG  = 2
_COL_TRANS = 3
_COL_CTX   = 4
_COL_CHECK = _COL_MARK  # 向后兼容别名
_NUM_COLS  = 5

# 标签预设颜色
_PRESET_COLORS = ["#2196F3", "#FF9800", "#4CAF50", "#F44336", "#9C27B0", "#00BCD4", "#795548", "#607D8B"]

# 行背景色
_ROW_BG_GREEN  = QColor("#E8F5E9")   # 已翻译



class Step2PreviewWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._entries: list[TranslationEntry] = []  # 全部词条
        self._category_filters: set[str] = set()  # 多选分类标签
        self._stage_filters: set[int] = set()  # 多选翻译状态标签（0=未翻译,1=有疑问,2=已翻译）
        self._label_library: dict[str, dict] = {}  # label_id → {name, color}
        self._entry_labels: dict[str, set[str]] = {}  # entry_id → set[label_id]
        self._label_filters: set[str] = set()  # 标签筛选
        self._focus_labeled: bool = False  # 只看有标签条目
        self._filtered_total = 0
        self._render_generation = 0
        self._render_entries: tuple[TranslationEntry, ...] = ()
        self._pending_locate_entry_id: str | None = None
        self._tag_buttons: dict[str | int | None, QPushButton] = {}  # 标签按钮
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._populate_table)
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)
        if getattr(ctx, "uses_authoritative_projection", False):
            ctx.label_data_changed.connect(self._reload_projected_labels)
            self._reload_projected_labels()

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

        # 标签筛选行
        self._mark_tags_widget = QWidget()
        mark_tags_layout = QHBoxLayout(self._mark_tags_widget)
        mark_tags_layout.setContentsMargins(0, 0, 0, 0)
        mark_tags_layout.setSpacing(4)
        mark_tags_layout.addWidget(QLabel("标签："))
        self._mark_tags_container = QHBoxLayout()
        self._mark_tags_container.setSpacing(3)
        mark_tags_layout.addLayout(self._mark_tags_container)
        # 管理标签按钮
        manage_btn = QPushButton("管理标签")
        manage_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        manage_btn.clicked.connect(self._on_manage_labels)
        mark_tags_layout.addWidget(manage_btn)
        # 聚焦按钮
        self._focus_btn = QPushButton("[已标记]")
        self._focus_btn.setToolTip("只看有标签的条目")
        self._focus_btn.setStyleSheet(self._TAG_NORMAL)
        self._focus_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._focus_btn.clicked.connect(self._on_focus_toggle)
        self._focus_btn.setEnabled(False)
        mark_tags_layout.addWidget(self._focus_btn)
        mark_tags_layout.addStretch()
        self._mark_tags_widget.hide()
        outer.addWidget(self._mark_tags_widget)

        # 词条表格（标记列 + 行背景色）
        self._table = QTableWidget(0, _NUM_COLS)
        self._table.setHorizontalHeaderLabels(["", "Key", "原文", "译文", "类型"])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_MARK, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_MARK, 32)
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
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        outer.addWidget(self._table, stretch=1)

        # 底部计数。表格通过 Qt 事件循环自动增量渲染直至全部完成。
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
        """返回所有有标签的条目，供 AI 翻译浮窗使用。"""
        result = []
        id_to_entry = {e.id: e for e in self._entries if e.id}
        for entry_id in self._entry_labels:
            if entry_id in id_to_entry and self._entry_labels[entry_id]:
                result.append(id_to_entry[entry_id])
        return result

    def get_filtered_count(self) -> int:
        """返回当前筛选后显示的条数。"""
        return self._filtered_total

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
            self._entry_labels.clear()
            self._label_filters.clear()
            self._focus_labeled = False
            self._focus_btn.setStyleSheet(self._TAG_NORMAL)
            self._focus_btn.setEnabled(False)
            self._tags_widget.hide()
            self._stage_tags_widget.hide()
            self._mark_tags_widget.hide()
            self._search_widget.hide()
            self._table.setRowCount(0)
            self._filtered_total = 0
            self._render_generation += 1
            self._render_entries = ()
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
        self._entry_labels = {eid: ls for eid, ls in self._entry_labels.items() if eid in existing_ids}
        self._label_filters.clear()

        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
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

    # STAGE_LABELS 从 translation_entry 导入，不再本地定义

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
            counter[e.stage] += 1

        total = len(self._entries)

        # 「全部」标签
        all_btn = QPushButton(f"全部 {total}")
        all_btn.setStyleSheet(self._TAG_ACTIVE if not self._stage_filters else self._TAG_NORMAL)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._on_stage_tag_clicked(None))
        self._stage_tags_container.addWidget(all_btn)

        for stage_val, label_name in STAGE_LABELS.items():
            count = counter.get(stage_val, 0)
            if count == 0 and stage_val not in self._stage_filters:
                continue
            label = f"{label_name} {count}"
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

    # ── 标签筛选与管理 ─────────────────────────────────────────────────────

    def _ensure_default_labels(self):
        import uuid
        if not self._label_library:
            library = {}
            defaults = [
                ("待处理", "#2196F3"),
                ("有疑问", "#FF9800"),
                ("已确认", "#4CAF50"),
            ]
            for name, color in defaults:
                lid = uuid.uuid4().hex[:8]
                library[lid] = {"name": name, "color": color}
            if self._commit_labels(self._entry_labels, library):
                self._label_library = library

    def _on_manage_labels(self):
        dlg = _LabelManagerDialog(self._label_library, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_library = dlg.get_label_library()
            removed = set(self._label_library) - set(new_library)
            new_entry_labels = {key: set(value) for key, value in self._entry_labels.items()}
            for labels in new_entry_labels.values():
                labels.difference_update(removed)
            if not self._commit_labels(new_entry_labels, new_library):
                return
            self._entry_labels = new_entry_labels
            self._label_library = new_library
            self._build_label_tags()
            self._populate_table()

    def _build_label_tags(self):
        from collections import Counter
        while self._mark_tags_container.count():
            item = self._mark_tags_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._label_library:
            self._mark_tags_widget.hide()
            self._focus_btn.setEnabled(False)
            return

        counter = Counter()
        for labels in self._entry_labels.values():
            for lid in labels:
                counter[lid] += 1

        all_btn = QPushButton(f"全部 {sum(1 for ls in self._entry_labels.values() if ls)}")
        all_btn.setStyleSheet(self._TAG_ACTIVE if not self._label_filters else self._TAG_NORMAL)
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._on_label_tag_clicked(None))
        self._mark_tags_container.addWidget(all_btn)

        for lid, info in self._label_library.items():
            count = counter.get(lid, 0)
            if count == 0 and lid not in self._label_filters:
                continue
            btn = QPushButton(f"● {info['name']} {count}")
            btn.setStyleSheet(
                (self._TAG_ACTIVE if lid in self._label_filters else self._TAG_NORMAL) +
                f" color: {info['color']};"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, l=lid: self._on_label_tag_clicked(l))
            self._mark_tags_container.addWidget(btn)

        has_labels = any(ls for ls in self._entry_labels.values())
        self._focus_btn.setEnabled(has_labels)
        if not has_labels and self._focus_labeled:
            self._focus_labeled = False
            self._focus_btn.setStyleSheet(self._TAG_NORMAL)

        if not self._entries:
            self._mark_tags_widget.hide()
        else:
            self._mark_tags_widget.show()

    def _on_label_tag_clicked(self, lid: str | None):
        if lid is None:
            self._label_filters.clear()
        elif lid in self._label_filters:
            self._label_filters.discard(lid)
        else:
            self._label_filters.add(lid)
        self._build_label_tags()
        self._populate_table()

    def _on_focus_toggle(self):
        self._focus_labeled = not self._focus_labeled
        self._focus_btn.setStyleSheet(
            self._TAG_ACTIVE if self._focus_labeled else self._TAG_NORMAL
        )
        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    def _populate_table(self):
        self._render_generation += 1
        generation = self._render_generation
        self._render_entries = tuple(self._apply_all_filters())
        self._filtered_total = len(self._render_entries)
        self._table.clearContents()
        self._table.setRowCount(0)
        if self._filtered_total:
            self._progress.setRange(0, self._filtered_total)
            self._progress.setValue(0)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
        self._append_table_batch(generation)

    def _append_table_batch(self, generation: int) -> None:
        if generation != self._render_generation:
            return
        start = self._table.rowCount()
        end = min(start + _ROW_BATCH_SIZE, self._filtered_total)
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(_COL_CTX, QHeaderView.ResizeMode.Interactive)
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)

        self._table.setRowCount(end)

        for row in range(start, end):
            entry = self._render_entries[row]
            # 行背景色（按 ParaTranz stage）
            if entry.stage == STAGE_HIDDEN:
                row_bg = QColor("#F5F5F5")  # 已隐藏 → 浅灰
            elif entry.stage == STAGE_LOCKED:
                row_bg = QColor("#FFEBEE")  # 已锁定 → 浅红
            elif entry.stage >= STAGE_TRANSLATED:
                row_bg = _ROW_BG_GREEN       # 有译文 → 浅绿
            else:
                row_bg = None                # 未翻译 → 白色

            # Key 列文字颜色使用 Stage 色
            stage_color = QColor(STAGE_COLORS.get(entry.stage, "#000000"))

            # Col 0: 标签列（首个标签色圆点 + 数量）
            labels = self._entry_labels.get(entry.id, set()) if entry.id else set()
            if labels:
                first_lid = next(iter(labels))
                first_info = self._label_library.get(first_lid, {})
                count = len(labels)
                dot_text = f"● {count}" if count > 1 else "●"
                mark_item = QTableWidgetItem(dot_text)
                mark_item.setForeground(QColor(first_info.get("color", "#999")))
                tooltip_names = []
                for lid in labels:
                    info = self._label_library.get(lid)
                    if info:
                        tooltip_names.append(info['name'])
                mark_item.setToolTip("\n".join(tooltip_names))
            else:
                mark_item = QTableWidgetItem("")
            mark_item.setData(Qt.ItemDataRole.UserRole, entry)
            mark_item.setFlags(mark_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            mark_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if row_bg:
                mark_item.setBackground(row_bg)
            self._table.setItem(row, _COL_MARK, mark_item)

            # Col 1: Key（文字颜色=Stage 色）
            key_item = QTableWidgetItem(entry.key or "")
            key_item.setData(Qt.ItemDataRole.UserRole, entry)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            key_item.setForeground(stage_color)
            if row_bg:
                key_item.setBackground(row_bg)

            # Col 2: 原文
            orig_item = QTableWidgetItem(entry.original[:80] if entry.original else "")
            orig_item.setData(Qt.ItemDataRole.UserRole, entry)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if row_bg:
                orig_item.setBackground(row_bg)

            # Col 3: 译文（可编辑）
            trans_text = entry.translation or ""
            trans_item = QTableWidgetItem(trans_text[:80] if trans_text else "（无译文）")
            trans_item.setData(Qt.ItemDataRole.UserRole, entry)
            if trans_text:
                trans_item.setForeground(QColor("#4CAF50"))
            else:
                trans_item.setForeground(QColor("#9E9E9E"))
            if row_bg:
                trans_item.setBackground(row_bg)

            # Col 4: 类型
            ctx_item = QTableWidgetItem(_entry_category(entry))
            ctx_item.setData(Qt.ItemDataRole.UserRole, entry)
            ctx_item.setFlags(ctx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if row_bg:
                ctx_item.setBackground(row_bg)

            self._table.setItem(row, _COL_KEY,   key_item)
            self._table.setItem(row, _COL_ORIG,  orig_item)
            self._table.setItem(row, _COL_TRANS, trans_item)
            self._table.setItem(row, _COL_CTX,   ctx_item)

        self._table.setUpdatesEnabled(True)
        self._table.blockSignals(False)
        self._progress.setValue(end)
        self._update_count_label()
        self._select_pending_entry(start, end)
        if start == 0:
            # Size against the first batch only. Scanning tens of thousands of
            # populated rows at completion would freeze the GUI again.
            self._table.resizeColumnToContents(_COL_KEY)
            self._table.resizeColumnToContents(_COL_CTX)
            hh.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Interactive)
            hh.setSectionResizeMode(_COL_CTX, QHeaderView.ResizeMode.Interactive)
        if end < self._filtered_total:
            QTimer.singleShot(0, lambda: self._append_table_batch(generation))
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(100)

    def _select_pending_entry(self, start: int, end: int) -> None:
        target = self._pending_locate_entry_id
        if target is None:
            return
        for row in range(start, end):
            if self._render_entries[row].id != target:
                continue
            item = self._table.item(row, _COL_KEY)
            self._table.selectRow(row)
            self._table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._pending_locate_entry_id = None
            return

    def _apply_all_filters(self) -> list[TranslationEntry]:
        """叠加所有筛选条件（分类 + 状态 + 搜索 + 标记 + 聚焦），返回过滤后的词条列表。"""
        result = list(self._entries)

        # 分类筛选（多选 AND）
        if self._category_filters:
            result = [e for e in result if _entry_category(e) in self._category_filters]

        # Stage 筛选（精确匹配 ParaTranz stage 值）
        if self._stage_filters:
            result = [e for e in result if e.stage in self._stage_filters]

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

        # 标签筛选（多选 AND）
        if self._label_filters:
            result = [e for e in result
                      if e.id and self._entry_labels.get(e.id, set()) & self._label_filters]

        # 聚焦：只看有标签
        if self._focus_labeled:
            result = [e for e in result
                      if e.id and e.id in self._entry_labels and self._entry_labels[e.id]]

        return result

    def _clear_search(self):
        self._search_key.clear()
        self._search_orig.clear()
        self._search_trans.clear()
        self._populate_table()

    def get_filter_state(self) -> dict:
        """返回当前筛选状态，用于持久化。"""
        return {
            "category": list(self._category_filters),
            "stage": list(self._stage_filters),
            "label": list(self._label_filters),
            "search_key": self._search_key.text(),
            "search_orig": self._search_orig.text(),
            "search_trans": self._search_trans.text(),
        }

    def apply_filter_state(self, state: dict) -> None:
        """从持久化状态恢复筛选条件。"""
        if not state:
            return
        self._category_filters = set(state.get("category", []))
        self._stage_filters = set(state.get("stage", []))
        self._label_filters = set(state.get("label", []))
        self._search_key.setText(state.get("search_key", ""))
        self._search_orig.setText(state.get("search_orig", ""))
        self._search_trans.setText(state.get("search_trans", ""))
        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
        self._populate_table()

    def collect_labels(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """返回 (_entry_labels, _label_library) 副本，供持久化保存。"""
        return (
            {key: set(value) for key, value in self._entry_labels.items()},
            {key: dict(value) for key, value in self._label_library.items()},
        )

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_double_clicked(self, item: QTableWidgetItem):
        """双击进入编辑模式：只有译文列可编辑。"""
        if item.column() == _COL_TRANS:
            self._table.editItem(item)

    def _on_item_changed(self, item: QTableWidgetItem):
        """译文编辑后更新 entry 和行背景色。"""
        if item.column() != _COL_TRANS:
            return
        # Projection commands notify synchronously. A subscriber may rebuild the
        # table during the command, which deletes this QTableWidgetItem wrapper.
        # Capture every value needed from it before crossing that boundary.
        original_row = item.row()
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return
        new_text = item.text().strip()
        if new_text == "（无译文）":
            new_text = ""
        if getattr(self._ctx, "uses_authoritative_projection", False):
            stage = 2 if new_text and entry.stage < 2 else entry.stage
            result = self._ctx.update_projected_entry(
                entry.key,
                translation=new_text,
                stage=stage,
            )
            if not result.is_success:
                self._populate_table()
                return
        entry.translation = new_text if new_text else ""
        if entry.translation and entry.stage < 2:
            entry.stage = 2
        row, current_item = self._find_rendered_translation_item(original_row, entry.id)
        if current_item is None:
            return
        # 刷新该行显示（颜色 + 文本）
        if entry.stage == STAGE_HIDDEN:
            row_bg = QColor("#F5F5F5")
        elif entry.stage == STAGE_LOCKED:
            row_bg = QColor("#FFEBEE")
        elif entry.translation:
            row_bg = _ROW_BG_GREEN
        else:
            row_bg = None
        self._table.blockSignals(True)
        if entry.translation:
            current_item.setForeground(QColor("#4CAF50"))
            current_item.setText(entry.translation[:80] if len(entry.translation) > 80 else entry.translation)
        else:
            current_item.setForeground(QColor("#9E9E9E"))
            current_item.setText("（无译文）")
        # 刷新该行所有列的背景色
        for c in range(_NUM_COLS):
            ci = self._table.item(row, c)
            if ci:
                if row_bg:
                    ci.setBackground(row_bg)
                else:
                    ci.setData(Qt.ItemDataRole.BackgroundRole, None)
        self._table.blockSignals(False)

    def _find_rendered_translation_item(
        self,
        preferred_row: int,
        entry_id: str,
    ) -> tuple[int, QTableWidgetItem | None]:
        """Return the current item without dereferencing a deleted Qt wrapper."""
        if 0 <= preferred_row < self._table.rowCount():
            candidate = self._table.item(preferred_row, _COL_TRANS)
            candidate_entry = (
                None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            )
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return preferred_row, candidate
        for row in range(self._table.rowCount()):
            candidate = self._table.item(row, _COL_TRANS)
            candidate_entry = (
                None if candidate is None else candidate.data(Qt.ItemDataRole.UserRole)
            )
            if isinstance(candidate_entry, TranslationEntry) and candidate_entry.id == entry_id:
                return row, candidate
        return -1, None

    def _on_cell_clicked(self, row: int, col: int):
        """占位：标签分配由右键菜单处理。"""
        pass

    # ── 右键菜单 ────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        menu = self._build_context_menu(row)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _build_context_menu(self, row: int):
        item = self._table.item(row, _COL_KEY)
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return QMenu(self)

        from PyQt6.QtWidgets import QInputDialog

        menu = QMenu(self)
        labels = self._entry_labels.get(entry.id, set())

        # 标签部分
        label_menu = menu.addMenu("标签")
        if not self._label_library:
            no_label = label_menu.addAction("暂无标签，请先创建")
            no_label.setEnabled(False)
        else:
            for lid, info in self._label_library.items():
                action = label_menu.addAction(f"● {info['name']}")
                action.setCheckable(True)
                action.setChecked(lid in labels)
                action.toggled.connect(
                    lambda checked, eid=entry.id, l=lid: self._on_label_toggle(eid, l, checked)
                )
        label_menu.addSeparator()
        label_menu.addAction("管理标签…", self._on_manage_labels)
        label_menu.addAction("+ 新建标签…", lambda: self._on_quick_create_label(entry))

        # Stage 部分
        stage_menu = menu.addMenu("翻译状态")
        current_stage = entry.stage
        stage_group = QActionGroup(stage_menu)
        stage_group.setExclusive(True)
        for stage_val, stage_name in sorted(STAGE_LABELS.items()):
            action = stage_menu.addAction(stage_name)
            action.setCheckable(True)
            action.setChecked(stage_val == current_stage)
            stage_group.addAction(action)
            action.toggled.connect(
                lambda checked, e=entry, sv=stage_val: self._on_stage_change(e, sv) if checked else None
            )

        return menu

    def _on_label_toggle(self, entry_id: str, lid: str, checked: bool):
        entry_labels = {key: set(value) for key, value in self._entry_labels.items()}
        if entry_id not in entry_labels:
            entry_labels[entry_id] = set()
        if checked:
            entry_labels[entry_id].add(lid)
        else:
            entry_labels[entry_id].discard(lid)
        if not self._commit_labels(entry_labels, self._label_library):
            return
        self._entry_labels = entry_labels
        self._build_label_tags()
        self._populate_table()

    def _on_stage_change(self, entry: TranslationEntry, stage_val: int):
        if getattr(self._ctx, "uses_authoritative_projection", False):
            result = self._ctx.update_projected_entry(entry.key, stage=stage_val)
            if not result.is_success:
                return
        entry.stage = stage_val
        self._build_stage_tags()
        self._populate_table()

    def _on_quick_create_label(self, entry):
        from PyQt6.QtWidgets import QInputDialog
        import uuid, random
        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        name = name.strip()
        color = random.choice(_PRESET_COLORS)
        lid = uuid.uuid4().hex[:8]
        library = {key: dict(value) for key, value in self._label_library.items()}
        entry_labels = {key: set(value) for key, value in self._entry_labels.items()}
        library[lid] = {"name": name, "color": color}
        if entry.id not in entry_labels:
            entry_labels[entry.id] = set()
        entry_labels[entry.id].add(lid)
        if not self._commit_labels(entry_labels, library):
            return
        self._label_library = library
        self._entry_labels = entry_labels
        self._build_label_tags()
        self._populate_table()

    def _commit_labels(
        self,
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ) -> bool:
        if not getattr(self._ctx, "uses_authoritative_projection", False):
            return True
        result = self._ctx.replace_projected_labels(entry_labels, label_library)
        return result.is_success

    def _reload_projected_labels(self) -> None:
        self._label_library = self._ctx.label_library
        self._entry_labels = self._ctx.entry_labels
        if hasattr(self, "_mark_tags_container"):
            self._build_label_tags()
        if hasattr(self, "_table"):
            self._populate_table()

    def _update_count_label(self):
        labeled = sum(1 for ls in self._entry_labels.values() if ls)
        shown = self._table.rowCount()
        filtered = self._filtered_total
        total = len(self._entries)

        if filtered == total and shown == total:
            self._count_lbl.setText(f"有标签 {labeled} 条 | 共 {total} 条")
        elif shown < filtered:
            self._count_lbl.setText(
                f"有标签 {labeled} 条 | 已加载 {shown} 条（筛选结果 {filtered} 条，共 {total} 条）"
            )
        else:
            self._count_lbl.setText(f"有标签 {labeled} 条 | 筛选结果 {filtered} 条（共 {total} 条）")

    def locate_entry(self, entry_id: str):
        """在表格中定位到指定条目（清除筛选，滚动到行并选中）。"""
        # 清除所有筛选以便目标行可见
        self._category_filters.clear()
        self._stage_filters.clear()
        self._search_key.clear()
        self._search_orig.clear()
        self._search_trans.clear()
        if hasattr(self, '_label_filters'):
            self._label_filters.clear()
        self._focus_labeled = False
        self._focus_btn.setStyleSheet(self._TAG_NORMAL)

        # 更新标签UI
        self._build_category_tags()
        self._build_stage_tags()
        if hasattr(self, '_build_label_tags'):
            self._build_label_tags()

        # 刷新表格
        self._pending_locate_entry_id = entry_id
        self._populate_table()
