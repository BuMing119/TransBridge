"""混合模式规则映射表编辑器。

QTableWidget 展示规则列表，支持添加/删除/上移/下移/重置默认。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    pass

# ── 预设 ─────────────────────────────────────────────────────────────────────

_ALL_CATEGORIES = ["人名", "地名", "书名", "书籍内容", "物品", "法术技能", "对话", "互动", "任务日志", "其他"]
_ALL_STAGES = {0: "未翻译", 1: "已翻译", 2: "有疑问", 3: "已检查", 5: "已审核", 9: "已锁定", -1: "已隐藏"}

_DEFAULT_RULES: list[dict] = [
    {"priority": 0, "status_filter": [0], "action": "translate"},
    {"priority": 1, "status_filter": [1, 2, 3, 5], "action": "polish"},
    {"priority": 2, "status_filter": [9, -1], "action": "skip"},
]

_ACTION_LABELS = {"translate": "翻译", "polish": "润色", "skip": "跳过"}


class _RuleEditorWidget(QWidget):
    """规则映射表编辑器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules: list[dict] = []  # 内部用 dict 存储，导出时转为 ActionRule

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("动作分配规则（从上到下按优先级匹配）:"))
        toolbar.addStretch()
        self._btn_add = QPushButton("＋ 添加")
        self._btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(self._btn_add)
        self._btn_up = QPushButton("↑")
        self._btn_up.clicked.connect(self._on_move_up)
        toolbar.addWidget(self._btn_up)
        self._btn_down = QPushButton("↓")
        self._btn_down.clicked.connect(self._on_move_down)
        toolbar.addWidget(self._btn_down)
        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._on_delete)
        toolbar.addWidget(self._btn_delete)
        self._btn_reset = QPushButton("重置默认")
        self._btn_reset.clicked.connect(self.reset_to_default)
        toolbar.addWidget(self._btn_reset)
        layout.addLayout(toolbar)

        # 表格
        headers = ["优先级", "翻译状态", "分类", "动作"]
        self._table = QTableWidget(0, len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        self.reset_to_default()

    # ── 公共接口 ──────────────────────────────────────────────────────────

    def get_rules(self) -> list:
        """导出为 ActionRule 列表。"""
        from transbridge.paratranz.config_manager import ActionRule

        result = []
        for i, r in enumerate(self._rules):
            result.append(
                ActionRule(
                    rule_id=r.get("rule_id", ""),
                    priority=r["priority"],
                    status_filter=r.get("status_filter"),
                    label_filter=r.get("label_filter"),
                    category_filter=r.get("category_filter"),
                    action=r.get("action", "skip"),
                )
            )
        return result

    def set_rules(self, rules: list) -> None:
        """从 ActionRule 列表导入。"""
        self._rules = [r.to_dict() if hasattr(r, "to_dict") else r for r in rules]
        self._refresh_table()

    def reset_to_default(self):
        """重置为智能默认规则。"""
        import uuid

        self._rules = []
        for d in _DEFAULT_RULES:
            r = dict(d)
            r["rule_id"] = uuid.uuid4().hex[:8]
            r["label_filter"] = None
            r["category_filter"] = None
            r["status_filter"] = set(r.get("status_filter", []))
            self._rules.append(r)
        self._refresh_table()

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _refresh_table(self):
        self._table.setRowCount(len(self._rules))
        for i, r in enumerate(self._rules):
            # 优先级
            self._table.setItem(i, 0, QTableWidgetItem(str(r["priority"])))
            # 状态筛选
            stages = r.get("status_filter")
            if stages:
                names = [_ALL_STAGES.get(s, str(s)) for s in sorted(stages)]
                self._table.setItem(i, 1, QTableWidgetItem(", ".join(names)))
            else:
                self._table.setItem(i, 1, QTableWidgetItem("全部"))
            # 分类筛选
            cats = r.get("category_filter")
            if cats:
                self._table.setItem(i, 2, QTableWidgetItem(", ".join(sorted(cats))))
            else:
                self._table.setItem(i, 2, QTableWidgetItem("全部"))
            # 动作（QComboBox）
            combo = QComboBox()
            combo.addItems(["翻译", "润色", "跳过"])
            action_map = {"translate": 0, "polish": 1, "skip": 2}
            combo.setCurrentIndex(action_map.get(r.get("action", "skip"), 2))
            combo.currentIndexChanged.connect(lambda idx, row=i: self._on_action_changed(row, idx))
            self._table.setCellWidget(i, 3, combo)

    def _on_action_changed(self, row: int, idx: int):
        action_map = {0: "translate", 1: "polish", 2: "skip"}
        self._rules[row]["action"] = action_map.get(idx, "skip")

    def _on_add(self):
        import uuid

        self._rules.append({
            "rule_id": uuid.uuid4().hex[:8],
            "priority": len(self._rules),
            "status_filter": None,
            "label_filter": None,
            "category_filter": None,
            "action": "skip",
        })
        self._refresh_table()

    def _on_delete(self):
        rows = set(i.row() for i in self._table.selectedItems())
        if not rows:
            return
        for row in sorted(rows, reverse=True):
            if row < len(self._rules):
                self._rules.pop(row)
        self._refresh_table()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row > 0:
            self._rules[row], self._rules[row - 1] = self._rules[row - 1], self._rules[row]
            self._refresh_table()
            self._table.selectRow(row - 1)

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < len(self._rules) - 1:
            self._rules[row], self._rules[row + 1] = self._rules[row + 1], self._rules[row]
            self._refresh_table()
            self._table.selectRow(row + 1)
