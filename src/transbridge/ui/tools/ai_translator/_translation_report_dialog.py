"""AI翻译/润色结果报告对话框。

多Tab结构：汇总（统计卡片）+ 条目详情（可筛选排序表格）+ 问题明细（仅翻译模式）。
"""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)

from transbridge.application.translation import ReportSnapshot
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from ._theme_support import AiThemeBinding
from .reporting import TranslationReportArtifacts, diagnostic_entry_id, report_overview


class _TranslationReportDialog(QDialog):
    """AI翻译/润色结果报告对话框。

    翻译与润色模式均只消费 canonical ``ReportSnapshot``。

    信号:
        entry_activated(str): 双击条目行时发射 entry_id，用于跳转Step2主表
    """

    entry_activated = pyqtSignal(str)

    def __init__(
        self,
        snapshot: ReportSnapshot | None = None,
        *,
        report_path: str | None = None,
        report_pending: bool = False,
        parent=None,
        theme_view: ThemeView | None = None,
    ):
        super().__init__(parent)
        self._snapshot = snapshot
        self._report_path = report_path
        self._report_paths = (report_path,) if report_path else ()
        self._report_pending = report_pending
        self._stat_labels: list[QLabel] = []

        source = snapshot.run_spec_summary.get("source") if snapshot is not None else None
        self._mixed_mode = source == "mixed"
        self._translate_mode = source != "polish"
        title = "混合运行报告" if self._mixed_mode else ("翻译报告" if self._translate_mode else "润色报告")
        self.setWindowTitle(title)
        self.resize(850, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._entry_table: QTableWidget | None = None
        self._entry_filter: QComboBox | None = None
        self._entry_row_ids: dict[int, str] = {}
        self._issue_table: QTableWidget | None = None
        self._issue_filter: QComboBox | None = None

        self._init_ui()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)

    # ── UI 初始化 ────────────────────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_summary_tab(), "汇总")
        self._tabs.addTab(self._build_entries_tab(), "条目详情")
        self._tabs.addTab(self._build_issues_tab(), "问题明细")
        layout.addWidget(self._tabs)

        self._init_bottom_bar(layout)

    def _init_bottom_bar(self, parent_layout):
        bar = QHBoxLayout()
        self._report_status = QLabel()
        self._report_status.setWordWrap(True)
        if self._report_pending:
            self._set_report_status("正在后台生成 JSON、CSV 和 Excel 报告…", "info")
        elif self._report_path:
            self._set_report_status("报告文件已生成。", "success")
        else:
            self._set_report_status("当前没有可打开的 Excel 报告。", "warning")
        bar.addWidget(self._report_status, 1)
        self._btn_excel = QPushButton("打开 Excel")
        self._btn_excel.setEnabled(self._report_path is not None and os.path.exists(self._report_path))
        self._btn_excel.clicked.connect(self._on_open_excel)
        bar.addWidget(self._btn_excel)
        bar.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        bar.addWidget(btn_close)
        parent_layout.addLayout(bar)

    def set_report_render_result(self, artifacts: TranslationReportArtifacts) -> None:
        """Expose background renderer paths and diagnostics without losing partial output."""
        self._report_pending = False
        self._report_paths = artifacts.paths
        self._report_path = artifacts.excel_path
        self._btn_excel.setEnabled(bool(self._report_path and os.path.exists(self._report_path)))
        if artifacts.diagnostics:
            produced = f"已成功生成 {len(artifacts.paths)} 个文件；" if artifacts.paths else ""
            self._set_report_status(
                f"{produced}部分报告生成失败：{'；'.join(artifacts.diagnostics)}",
                "warning" if artifacts.paths else "error",
            )
        elif artifacts.paths:
            self._set_report_status(f"报告生成完成，共 {len(artifacts.paths)} 个文件。", "success")
        else:
            self._set_report_status("报告生成结束，但没有产生文件。", "error")

    def set_report_render_error(self, message: str) -> None:
        """Display a fatal worker error while leaving the in-memory snapshot usable."""
        self._report_pending = False
        self._set_report_status(f"报告文件生成失败：{message}", "error")

    def _set_report_status(self, message: str, state: str) -> None:
        self._report_status.setText(message)
        self._report_status.setAccessibleName("报告生成状态")
        self._report_status.setAccessibleDescription(message)
        self._report_status.setProperty("aiReportState", state)
        binding = getattr(self, "_theme_binding", None)
        if binding is not None:
            brush = binding.report(state)
            if brush is not None:
                palette = self._report_status.palette()
                palette.setColor(self._report_status.foregroundRole(), brush.foreground.color())
                self._report_status.setPalette(palette)

    # ── 统计卡片 ─────────────────────────────────────────────────────────────

    def _make_stat_card(self, label: str, value: str, state: str = "info") -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        ComponentStyle.apply_static(card, ComponentKind.CARD)
        lay = QVBoxLayout(card)
        lay.setSpacing(2)
        val_lbl = QLabel(str(value))
        value_font = val_lbl.font()
        value_font.setPointSize(15)
        value_font.setBold(True)
        val_lbl.setFont(value_font)
        val_lbl.setProperty("aiReportState", state)
        val_lbl.setAccessibleName(f"{label}：{value}")
        self._stat_labels.append(val_lbl)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_lbl = QLabel(label)
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(val_lbl)
        lay.addWidget(desc_lbl)
        return card

    # ── Summary Tab ──────────────────────────────────────────────────────────

    def _build_summary_tab(self):
        widget = QFrame()
        layout = QVBoxLayout(widget)
        layout.setSpacing(12)

        if self._mixed_mode:
            snapshot = self._snapshot
            summary = snapshot.run_spec_summary if snapshot is not None else {}
            translation = summary.get("translation") or {}
            polish = summary.get("polish") or {}
            translation_counts = translation.get("translation_counts", {})
            polish_counts = polish.get("polish_counts", {})
            cards = [
                ("总条目", snapshot.input_count if snapshot is not None else 0, "info"),
                ("翻译成功", translation_counts.get("succeeded", 0), "success"),
                ("翻译失败", translation_counts.get("failed", 0), "error"),
                ("校对接受", polish_counts.get("accepted", 0), "success"),
                ("校对失败", polish_counts.get("failed", 0), "error"),
            ]
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, (label, val, color) in enumerate(cards):
                grid.addWidget(self._make_stat_card(label, str(val), color), 0, i)
            if snapshot is not None:
                overview = report_overview(snapshot)
                details = [
                    ("已接受", snapshot.accepted_count, "success"),
                    ("需审核", overview.needs_review, "warning"),
                    ("问题", snapshot.issue_count, "warning"),
                    ("最终失败", snapshot.failure_count, "error"),
                    ("终态", snapshot.outcome.value, "info"),
                ]
                for i, (label, val, color) in enumerate(details):
                    grid.addWidget(self._make_stat_card(label, str(val), color), 1, i)
            layout.addLayout(grid)
        elif self._translate_mode:
            snapshot = self._snapshot
            summary = snapshot.run_spec_summary if snapshot is not None else {}
            counts = summary.get("translation_counts", {})

            cards1 = [
                ("总条目", sum(int(counts.get(key, 0)) for key in ("succeeded", "failed", "skipped")), "info"),
                ("成功", counts.get("succeeded", 0), "success"),
                ("失败", counts.get("failed", 0), "error"),
                ("跳过", counts.get("skipped", 0), "info"),
                ("新增术语", counts.get("new_dynamic_terms", 0), "info"),
            ]
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, (label, val, color) in enumerate(cards1):
                grid.addWidget(self._make_stat_card(label, str(val), color), 0, i)

            if snapshot:
                overview = report_overview(snapshot)
                cards2 = [
                    ("报告条目", snapshot.input_count, "info"),
                    ("错误", overview.errors, "error"),
                    ("警告", overview.warnings, "warning"),
                    ("已变更", overview.changed, "info"),
                    ("需审核", overview.needs_review, "warning"),
                ]
                for i, (label, val, color) in enumerate(cards2):
                    grid.addWidget(self._make_stat_card(label, str(val), color), 1, i)

                cards3 = [
                    ("已接受", snapshot.accepted_count, "success"),
                    ("未接受", overview.needs_review, "warning"),
                    ("阶段数", len(snapshot.stage_outcomes), "info"),
                    ("报告失败", snapshot.failure_count, "error"),
                    ("终态", snapshot.outcome.value, "info"),
                ]
                for i, (label, val, color) in enumerate(cards3):
                    grid.addWidget(self._make_stat_card(label, str(val), color), 2, i)

            layout.addLayout(grid)
        else:
            snapshot = self._snapshot
            summary = snapshot.run_spec_summary if snapshot is not None else {}
            counts = summary.get("polish_counts", {})
            cards = [
                ("润色总数", snapshot.input_count if snapshot is not None else 0, "info"),
                ("接受", counts.get("accepted", 0), "success"),
                ("拒绝", counts.get("rejected", 0), "warning"),
                ("失败", counts.get("failed", 0), "error"),
                ("信心度均值", f"{float(summary.get('avg_confidence', 0)):.1%}", "info"),
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
            filter_bar.addWidget(QLabel("报告状态:"))
            self._entry_filter = QComboBox()
            self._entry_filter.addItems(["全部", "已接受", "需审核"])
        else:
            filter_bar.addWidget(QLabel("状态:"))
            self._entry_filter = QComboBox()
            self._entry_filter.addItems(["全部", "已接受", "已拒绝", "失败"])
        self._entry_filter.currentTextChanged.connect(self._apply_entry_filter)
        filter_bar.addWidget(self._entry_filter)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        # 表格
        if self._translate_mode:
            headers = ["原文", "处理前译文", "最终译文", "状态", "阶段", "问题数"]
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
        snapshot = self._snapshot
        candidates = sorted(
            snapshot.candidates if snapshot is not None else (),
            key=lambda item: item.entry_key.serialize(),
        )
        issue_counts: dict[str, int] = {}
        if snapshot is not None:
            for diagnostic in snapshot.diagnostics:
                entry_id = diagnostic_entry_id(diagnostic.details)
                if entry_id:
                    issue_counts[entry_id] = issue_counts.get(entry_id, 0) + 1

        self._entry_table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            entry_id = candidate.entry_key.local_key
            self._entry_row_ids[row] = entry_id
            status = "已接受" if candidate.accepted else "需审核"
            phases = " → ".join(candidate.phases) if candidate.phases else "翻译"

            self._entry_table.setItem(row, 0, self._cell(candidate.original[:80], candidate.original))
            self._entry_table.setItem(row, 1, self._cell(candidate.before_text[:80], candidate.before_text))
            self._entry_table.setItem(row, 2, self._cell(candidate.text[:80], candidate.text))
            self._entry_table.setItem(row, 3, self._cell(status))
            self._entry_table.setItem(row, 4, self._cell(phases))
            self._entry_table.setItem(row, 5, self._cell(str(issue_counts.get(entry_id, 0))))

    def _populate_polish_entries(self):
        self._entry_row_ids.clear()
        candidates = sorted(
            self._snapshot.candidates if self._snapshot is not None else (),
            key=lambda item: item.entry_key.serialize(),
        )
        self._entry_table.setRowCount(len(candidates))

        status_labels = {"accepted": "已接受", "rejected": "已拒绝", "failed": "失败"}
        for row, candidate in enumerate(candidates):
            self._entry_row_ids[row] = candidate.entry_key.local_key
            details = dict(getattr(candidate, "report_details", {}))
            status = status_labels.get(str(details.get("result_status", "")), "已拒绝")
            confidence = float(details.get("confidence", 0.0))
            changes_summary = _format_changes(details.get("changes", ()))
            self._entry_table.setItem(row, 0, self._cell(candidate.original[:80], candidate.original))
            self._entry_table.setItem(row, 1, self._cell(candidate.before_text[:80], candidate.before_text))
            self._entry_table.setItem(row, 2, self._cell(candidate.text[:80], candidate.text))
            self._entry_table.setItem(row, 3, self._cell(status))
            conf_item = QTableWidgetItem(f"{confidence:.0%}")
            conf_item.setData(Qt.ItemDataRole.UserRole, confidence)
            self._entry_table.setItem(row, 4, conf_item)
            self._entry_table.setItem(row, 5, self._cell(changes_summary[:120], changes_summary))

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
            target = filter_value if filter_value != "全部" else None
            for row in range(self._entry_table.rowCount()):
                if target is None:
                    self._entry_table.setRowHidden(row, False)
                else:
                    item = self._entry_table.item(row, 3)
                    self._entry_table.setRowHidden(row, item.text() != filter_value if item else True)
        else:
            target = filter_value if filter_value != "全部" else None
            for row in range(self._entry_table.rowCount()):
                if target is None:
                    self._entry_table.setRowHidden(row, False)
                else:
                    item = self._entry_table.item(row, 3)
                    self._entry_table.setRowHidden(row, item.text() != target if item else True)
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

        headers = ["条目ID", "诊断代码", "严重度", "描述", "分类"]
        self._issue_table = QTableWidget()
        self._issue_table.setColumnCount(5)
        self._issue_table.setHorizontalHeaderLabels(headers)
        self._issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        diagnostics = self._snapshot.diagnostics if self._snapshot is not None else ()
        self._issue_table.setRowCount(len(diagnostics))
        for i, diagnostic in enumerate(diagnostics):
            severity = diagnostic.severity.value
            category = diagnostic.category.value if diagnostic.category else ""
            self._issue_table.setItem(i, 0, QTableWidgetItem(diagnostic_entry_id(diagnostic.details)))
            self._issue_table.setItem(i, 1, QTableWidgetItem(diagnostic.code))
            sev_item = QTableWidgetItem(severity)
            sev_item.setData(Qt.ItemDataRole.UserRole, severity)
            self._issue_table.setItem(i, 2, sev_item)
            self._issue_table.setItem(i, 3, QTableWidgetItem(diagnostic.message))
            self._issue_table.setItem(i, 4, QTableWidgetItem(category))

        self._issue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._issue_table)
        return widget

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        report_status = getattr(self, "_report_status", None)
        if report_status is not None:
            brush = binding.report(str(report_status.property("aiReportState") or "info"))
            if brush is not None:
                palette = report_status.palette()
                palette.setColor(report_status.foregroundRole(), brush.foreground.color())
                report_status.setPalette(palette)
        for label in self._stat_labels:
            brush = binding.report(str(label.property("aiReportState") or "info"))
            if brush is not None:
                palette = label.palette()
                palette.setColor(label.foregroundRole(), brush.foreground.color())
                label.setPalette(palette)
        if self._issue_table is not None and binding.domain is not None:
            for row in range(self._issue_table.rowCount()):
                item = self._issue_table.item(row, 2)
                if item is not None:
                    severity = str(item.data(Qt.ItemDataRole.UserRole) or "info")
                    item.setForeground(binding.domain.report(severity).foreground)
        for table in (self._entry_table, self._issue_table):
            if table is not None:
                table.viewport().update()

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision

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
                severity = item.data(Qt.ItemDataRole.UserRole) if item else None
                self._issue_table.setRowHidden(row, severity != target)

    # ── 按钮动作 ─────────────────────────────────────────────────────────────

    def _on_open_excel(self):
        if self._report_path and os.path.exists(self._report_path):
            os.startfile(self._report_path)


def _format_changes(changes: object) -> str:
    if not isinstance(changes, (list, tuple)):
        return str(changes or "")
    parts: list[str] = []
    for change in changes[:3]:
        if isinstance(change, dict):
            aspect = str(change.get("aspect", "")).strip()
            before = str(change.get("before", "")).strip()
            after = str(change.get("after", "")).strip()
            detail = f"{before} → {after}" if before or after else str(change.get("description", ""))
            parts.append(f"{aspect}: {detail}" if aspect else detail)
        else:
            parts.append(str(change))
    return "; ".join(part for part in parts if part)
