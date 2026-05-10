"""AI翻译/润色结果报告对话框。

多Tab结构：汇总（统计卡片）+ 条目详情（可筛选排序表格）+ 问题明细（仅翻译模式）。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QTabWidget, QFrame, QComboBox,
    QAbstractItemView, QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

if TYPE_CHECKING:
    from src.transbridge.ai_translator.translator import TranslationResult
    from src.transbridge.ai_translator.post_processor.base import PostProcessResult
    from src.transbridge.ai_translator.post_processor.polisher import PolishResult
    from src.transbridge.converter.translation_entry import TranslationEntry


class _TranslationReportDialog(QDialog):
    """AI翻译/润色结果报告对话框。

    双模式：
    - 翻译模式：传入 translate_result → 3 Tab（汇总/条目/问题）
    - 润色模式：传入 polish_stats → 2 Tab（汇总/条目）

    信号:
        entry_activated(str): 双击条目行时发射 entry_id，用于跳转Step2主表
    """

    entry_activated = pyqtSignal(str)

    def __init__(
        self,
        # 翻译模式参数
        translate_result: "TranslationResult | None" = None,
        refine_results: dict | None = None,
        polish_results: dict | None = None,
        decisions: dict | None = None,
        # 润色模式参数
        polish_entries: list["TranslationEntry"] | None = None,
        polish_results_dict: dict[str, "PolishResult"] | None = None,
        polish_stats: dict | None = None,
        # 通用
        report_path: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._translate_result = translate_result
        self._refine_results = refine_results or {}
        self._polish_mid_results = polish_results or {}
        self._decisions = decisions or {}
        self._polish_entries = polish_entries or []
        self._polish_results_dict = polish_results_dict or {}
        self._polish_stats = polish_stats or {}
        self._report_path = report_path

        self._translate_mode = translate_result is not None
        title = "翻译报告" if self._translate_mode else "润色报告"
        self.setWindowTitle(title)
        self.resize(850, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._entry_table: QTableWidget | None = None
        self._entry_filter: QComboBox | None = None
        self._entry_row_ids: dict[int, str] = {}
        self._issue_table: QTableWidget | None = None
        self._issue_filter: QComboBox | None = None

        self._init_ui()

    # ── UI 初始化 ────────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_summary_tab(), "汇总")
        self._tabs.addTab(self._build_entries_tab(), "条目详情")
        if self._translate_mode:
            self._tabs.addTab(self._build_issues_tab(), "问题明细")
        layout.addWidget(self._tabs)

        self._init_bottom_bar(layout)

    def _init_bottom_bar(self, parent_layout):
        bar = QHBoxLayout()
        self._btn_excel = QPushButton("打开 Excel")
        self._btn_excel.setEnabled(self._report_path is not None and os.path.exists(self._report_path))
        self._btn_excel.clicked.connect(self._on_open_excel)
        bar.addWidget(self._btn_excel)
        bar.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        parent_layout.addLayout(bar)

    # ── 统计卡片 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_stat_card(label: str, value: str, color: str = "#333") -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setStyleSheet("QFrame { background: white; border-radius: 4px; padding: 6px; }")
        lay = QVBoxLayout(card)
        lay.setSpacing(2)
        val_lbl = QLabel(str(value))
        val_lbl.setStyleSheet(f"font-size: 20px; font-weight: bold; color: {color};")
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl = QLabel(label)
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl.setStyleSheet("color: #666; font-size: 11px;")
        lay.addWidget(val_lbl)
        lay.addWidget(desc_lbl)
        return card

    # ── Summary Tab ──────────────────────────────────────────────────────────

    def _build_summary_tab(self):
        widget = QFrame()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        if self._translate_mode:
            r = self._translate_result
            pp = r.post_process_result

            cards1 = [
                ("总条目", r.success_count + r.failed_count + r.skipped_count, "#333"),
                ("成功", r.success_count, "#4CAF50"),
                ("失败", r.failed_count, "#F44336"),
                ("跳过", r.skipped_count, "#9E9E9E"),
                ("新增术语", r.new_dynamic_terms, "#2196F3"),
            ]
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, (label, val, color) in enumerate(cards1):
                grid.addWidget(self._make_stat_card(label, str(val), color), 0, i)

            if pp:
                err = sum(1 for x in pp.issues if x.severity == "error")
                wrn = sum(1 for x in pp.issues if x.severity == "warning")
                inf = sum(1 for x in pp.issues if x.severity == "info")
                cards2 = [
                    ("检查数", pp.total_checked, "#333"),
                    ("错误", err, "#F44336"),
                    ("警告", wrn, "#FF9800"),
                    ("信息", inf, "#9E9E9E"),
                    ("需审核", len(pp.needs_review), "#FF9800"),
                ]
                for i, (label, val, color) in enumerate(cards2):
                    grid.addWidget(self._make_stat_card(label, str(val), color), 1, i)

                passed = sum(1 for d in self._decisions.values() if getattr(d, "verdict", None) == "pass")
                rejected = sum(1 for d in self._decisions.values() if getattr(d, "verdict", None) == "reject")
                pending = sum(1 for d in self._decisions.values() if getattr(d, "verdict", None) == "pending")
                cards3 = [
                    ("通过", passed, "#4CAF50"),
                    ("打回", rejected, "#F44336"),
                    ("待审", pending, "#FF9800"),
                    ("修复数", pp.auto_fixed, "#2196F3"),
                    ("润色数", len(self._polish_mid_results), "#9C27B0"),
                ]
                for i, (label, val, color) in enumerate(cards3):
                    grid.addWidget(self._make_stat_card(label, str(val), color), 2, i)

            layout.addLayout(grid)
        else:
            s = self._polish_stats
            cards = [
                ("润色总数", s.get("total", 0), "#333"),
                ("接受", s.get("accepted", 0), "#4CAF50"),
                ("拒绝", s.get("rejected", 0), "#F44336"),
                ("失败", s.get("failed", 0), "#9E9E9E"),
                ("信心度均值", f"{s.get('avg_confidence', 0):.1%}", "#2196F3"),
            ]
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, (label, val, color) in enumerate(cards):
                grid.addWidget(self._make_stat_card(label, str(val), color), 0, i)
            layout.addLayout(grid)

        layout.addStretch()
        return widget

    # ── Entries Tab ──────────────────────────────────────────────────────────

    def _build_entries_tab(self):
        widget = QFrame()
        layout = QVBoxLayout(widget)

        # 筛选栏
        filter_bar = QHBoxLayout()
        if self._translate_mode:
            filter_bar.addWidget(QLabel("裁决结果:"))
            self._entry_filter = QComboBox()
            self._entry_filter.addItems(["全部", "通过", "打回", "待审"])
        else:
            filter_bar.addWidget(QLabel("状态:"))
            self._entry_filter = QComboBox()
            self._entry_filter.addItems(["全部", "已接受", "已拒绝"])
        self._entry_filter.currentTextChanged.connect(self._apply_entry_filter)
        filter_bar.addWidget(self._entry_filter)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # 表格
        if self._translate_mode:
            headers = ["原文", "原译文", "最终译文", "裁决结果", "信心度", "问题数"]
        else:
            headers = ["原文", "原译文", "润色结果", "接受?", "信心度", "变更摘要"]

        self._entry_table = QTableWidget()
        self._entry_table.setColumnCount(len(headers))
        self._entry_table.setHorizontalHeaderLabels(headers)
        self._entry_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._entry_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._entry_table.setSortingEnabled(True)
        self._entry_table.cellDoubleClicked.connect(self._on_entry_double_clicked)
        self._entry_table.horizontalHeader().setStretchLastSection(True)

        self._populate_entries()
        layout.addWidget(self._entry_table)
        return widget

    def _populate_entries(self):
        if self._translate_mode:
            self._populate_translate_entries()
        else:
            self._populate_polish_entries()

    def _populate_translate_entries(self):
        self._entry_row_ids.clear()
        all_ids = set(self._decisions.keys())
        all_ids.update(self._refine_results.keys())
        all_ids.update(self._polish_mid_results.keys())
        pp = self._translate_result.post_process_result
        if pp:
            all_ids.update(i.entry_id for i in pp.issues)

        self._entry_table.setRowCount(len(all_ids))
        for row, entry_id in enumerate(sorted(all_ids)):
            self._entry_row_ids[row] = entry_id
            dec = self._decisions.get(entry_id)
            ref = self._refine_results.get(entry_id)
            pol = self._polish_mid_results.get(entry_id)

            # 最终译文: 润色 > 修复 > 原文
            if pol and pol.polished_translation:
                final = pol.polished_translation
            elif ref and ref.refined_translation:
                final = ref.refined_translation
            else:
                final = ref.original_translation if ref else ""

            # 原文和原译文从 issues 或中间数据获取
            entry_issues = []
            if pp:
                entry_issues = [i for i in pp.issues if i.entry_id == entry_id]
            original = entry_issues[0].original if entry_issues else ""
            orig_trans = entry_issues[0].translation if entry_issues else (
                ref.original_translation if ref else (pol.original_translation if pol else "")
            )

            verdict_text = dec.verdict if dec else "-"
            conf = dec.confidence if dec else 0.0
            issue_count = len(entry_issues)

            self._entry_table.setItem(row, 0, self._cell(original[:80], original))
            self._entry_table.setItem(row, 1, self._cell(orig_trans[:80], orig_trans))
            self._entry_table.setItem(row, 2, self._cell(final[:80], final))
            self._entry_table.setItem(row, 3, self._cell(verdict_text))
            conf_item = QTableWidgetItem(f"{conf:.0%}")
            conf_item.setData(Qt.ItemDataRole.UserRole, float(conf))
            self._entry_table.setItem(row, 4, conf_item)
            self._entry_table.setItem(row, 5, self._cell(str(issue_count)))

    def _populate_polish_entries(self):
        self._entry_row_ids.clear()
        self._entry_table.setRowCount(len(self._polish_entries))

        for row, entry in enumerate(self._polish_entries):
            self._entry_row_ids[row] = entry.id
            pr = self._polish_results_dict.get(entry.id)
            if pr:
                changes_summary = ""
                if pr.changes:
                    parts = []
                    for c in pr.changes[:3]:
                        parts.append(f"{c.get('aspect', '')}: {c.get('before', '')} → {c.get('after', '')}")
                    changes_summary = "; ".join(parts)
                accepted = pr.confidence > 0.0
                self._entry_table.setItem(row, 0, self._cell(entry.original[:80], entry.original))
                self._entry_table.setItem(row, 1, self._cell(pr.original_translation[:80], pr.original_translation))
                self._entry_table.setItem(row, 2, self._cell(pr.polished_translation[:80], pr.polished_translation))
                self._entry_table.setItem(row, 3, self._cell("是" if accepted else "否"))
                conf_item = QTableWidgetItem(f"{pr.confidence:.0%}")
                conf_item.setData(Qt.ItemDataRole.UserRole, float(pr.confidence))
                self._entry_table.setItem(row, 4, conf_item)
                self._entry_table.setItem(row, 5, self._cell(changes_summary[:120]))
            else:
                self._entry_table.setItem(row, 0, self._cell(entry.original[:80], entry.original))
                self._entry_table.setItem(row, 1, self._cell(entry.translation[:80] if entry.translation else "", entry.translation or ""))
                self._entry_table.setItem(row, 2, self._cell(""))
                self._entry_table.setItem(row, 3, self._cell("否"))
                conf_item = QTableWidgetItem("0%")
                conf_item.setData(Qt.ItemDataRole.UserRole, 0.0)
                self._entry_table.setItem(row, 4, conf_item)
                self._entry_table.setItem(row, 5, self._cell(""))

    @staticmethod
    def _cell(text: str, tooltip: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if tooltip and tooltip != text:
            item.setToolTip(tooltip)
        return item

    def _apply_entry_filter(self, filter_value: str):
        if self._entry_table is None:
            return
        self._entry_table.setSortingEnabled(False)
        if self._translate_mode:
            verdict_map = {"通过": "pass", "打回": "reject", "待审": "pending"}
            target = verdict_map.get(filter_value)
            for row in range(self._entry_table.rowCount()):
                if target is None:
                    self._entry_table.setRowHidden(row, False)
                else:
                    item = self._entry_table.item(row, 3)
                    self._entry_table.setRowHidden(row, item.text() != filter_value if item else True)
        else:
            target = {"已接受": True, "已拒绝": False}.get(filter_value)
            for row in range(self._entry_table.rowCount()):
                if target is None:
                    self._entry_table.setRowHidden(row, False)
                else:
                    item = self._entry_table.item(row, 3)
                    is_accepted = item.text() == "是" if item else False
                    self._entry_table.setRowHidden(row, is_accepted != target)
        self._entry_table.setSortingEnabled(True)

    def _on_entry_double_clicked(self, row: int, col: int):
        entry_id = self._entry_row_ids.get(row)
        if entry_id:
            self.entry_activated.emit(entry_id)

    # ── Issues Tab ───────────────────────────────────────────────────────────

    def _build_issues_tab(self):
        widget = QFrame()
        layout = QVBoxLayout(widget)

        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("严重度:"))
        self._issue_filter = QComboBox()
        self._issue_filter.addItems(["全部", "错误", "警告", "信息"])
        self._issue_filter.currentTextChanged.connect(self._apply_issue_filter)
        filter_bar.addWidget(self._issue_filter)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        headers = ["条目ID", "问题类型", "严重度", "描述", "建议"]
        self._issue_table = QTableWidget()
        self._issue_table.setColumnCount(5)
        self._issue_table.setHorizontalHeaderLabels(headers)
        self._issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        pp = self._translate_result.post_process_result if self._translate_result else None
        issues = pp.issues if pp else []
        self._issue_table.setRowCount(len(issues))
        severity_colors = {"error": "#F44336", "warning": "#FF9800", "info": "#9E9E9E"}
        for i, issue in enumerate(issues):
            self._issue_table.setItem(i, 0, QTableWidgetItem(issue.entry_id))
            self._issue_table.setItem(i, 1, QTableWidgetItem(issue.issue_type))
            sev_item = QTableWidgetItem(issue.severity)
            sev_item.setForeground(QColor(severity_colors.get(issue.severity, "#333")))
            self._issue_table.setItem(i, 2, sev_item)
            self._issue_table.setItem(i, 3, QTableWidgetItem(issue.message))
            self._issue_table.setItem(i, 4, QTableWidgetItem(issue.suggestion))

        self._issue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._issue_table)
        return widget

    def _apply_issue_filter(self, filter_value: str):
        if self._issue_table is None:
            return
        target_map = {"错误": "error", "警告": "warning", "信息": "info"}
        target = target_map.get(filter_value)
        for row in range(self._issue_table.rowCount()):
            if target is None:
                self._issue_table.setRowHidden(row, False)
            else:
                item = self._issue_table.item(row, 2)
                self._issue_table.setRowHidden(row, item.text() != filter_value if item else True)

    # ── 按钮动作 ─────────────────────────────────────────────────────────────

    def _on_open_excel(self):
        if self._report_path and os.path.exists(self._report_path):
            os.startfile(self._report_path)
