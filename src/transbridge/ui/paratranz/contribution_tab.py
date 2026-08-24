"""
ContributionTab: 贡献统计标签页。
"""

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_contribution_api import ParatranzScoresAPI
from transbridge.ui.foundation.components import reserve_text_width

from ..workers import ApiWorker
from ._layout_stability import configure_stable_table_columns


def _extract_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("data", "results", "items"):
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _infer_operation(base: float) -> str:
    """根据 base 值推断操作类型（避免浮点等值比较）。"""
    if base >= 0.9:
        return "翻译"
    if base >= 0.4:
        return "编辑"
    return "审核"


class ContributionTab(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._project_id: int | None = None
        self._scores: list[dict] = []
        self._gen = 0
        self._init_ui()
        ctx.project_selected.connect(self._on_project_changed)

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 筛选栏
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("用户ID:"))
        self._uid_input = QLineEdit()
        self._uid_input.setPlaceholderText("留空查全部")
        self._uid_input.setFixedWidth(80)
        filter_row.addWidget(self._uid_input)

        filter_row.addWidget(QLabel("类型:"))
        self._op_combo = QComboBox()
        for val, name in (("", "全部"), ("translate", "翻译"), ("edit", "编辑"), ("review", "审核")):
            self._op_combo.addItem(name, val)
        filter_row.addWidget(self._op_combo)

        filter_row.addWidget(QLabel("页码:"))
        self._page_spin = QSpinBox()
        self._page_spin.setRange(1, 9999)
        self._page_spin.setFixedWidth(60)
        filter_row.addWidget(self._page_spin)

        # 时间段筛选
        from PyQt6.QtWidgets import QDateEdit

        filter_row2 = QHBoxLayout()
        filter_row2.addWidget(QLabel("开始日期:"))
        self._start_date = QDateEdit()
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        self._start_date.setSpecialValueText("不限")
        self._start_date.setDate(QDate(2000, 1, 1))
        self._start_date.setMinimumDate(QDate(2000, 1, 1))
        self._start_date.setCalendarPopup(True)
        self._start_date.setFixedWidth(110)
        filter_row2.addWidget(self._start_date)

        self._start_enabled = QPushButton("启用")
        self._start_enabled.setCheckable(True)
        self._start_enabled.setFixedWidth(46)
        self._start_date.setEnabled(False)
        self._start_enabled.toggled.connect(self._start_date.setEnabled)
        filter_row2.addWidget(self._start_enabled)

        filter_row2.addWidget(QLabel("结束日期:"))
        self._end_date = QDateEdit()
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        self._end_date.setDate(QDate.currentDate())
        self._end_date.setCalendarPopup(True)
        self._end_date.setFixedWidth(110)
        self._end_date.setEnabled(False)
        filter_row2.addWidget(self._end_date)

        self._end_enabled = QPushButton("启用")
        self._end_enabled.setCheckable(True)
        self._end_enabled.setFixedWidth(46)
        self._end_enabled.toggled.connect(self._end_date.setEnabled)
        filter_row2.addWidget(self._end_enabled)

        refresh_btn = QPushButton("查询")
        refresh_btn.clicked.connect(self.load_scores)
        filter_row2.addWidget(refresh_btn)
        filter_row2.addStretch()
        layout.addLayout(filter_row)
        layout.addLayout(filter_row2)

        # 明细表格
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["时间", "用户 ID", "操作类型", "基准值", "乘数", "贡献值"])
        configure_stable_table_columns(
            self._table,
            fixed_widths={0: 160, 1: 100, 3: 100, 4: 100, 5: 110},
            stretch_columns=(2,),
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_summary)
        layout.addWidget(self._table, stretch=1)

        # 汇总面板
        summary_box = QGroupBox("汇总（当前筛选数据）")
        summary_layout = QHBoxLayout(summary_box)
        self._lbl_total_scores = QLabel("总贡献值：—")
        self._lbl_count = QLabel("条数：—")
        self._lbl_count.setFixedWidth(reserve_text_width(self._lbl_count, ("条数：—", "条数：999999")))
        self._lbl_total_scores.setMinimumWidth(
            reserve_text_width(self._lbl_total_scores, ("总贡献值：—", "总贡献值：-9999999999.99"))
        )
        summary_layout.addWidget(self._lbl_count)
        summary_layout.addWidget(self._lbl_total_scores)
        summary_layout.addStretch()
        layout.addWidget(summary_box)

    def _on_project_changed(self, project):
        self._project_id = project.get("id") if project else None
        self._table.setRowCount(0)
        self._scores = []
        if self._project_id:
            self.load_scores()

    def load_scores(self):
        if not self._project_id:
            return
        config = self._ctx.config
        pid = self._project_id
        uid_text = self._uid_input.text().strip()
        uid = int(uid_text) if uid_text.isdigit() else None
        op = self._op_combo.currentData() or None
        page = self._page_spin.value()
        self._gen += 1
        gen = self._gen

        start = (
            self._start_date.date().toString("yyyy-MM-dd") + "T00:00:00Z" if self._start_enabled.isChecked() else None
        )
        end = self._end_date.date().toString("yyyy-MM-dd") + "T23:59:59Z" if self._end_enabled.isChecked() else None

        def _fetch():
            api = ParatranzScoresAPI(token=config.token, config=config)
            return _extract_list(api.get_scores(pid, page=page, uid=uid, operation=op, start=start, end=end))

        def _on_done(scores):
            if self._gen != gen:
                return
            self._on_loaded(scores)

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: QMessageBox.warning(self, "加载失败", e))
        w.start()
        self._workers.append(w)

    def _on_loaded(self, scores: list):
        self._scores = scores
        self._table.setRowCount(len(scores))
        for row, s in enumerate(scores):
            base = s.get("base", 0)
            cells = [
                str(s.get("createdAt", ""))[:19],
                str(s.get("uid", "—")),
                _infer_operation(base),
                str(base),
                str(s.get("multiplier", "—")),
                str(s.get("value", "—")),
            ]
            for col, val in enumerate(cells):
                self._table.setItem(row, col, QTableWidgetItem(val))
        self._update_summary()

    def _update_summary(self):
        count = len(self._scores)
        total = sum(s.get("value", 0) for s in self._scores)
        self._lbl_count.setText(f"条数：{count}")
        self._lbl_total_scores.setText(f"总贡献值：{total:.2f}")
