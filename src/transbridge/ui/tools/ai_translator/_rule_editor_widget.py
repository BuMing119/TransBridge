"""混合模式规则映射表编辑器。

QTableWidget 展示规则列表，支持添加/删除/上移/下移/重置默认。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.converter.context_categories import ALL_DISPLAY_CATEGORIES

# ── 预设 ─────────────────────────────────────────────────────────────────────

_ALL_CATEGORIES = ALL_DISPLAY_CATEGORIES
_ALL_STAGES = {0: "未翻译", 1: "已翻译", 2: "有疑问", 3: "已检查", 5: "已审核", 9: "已锁定", -1: "已隐藏"}

_DEFAULT_RULES: list[dict] = [
    {"priority": 0, "status_filter": [0], "action": "translate"},
    {"priority": 1, "status_filter": [1, 2, 3, 5], "action": "polish"},
    {"priority": 2, "status_filter": [9, -1], "action": "skip"},
]

_ACTION_OPTIONS = (("translate", "翻译"), ("polish", "润色"), ("skip", "跳过"))


def _options_with_compatibility_values(
    options: list[tuple[object, str]],
    selected: set,
) -> list[tuple[object, str]]:
    known = {value for value, _label in options}
    extras = sorted(selected - known, key=str)
    return [*options, *((value, f"兼容原始值：{value}") for value in extras)]


class _CheckableFilterButton(QToolButton):
    """Compact multi-select dropdown whose empty selection means no filter."""

    selection_changed = pyqtSignal(object)

    def __init__(self, options: list[tuple[object, str]], selected: set | None, parent=None) -> None:
        super().__init__(parent)
        self._menu = QMenu(self)
        clear_action = self._menu.addAction("全部")
        clear_action.triggered.connect(self._clear)
        self._menu.addSeparator()
        self._actions = []
        selected_values = set(selected or ())
        for value, label in options:
            action = self._menu.addAction(label)
            action.setCheckable(True)
            action.setData(value)
            action.setChecked(value in selected_values)
            action.toggled.connect(self._selection_toggled)
            self._actions.append(action)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._refresh_text()

    @property
    def selected(self) -> set | None:
        values = {action.data() for action in self._actions if action.isChecked()}
        return values or None

    def _clear(self) -> None:
        for action in self._actions:
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._refresh_text()
        self.selection_changed.emit(None)

    def _selection_toggled(self, _checked: bool) -> None:
        self._refresh_text()
        self.selection_changed.emit(self.selected)

    def _refresh_text(self) -> None:
        labels = [action.text() for action in self._actions if action.isChecked()]
        full_text = "、".join(labels) if labels else "全部"
        self.setText(f"{labels[0]}、{labels[1]} +{len(labels) - 2}" if len(labels) > 2 else full_text)
        self.setToolTip(f"当前：{full_text}；可多选，未选择表示全部")


class _RuleEditorWidget(QWidget):
    """规则映射表编辑器。"""

    rules_changed = pyqtSignal()

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
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
        self._rules = []
        for value in rules:
            rule = dict(value.to_dict() if hasattr(value, "to_dict") else value)
            for field in ("status_filter", "label_filter", "category_filter"):
                rule[field] = set(rule[field]) if rule.get(field) else None
            self._rules.append(rule)
        self._rules.sort(key=lambda rule: (rule.get("priority", 0), rule.get("rule_id", "")))
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
        self.rules_changed.emit()

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _refresh_table(self):
        self._normalize_priorities()
        self._table.setRowCount(len(self._rules))
        for i, r in enumerate(self._rules):
            # 优先级
            priority_item = QTableWidgetItem(str(r["priority"]))
            priority_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(i, 0, priority_item)
            # 状态筛选
            selected_stages = set(r.get("status_filter") or ())
            stage_filter = _CheckableFilterButton(
                _options_with_compatibility_values(list(_ALL_STAGES.items()), selected_stages),
                selected_stages,
                self._table,
            )
            stage_filter.selection_changed.connect(
                lambda selected, row=i: self._on_filter_changed(row, "status_filter", selected)
            )
            self._table.setCellWidget(i, 1, stage_filter)
            # 分类筛选
            selected_categories = set(r.get("category_filter") or ())
            category_options = [(category, category) for category in _ALL_CATEGORIES]
            category_filter = _CheckableFilterButton(
                _options_with_compatibility_values(category_options, selected_categories),
                selected_categories,
                self._table,
            )
            category_filter.selection_changed.connect(
                lambda selected, row=i: self._on_filter_changed(row, "category_filter", selected)
            )
            self._table.setCellWidget(i, 2, category_filter)
            # 动作（QComboBox）
            combo = QComboBox()
            for value, label in _ACTION_OPTIONS:
                combo.addItem(label, value)
            combo.setCurrentIndex(max(0, combo.findData(r.get("action", "skip"))))
            combo.currentIndexChanged.connect(lambda _idx, row=i, widget=combo: self._on_action_changed(row, widget))
            self._table.setCellWidget(i, 3, combo)

    def _on_filter_changed(self, row: int, field: str, selected: set | None) -> None:
        if row >= len(self._rules):
            return
        self._rules[row][field] = None if selected is None else set(selected)
        self.rules_changed.emit()

    def _on_action_changed(self, row: int, combo: QComboBox) -> None:
        if row >= len(self._rules):
            return
        self._rules[row]["action"] = combo.currentData() or "skip"
        self.rules_changed.emit()

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
        self.rules_changed.emit()

    def _on_delete(self):
        rows = set(i.row() for i in self._table.selectedItems())
        if not rows:
            return
        for row in sorted(rows, reverse=True):
            if row < len(self._rules):
                self._rules.pop(row)
        self._refresh_table()
        self.rules_changed.emit()

    def _on_move_up(self):
        row = self._table.currentRow()
        if row > 0:
            self._rules[row], self._rules[row - 1] = self._rules[row - 1], self._rules[row]
            self._refresh_table()
            self._table.selectRow(row - 1)
            self.rules_changed.emit()

    def _on_move_down(self):
        row = self._table.currentRow()
        if row < len(self._rules) - 1:
            self._rules[row], self._rules[row + 1] = self._rules[row + 1], self._rules[row]
            self._refresh_table()
            self._table.selectRow(row + 1)
            self.rules_changed.emit()

    def _normalize_priorities(self) -> None:
        for priority, rule in enumerate(self._rules):
            rule["priority"] = priority
