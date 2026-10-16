"""Inbound review editing, preview comparisons, and guarded draft submission."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.application.terminology_sync.inbound import InboundReviewDecision

from .inbound_review import InboundReviewDrafts, InboundReviewEdit
from .sync_copy import INBOUND_LABELS, REASON_LABELS, content_text, label
from .sync_details import SyncDetailsTable


class SyncInboundReviewPanel(QFrame):
    draft_committed = pyqtSignal(object)

    def __init__(self, presenter, run, failed, parent=None) -> None:
        super().__init__(parent)
        self.presenter, self._run, self._failed = presenter, run, failed
        self._drafts = InboundReviewDrafts()
        self._rendered_id = None
        self._rendered_items = ()
        self._committed_ref = None
        self._shown_projection = None
        self.setProperty("tbTerminologyCard", True)
        layout = QVBoxLayout(self)
        self.notice = QLabel(self)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.selector = QComboBox(self)
        self.selector.setAccessibleName("待复核远端变化集")
        layout.addWidget(self.selector)
        self.table = QTableWidget(0, 7, self)
        self.table.setAccessibleName("当前页远端术语变化")
        self.table.setHorizontalHeaderLabels((
            "原文",
            "远端译文",
            "复核决定",
            "编辑译文",
            "本地现有内容",
            "远端变化",
            "说明",
        ))
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table)
        controls = QHBoxLayout()
        self.previous_button = QPushButton("上一页", self)
        self.next_button = QPushButton("下一页", self)
        self.page_label = QLabel(self)
        self.preview_button = QPushButton("预览草稿变更", self)
        self.commit_button = QPushButton("提交到待发布草稿", self)
        self.commit_button.setEnabled(False)
        for widget in (
            self.previous_button,
            self.next_button,
            self.page_label,
            self.preview_button,
            self.commit_button,
        ):
            controls.addWidget(widget)
        layout.addLayout(controls)
        self.preview_details = SyncDetailsTable(
            "拟写入草稿的逐项影响",
            ("草稿操作", "变更前", "变更后", "阻断原因 / 后续操作"),
            self,
            locate=True,
        )
        layout.addWidget(self.preview_details)
        self.diagnostics = QPlainTextEdit(self)
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setAccessibleName("入站预览诊断")
        self.diagnostics.setMaximumHeight(100)
        layout.addWidget(self.diagnostics)
        self.blocked_button = QPushButton("定位首个阻断项", self)
        layout.addWidget(self.blocked_button)
        self.selector.currentIndexChanged.connect(self._select)
        self.previous_button.clicked.connect(lambda: self.page(-50))
        self.next_button.clicked.connect(lambda: self.page(50))
        self.preview_button.clicked.connect(self.preview)
        self.commit_button.clicked.connect(lambda: self._run("commit-inbound", self.presenter.commit_inbound))
        self.preview_details.item_requested.connect(self.locate)
        self.blocked_button.clicked.connect(self._locate_blocked)
        self.hide()

    def render(self, state) -> None:
        self.setVisible(bool(state.inbound_sets))
        if not state.inbound_sets:
            return
        selected = state.selected_inbound_id
        ids = [self.selector.itemData(index) for index in range(self.selector.count())]
        if ids != [item.change_set_id for item in state.inbound_sets]:
            self.selector.blockSignals(True)
            self.selector.clear()
            for item in state.inbound_sets:
                self.selector.addItem(f"{item.created_at:%Y-%m-%d %H:%M} · {len(item.items)} 项", item.change_set_id)
            self.selector.blockSignals(False)
        self.selector.blockSignals(True)
        self.selector.setCurrentIndex(self.selector.findData(selected))
        self.selector.blockSignals(False)
        projection = state.inbound_projection
        choices = {} if projection is None else projection.choices
        self._rendered_id, self._rendered_items = selected, state.inbound_items
        self.table.setRowCount(len(state.inbound_items))
        for row, item in enumerate(state.inbound_items):
            content = item.remote or item.local
            values = {
                0: "" if content is None else content.original,
                1: "（已删除）" if item.remote is None else item.remote.translation,
                4: content_text(item.local),
                5: label(getattr(item, "kind", ""), INBOUND_LABELS),
                6: label(getattr(item, "reason", ""), REASON_LABELS),
            }
            for column, value in values.items():
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cell.setToolTip(content_text(item.remote) if column == 1 else value)
                self.table.setItem(row, column, cell)
            edit = self._drafts.row(selected, item, choices.get(item.item_id))
            choice = QComboBox(self.table)
            for title, decision in (("接受", "accept"), ("拒绝", "reject"), ("编辑", "edit")):
                if decision != "edit" or content is not None:
                    choice.addItem(title, decision)
            choice.setProperty("item_id", item.item_id)
            choice.setCurrentIndex(choice.findData(edit.decision.value))
            self.table.setCellWidget(row, 2, choice)
            edited = QLineEdit(edit.translation, self.table)
            edited.setEnabled(edit.decision is InboundReviewDecision.EDIT)
            self.table.setCellWidget(row, 3, edited)
            choice.currentIndexChanged.connect(self._edited)
            edited.textChanged.connect(self._edited)
        start = state.inbound_offset + 1 if state.inbound_total else 0
        end = state.inbound_offset + len(state.inbound_items)
        self.page_label.setText(f"{start}-{end} / {state.inbound_total}")
        self.previous_button.setEnabled(not state.busy and state.inbound_offset > 0)
        self.next_button.setEnabled(not state.busy and end < state.inbound_total)
        self.preview_button.setEnabled(bool(state.inbound_items) and not state.busy)
        self.commit_button.setEnabled(
            not state.busy and projection is not None and projection.committable and state.inbound_commit is None
        )
        self.selector.setEnabled(not state.busy)
        self.table.setEnabled(not state.busy)
        self._render_proposal(state)

    def _render_proposal(self, state) -> None:
        proposal = state.inbound_proposal
        projection = state.inbound_projection
        self.preview_details.setVisible(proposal is not None)
        self.preview_details.set_projection(projection, busy=state.busy)
        diagnostics = "" if projection is None else projection.diagnostics
        if self._shown_projection is not projection:
            self.diagnostics.setPlainText(diagnostics)
            self._shown_projection = projection
        self.diagnostics.setVisible(bool(diagnostics))
        self.blocked_button.setVisible(projection is not None and not projection.committable)
        self.blocked_button.setEnabled(not state.busy)
        if state.inbound_commit is not None:
            ref = getattr(state.inbound_commit, "draft_ref", None)
            self.notice.setText(
                "已写入待发布草稿；请前往草稿复核并发布新术语版本后用于翻译。"
                if ref is not None
                else "复核决定已保存；未修改待发布草稿。"
            )
            if ref is not None and ref != self._committed_ref:
                self._committed_ref = ref
                self.draft_committed.emit(ref)
        elif proposal is not None:
            counts = projection.counts
            self.notice.setText(
                (
                    "预览存在阻断项：请定位并编辑或拒绝，再重新预览。"
                    if not projection.committable
                    else "请检查下方变更后提交到待发布草稿；提交后仍待复核与发布。"
                )
                + counts
            )
        else:
            self.notice.setText(state.inbound_notice)

    def set_busy(self, busy: bool) -> None:
        self.selector.setEnabled(not busy)
        self.table.setEnabled(not busy)
        if busy:
            for button in (
                self.previous_button,
                self.next_button,
                self.preview_button,
                self.commit_button,
                self.blocked_button,
                self.preview_details.locate_button,
            ):
                button.setEnabled(False)

    def _select(self, index: int) -> None:
        identity = self.selector.itemData(index)
        if isinstance(identity, str):
            self.remember_page()
            self.render(self.presenter.select_inbound(identity))

    def page(self, delta: int) -> None:
        self.remember_page()
        self.render(self.presenter.page_inbound(self.presenter.state.inbound_offset + delta))

    def remember_page(self) -> None:
        if self._rendered_id is None:
            return
        edits = []
        for row, item in enumerate(self._rendered_items):
            choice, edited = self.table.cellWidget(row, 2), self.table.cellWidget(row, 3)
            if not isinstance(choice, QComboBox) or not isinstance(edited, QLineEdit):
                continue
            decision = InboundReviewDecision(str(choice.currentData()))
            edited.setEnabled(decision is InboundReviewDecision.EDIT)
            edits.append(InboundReviewEdit(item, decision, edited.text()))
        self._drafts.remember(self._rendered_id, tuple(edits))

    def _edited(self, *_args) -> None:
        self.remember_page()
        self.presenter.invalidate_inbound_preview()
        self.commit_button.setEnabled(False)
        self.preview_details.hide()
        self.diagnostics.hide()
        self.blocked_button.hide()
        self.notice.setText("复核内容已修改，请重新预览草稿变更后提交。")

    def preview(self) -> None:
        self.remember_page()
        self.presenter.invalidate_inbound_preview()
        self.commit_button.setEnabled(False)
        edits = self._drafts.snapshot(self.presenter.state.selected_inbound_id)
        self._run("preview-inbound", self.presenter.inbound_preview_request(edits))

    def locate(self, item_id: str) -> None:
        self.remember_page()
        state = self.presenter.state
        selected = next((item for item in state.inbound_sets if item.change_set_id == state.selected_inbound_id), None)
        if selected is None:
            return
        index = next((index for index, item in enumerate(selected.items) if item.item_id == item_id), None)
        if index is None:
            return
        self.render(self.presenter.page_inbound(index // 50 * 50))
        self.table.selectRow(index % 50)
        widget = self.table.cellWidget(index % 50, 2)
        if widget is not None:
            widget.setFocus()

    def _locate_blocked(self) -> None:
        projection = self.presenter.state.inbound_projection
        if projection is not None and projection.first_blocked is not None:
            self.locate(projection.first_blocked)
