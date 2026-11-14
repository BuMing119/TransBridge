# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Explicit whole-collection exports, independent of visible pages and filters."""

from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton

from transbridge.application.terminology.browse_queries import TermBrowseQuery
from transbridge.application.terminology.effective import EffectiveSnapshotStatus
from transbridge.application.terminology.query_control import QueryCancellation
from transbridge.infra.terminology_export import TerminologyExport, conflict_export, write_terminology_export
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle

from .async_actions import AsyncActionRunner


class TerminologyExportController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._busy = False
        self._cancellation = QueryCancellation()
        self._runner = AsyncActionRunner(self, on_error=self._failed)
        self._buttons = []
        terms = window.terms_view
        self.status = QLabel(terms.editor_dialog)
        self.status.setWordWrap(True)
        terms.editor_dialog.content.insertWidget(0, self.status)
        self.conflict_status = QLabel(terms.conflict_dialog)
        self.conflict_status.setWordWrap(True)
        self.conflict_status.hide()
        terms.conflict_dialog.content.insertWidget(1, self.conflict_status)
        self.summary_button = self._button("单独导出主表（CSV/JSON）…", "conflict_summary", terms.conflict_dialog)
        self.conflict_button = self._button("单独导出证据（CSV/JSON）…", "conflicts", terms.conflict_dialog)
        self.workbook_button = self._button(
            "导出 Excel 工作簿（主表＋证据子表）…", "conflict_workbook", terms.conflict_dialog
        )
        more = QPushButton("导出…", terms.conflict_dialog)
        ComponentStyle.apply_static(more, ComponentKind.BUTTON)
        menu = QMenu(more)
        for button in (self.workbook_button, self.summary_button, self.conflict_button):
            button.hide()
            action = menu.addAction(button.text())
            action.triggered.connect(button.click)
        more.setMenu(menu)
        window.conflicts_view.toolbar.addWidget(more)
        self.workbook_button.setToolTip("一个 .xlsx 文件，左下角切换冲突主表、少数译名证据和全部证据。")
        self.terms_button = self._button("导出完整术语…", "terms", terms.editor_dialog)
        terms.editor_dialog.content.insertWidget(0, self.terms_button)
        self.main_button = self._button("导出完整术语…", "terms", terms)
        exports = QHBoxLayout()
        exports.addWidget(self.main_button)
        exports.addStretch(1)
        terms.layout().addLayout(exports)
        self.terms_button.setToolTip("导出当前方案全部已生效术语，不受分页和搜索限制；待应用结果请先应用。")
        self.main_button.setToolTip(self.terms_button.toolTip())
        self.summary_button.setToolTip(
            "每个冲突一行，包含各译名去重词条数与少数译名代表证据；全部原文译文另导出证据明细。"
        )
        self.conflict_button.setToolTip("导出本次提取的全部冲突、候选译名及来源上下文，不受分页和搜索限制。")
        window.closed.connect(self._close)
        window.context_invalidated.connect(self._close)

    def _close(self):
        self._cancellation.cancel()
        self._runner.close()

    def _button(self, text, kind, parent):
        button = QPushButton(text, parent)
        button.setAutoDefault(False)
        ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        button.clicked.connect(lambda: self.start(kind))
        self._buttons.append(button)
        return button

    def start(self, kind):
        if self._busy or not self.window.context_is_current:
            return
        if kind != "terms" and (self.window._draft_writes.pending_count or self.window._workflow.task_busy):
            self._failed(ValueError("请等待术语任务和处理决定保存完成后，再导出冲突清单。"))
            return
        context = self.window.presenter.context
        if context.project_id is None or context.variant_id is None:
            self._failed(ValueError("请先打开项目和翻译版本。"))
            return
        self._set_busy(True)
        self._set_status("正在读取完整数据，导出不受当前分页和筛选限制…")
        profile = self._profile_identity()
        services = self.window.presenter.services

        def read(token):
            token.raise_if_cancelled()
            if kind == "terms":
                if services.browsing is None:
                    raise ValueError("当前环境无法读取生效术语。")
                snapshot = services.browsing.describe(
                    TermBrowseQuery(context.project_id, context.variant_id), cancellation=token
                ).effective_snapshot
                if snapshot.status is not EffectiveSnapshotStatus.READY:
                    raise ValueError("尚无可导出的已生效术语，请先应用提取结果或选择已发布方案。")
                return TerminologyExport("terms", tuple(item for item in snapshot.decisions if item.is_effective))
            commands = services.commands
            build = commands.latest_build_result(context.project_id, context.variant_id)
            if build is None:
                raise ValueError("尚无提取结果，请先提取术语。")
            draft = commands.active_draft(context)
            version = (
                None
                if draft is not None
                else getattr(commands, "latest_version", lambda *_args: None)(context.project_id, context.variant_id)
            )
            token.raise_if_cancelled()
            current = commands.latest_build_result(context.project_id, context.variant_id)
            if current is None or current.ref != build.ref:
                raise ValueError("提取结果正在变化，请等待任务完成后重新导出。")
            return replace(conflict_export(build, draft, version), kind=kind)

        self._runner.run_read(read, lambda snapshot: self._choose_path(snapshot, profile))

    def _profile_identity(self):
        profiles = self.window._profile_controller
        if profiles is None:
            return None
        state = profiles.state
        return state.selected_profile_id, getattr(state, "selected_revision", None)

    def _choose_path(self, snapshot, profile):
        if not self.window.context_is_current or profile != self._profile_identity():
            self._failed(ValueError("当前项目或方案已变化，请重新导出。"))
            return
        if not snapshot.items:
            self._failed(ValueError("当前没有可导出的内容。"))
            return
        conflicts = snapshot.kind != "terms"
        summary = snapshot.kind == "conflict_summary"
        workbook = snapshot.kind == "conflict_workbook"
        parent = self.window.terms_view.conflict_dialog if conflicts else self.window
        name, selected = QFileDialog.getSaveFileName(
            parent,
            "导出冲突工作簿（含子表）"
            if workbook
            else "导出冲突审核主表"
            if summary
            else "导出全部冲突（含来源证据）"
            if conflicts
            else "导出全部已生效术语",
            "术语冲突.xlsx"
            if workbook
            else "术语冲突主表.csv"
            if summary
            else "术语冲突证据.csv"
            if conflicts
            else "项目术语.json",
            "Excel 工作簿 (*.xlsx)"
            if workbook
            else "CSV (*.csv);;JSON (*.json)"
            if conflicts
            else "JSON (*.json);;CSV (*.csv)",
        )
        if not name:
            self._set_busy(False)
            self._set_status("已取消导出。")
            return
        path = Path(name)
        if not path.suffix:
            path = path.with_suffix(".xlsx" if workbook else ".csv" if selected.startswith("CSV") else ".json")
            if (
                path.exists()
                and QMessageBox.question(
                    parent,
                    "替换已有文件",
                    f"{path} 已存在，是否替换？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                self._set_busy(False)
                self._set_status("已取消导出。")
                return
        if not self.window.context_is_current or profile != self._profile_identity():
            self._failed(ValueError("当前项目或方案已变化，请重新导出。"))
            return
        self._set_status("正在写入完整文件…")
        self._runner.run(
            lambda: write_terminology_export(path, snapshot, cancellation=self._cancellation),
            lambda count: self._saved(path, count),
        )

    def _saved(self, path, count):
        self._set_busy(False)
        self._set_status(f"已完整导出 {count:,} 项：{path}")

    def _failed(self, error):
        self._set_busy(False)
        self._set_status(f"导出失败：{error}")

    def _set_busy(self, busy):
        self._busy = busy
        for button in self._buttons:
            button.setEnabled(not busy)

    def _set_status(self, text):
        self.status.setText(text)
        self.conflict_status.setText(text)
        self.conflict_status.setVisible(bool(text))
        self.window.operation_notice.show_message(text)
