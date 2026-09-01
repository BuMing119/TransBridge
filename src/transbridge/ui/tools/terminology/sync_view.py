"""Thin view contract for mounting terminology sync without window business logic."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.contracts import JobRef
from transbridge.application.tasks.activity import TaskActivityViewState
from transbridge.application.terminology_sync.inbound import InboundReviewDecision
from transbridge.application.terminology_sync.models import TerminologySyncMode

from .inbound_review import InboundReviewDrafts, InboundReviewEdit
from .sync_presenter import TerminologySyncPresenter, TerminologySyncViewState


@dataclass(frozen=True, slots=True)
class TerminologySyncActionDescriptor:
    mode: TerminologySyncMode
    label: str
    accessible_name: str
    description: str


SYNC_ACTIONS = (
    TerminologySyncActionDescriptor(
        TerminologySyncMode.BACKUP,
        "备份已发布版本",
        "备份当前项目术语已发布版本到 ParaTranz",
        "仅把当前已发布术语版本备份到已验证的 ParaTranz 目标。",
    ),
    TerminologySyncActionDescriptor(
        TerminologySyncMode.BIDIRECTIONAL,
        "双向同步",
        "双向同步当前项目术语与 ParaTranz",
        "同时生成远端写入计划和待复核的入站变化；入站变化不会自动发布。",
    ),
)


class TerminologySyncView(Protocol):
    """UI host implements this bounded signal/render seam."""

    def render_sync(self, state: TerminologySyncViewState) -> None: ...


class _SyncSignals(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(object)


class _SyncCall(QRunnable):
    def __init__(self, call, signals: _SyncSignals) -> None:
        super().__init__()
        self._call = call
        self._signals = signals

    def run(self) -> None:
        try:
            self._signals.completed.emit(self._call())
        except Exception as exc:  # noqa: BLE001 - worker boundary projects a safe message
            self._signals.failed.emit(exc)


class TerminologySyncPanel(QFrame):
    """Mounted workbench card; application presenter remains the only workflow owner."""

    def __init__(self, presenter: TerminologySyncPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.presenter = presenter
        self.setProperty("tbTerminologySoftCard", True)
        self.setAccessibleName("ParaTranz 术语备份与双向同步")
        self._pool = QThreadPool.globalInstance()
        self._signals = _SyncSignals(self)
        self._signals.completed.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self._signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._pending = ""
        self._review_drafts = InboundReviewDrafts()
        self._rendered_inbound_id: str | None = None
        self._rendered_inbound_items = ()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        title = QLabel("ParaTranz 备份与双向同步", self)
        title.setProperty("tbTerminologySectionTitle", True)
        self.summary = QLabel("选择一个明确动作后，将先生成可分页计划，不会直接写入远端。", self)
        self.summary.setWordWrap(True)
        self.summary.setProperty("tbSecondary", True)
        controls = QHBoxLayout()
        self.backup_button = QPushButton("备份已发布版本…", self)
        self.backup_button.setAccessibleName(SYNC_ACTIONS[0].accessible_name)
        self.bidirectional_button = QPushButton("双向同步…", self)
        self.bidirectional_button.setAccessibleName(SYNC_ACTIONS[1].accessible_name)
        self.activate_button = QPushButton("启用此 Project/Variant 映射", self)
        self.activate_button.setVisible(False)
        self.confirm_button = QPushButton("确认远端影响", self)
        self.confirm_button.setVisible(False)
        self.execute_button = QPushButton("执行已确认计划", self)
        self.execute_button.setVisible(False)
        self.inbound_button = QPushButton("复核远端变化", self)
        self.retry_button = QPushButton("重试失败项", self)
        self.retry_button.setAccessibleName("重试已确定失败的术语同步项")
        self.retry_button.setVisible(False)
        self.reconcile_button = QPushButton("核对未知远端结果", self)
        self.reconcile_button.setAccessibleName("核对术语同步中未知的远端结果")
        self.reconcile_button.setVisible(False)
        for button in (
            self.backup_button,
            self.bidirectional_button,
            self.activate_button,
            self.confirm_button,
            self.execute_button,
            self.inbound_button,
            self.retry_button,
            self.reconcile_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(self.summary)
        layout.addLayout(controls)
        self._init_inbound(layout)

        self.backup_button.clicked.connect(lambda: self._plan(TerminologySyncMode.BACKUP))
        self.bidirectional_button.clicked.connect(lambda: self._plan(TerminologySyncMode.BIDIRECTIONAL))
        self.activate_button.clicked.connect(self._activate)
        self.confirm_button.clicked.connect(self._confirm)
        self.execute_button.clicked.connect(self._execute)
        self.inbound_button.clicked.connect(self._inbound)
        self.retry_button.clicked.connect(self._retry)
        self.reconcile_button.clicked.connect(self._reconcile)

    def render_sync(self, state: TerminologySyncViewState) -> None:
        self._set_busy(state.busy)
        if state.error:
            self.summary.setText(state.error)
        elif state.result is not None:
            counts: dict[str, int] = {}
            for outcome in state.result.outcomes:
                counts[outcome.status.value] = counts.get(outcome.status.value, 0) + 1
            detail = "、".join(f"{name} {count}" for name, count in sorted(counts.items()))
            prefix = "同步部分完成" if state.result.partial else "同步完成"
            if state.result.reconcile_required:
                prefix = "同步结果含未知远端状态，必须先核对"
            self.summary.setText(f"{prefix}：{detail}")
        elif state.summary is not None:
            counts = "、".join(f"{name} {count}" for name, count in state.summary.counts) or "无变更"
            suffix = "" if state.summary.execution_available else "；当前运行环境未接入该模式执行器"
            self.summary.setText(f"计划 {state.summary.ref.plan_id}：{counts}{suffix}")
        elif state.preflight is not None:
            self.summary.setText(
                "预检通过，可以生成计划。" if state.preflight.available else "；".join(state.preflight.diagnostics)
            )
        self.activate_button.setVisible(
            state.preflight is not None
            and not state.preflight.available
            and any("同步映射" in item for item in state.preflight.diagnostics)
        )
        if self.activate_button.isVisible():
            replacing = any("另一 Variant" in item for item in state.preflight.diagnostics)
            self.activate_button.setText("确认替换 Variant 映射" if replacing else "启用此 Project/Variant 映射")
        self.confirm_button.setVisible(state.confirmation_copy is not None and state.confirmation_token is None)
        self.execute_button.setVisible(state.summary is not None and state.summary.execution_available)
        self.execute_button.setEnabled(state.can_execute)
        self.retry_button.setVisible(state.retry_token is not None)
        self.retry_button.setEnabled(state.can_retry)
        self.reconcile_button.setVisible(state.retry_token is not None and bool(state.retry_token.unknown_item_ids))
        self.reconcile_button.setEnabled(state.can_reconcile)
        self._render_inbound(state)

    def render_activity(self, activity: TaskActivityViewState) -> None:
        self.summary.setText(f"{activity.display_context.title}：{activity.state.value}")
        self._set_busy(not activity.is_terminal)
        if activity.is_terminal:
            ref = JobRef(activity.job_id, activity.owner.owner_id, activity.run_id)
            self._run("result", lambda: self.presenter.complete_job(ref))

    def _plan(self, mode: TerminologySyncMode) -> None:
        def call():
            state = self.presenter.preflight(mode)
            return self.presenter.create_plan() if state.preflight and state.preflight.available else state

        self._run("plan", call)

    def _activate(self) -> None:
        self._run("activate", self.presenter.activate_mapping)

    def _confirm(self) -> None:
        self.render_sync(self.presenter.confirm())

    def _execute(self) -> None:
        self._run("execute", self.presenter.execute)

    def _inbound(self) -> None:
        self._run("inbound", self.presenter.load_inbound)

    def _retry(self) -> None:
        self._run("retry", self.presenter.retry)

    def _reconcile(self) -> None:
        self._run("reconcile", self.presenter.reconcile)

    def _run(self, pending: str, call) -> None:
        self._pending = pending
        self._set_busy(True)
        self._pool.start(_SyncCall(call, self._signals))

    def _completed(self, value: object) -> None:
        pending, self._pending = self._pending, ""
        if isinstance(value, TerminologySyncViewState):
            self.render_sync(value)
        elif pending in {"execute", "retry", "reconcile"}:
            self.summary.setText("任务已提交；可关闭窗口，后台任务仍会继续。")
            self._set_busy(True)
        else:
            self._set_busy(False)

    def _failed(self, error: object) -> None:
        self._pending = ""
        self.render_sync(replace(self.presenter.state, busy=False, error=str(error)))

    def _set_busy(self, busy: bool) -> None:
        self.backup_button.setEnabled(not busy)
        self.bidirectional_button.setEnabled(not busy)
        self.inbound_button.setEnabled(not busy)
        self.activate_button.setEnabled(not busy)
        self.confirm_button.setEnabled(not busy)
        self.inbound_selector.setEnabled(not busy)
        self.inbound_table.setEnabled(not busy)
        if busy:
            self.execute_button.setEnabled(False)
            self.retry_button.setEnabled(False)
            self.reconcile_button.setEnabled(False)
            self.previous_inbound_button.setEnabled(False)
            self.next_inbound_button.setEnabled(False)
            self.preview_inbound_button.setEnabled(False)
            self.commit_inbound_button.setEnabled(False)

    def _init_inbound(self, outer: QVBoxLayout) -> None:
        self.inbound_section = QFrame(self)
        self.inbound_section.setProperty("tbTerminologyCard", True)
        self.inbound_section.setVisible(False)
        layout = QVBoxLayout(self.inbound_section)
        self.inbound_notice = QLabel(
            "远端变化必须逐项接受、拒绝或编辑；提交后仅进入待发布草稿，仍不会影响当前翻译术语版本。",
            self.inbound_section,
        )
        self.inbound_notice.setWordWrap(True)
        self.inbound_selector = QComboBox(self.inbound_section)
        self.inbound_selector.setAccessibleName("待复核远端变化集")
        self.inbound_table = QTableWidget(0, 4, self.inbound_section)
        self.inbound_table.setAccessibleName("当前页远端术语变化")
        self.inbound_table.setHorizontalHeaderLabels(("原文", "远端译文", "复核决定", "编辑译文"))
        paging = QHBoxLayout()
        self.previous_inbound_button = QPushButton("上一页", self.inbound_section)
        self.next_inbound_button = QPushButton("下一页", self.inbound_section)
        self.inbound_page_label = QLabel("", self.inbound_section)
        self.preview_inbound_button = QPushButton("预览草稿变更", self.inbound_section)
        self.commit_inbound_button = QPushButton("提交到待发布草稿", self.inbound_section)
        self.commit_inbound_button.setEnabled(False)
        for widget in (
            self.previous_inbound_button,
            self.next_inbound_button,
            self.inbound_page_label,
            self.preview_inbound_button,
            self.commit_inbound_button,
        ):
            paging.addWidget(widget)
        paging.addStretch(1)
        layout.addWidget(self.inbound_notice)
        layout.addWidget(self.inbound_selector)
        layout.addWidget(self.inbound_table)
        layout.addLayout(paging)
        outer.addWidget(self.inbound_section)
        self.inbound_selector.currentIndexChanged.connect(self._select_inbound)
        self.previous_inbound_button.clicked.connect(lambda: self._page_inbound(-50))
        self.next_inbound_button.clicked.connect(lambda: self._page_inbound(50))
        self.preview_inbound_button.clicked.connect(self._preview_inbound)
        self.commit_inbound_button.clicked.connect(lambda: self._run("commit-inbound", self.presenter.commit_inbound))

    def _render_inbound(self, state: TerminologySyncViewState) -> None:
        self.inbound_section.setVisible(bool(state.inbound_sets))
        if not state.inbound_sets:
            return
        selected = state.selected_inbound_id
        current_ids = [self.inbound_selector.itemData(index) for index in range(self.inbound_selector.count())]
        expected_ids = [item.change_set_id for item in state.inbound_sets]
        if current_ids != expected_ids:
            self.inbound_selector.blockSignals(True)
            self.inbound_selector.clear()
            for item in state.inbound_sets:
                self.inbound_selector.addItem(
                    f"{item.created_at:%Y-%m-%d %H:%M} · {len(item.items)} 项", item.change_set_id
                )
            self.inbound_selector.blockSignals(False)
        index = self.inbound_selector.findData(selected)
        if index >= 0 and self.inbound_selector.currentIndex() != index:
            self.inbound_selector.blockSignals(True)
            self.inbound_selector.setCurrentIndex(index)
            self.inbound_selector.blockSignals(False)
        proposal_selection = (
            None if state.inbound_proposal is None else getattr(state.inbound_proposal, "selection", None)
        )
        reviewed_choices = (
            {} if proposal_selection is None else {item.item_id: item for item in proposal_selection.choices}
        )
        self._rendered_inbound_id = selected
        self._rendered_inbound_items = state.inbound_items
        self.inbound_table.setRowCount(len(state.inbound_items))
        for row, item in enumerate(state.inbound_items):
            content = item.remote or item.local
            self.inbound_table.setItem(row, 0, QTableWidgetItem("" if content is None else content.original))
            self.inbound_table.setItem(row, 1, QTableWidgetItem("" if item.remote is None else item.remote.translation))
            choice = QComboBox(self.inbound_table)
            choice.addItem("接受", InboundReviewDecision.ACCEPT.value)
            choice.addItem("拒绝", InboundReviewDecision.REJECT.value)
            if content is not None:
                choice.addItem("编辑", InboundReviewDecision.EDIT.value)
            choice.setProperty("item_id", item.item_id)
            edit = self._review_drafts.row(selected, item, reviewed_choices.get(item.item_id))
            choice.setCurrentIndex(choice.findData(edit.decision.value))
            self.inbound_table.setCellWidget(row, 2, choice)
            edited = QLineEdit(edit.translation, self.inbound_table)
            edited.setProperty("item_id", item.item_id)
            edited.setEnabled(edit.decision is InboundReviewDecision.EDIT)
            self.inbound_table.setCellWidget(row, 3, edited)
            choice.currentIndexChanged.connect(self._inbound_edited)
            edited.textChanged.connect(self._inbound_edited)
        start = state.inbound_offset + 1 if state.inbound_total else 0
        end = state.inbound_offset + len(state.inbound_items)
        self.inbound_page_label.setText(f"{start}-{end} / {state.inbound_total}")
        self.previous_inbound_button.setEnabled(not state.busy and state.inbound_offset > 0)
        self.next_inbound_button.setEnabled(not state.busy and end < state.inbound_total)
        self.preview_inbound_button.setEnabled(bool(state.inbound_items) and not state.busy)
        self.commit_inbound_button.setEnabled(
            not state.busy
            and state.inbound_proposal is not None
            and state.inbound_proposal.committable
            and state.inbound_commit is None
        )
        if state.inbound_commit is not None:
            self.inbound_notice.setText("已写入待发布草稿；必须发布新术语版本后才会影响翻译。")
        elif state.inbound_proposal is not None:
            self.inbound_notice.setText("草稿变更预览已生成；确认提交后仍处于待发布状态。")
        else:
            self.inbound_notice.setText(state.inbound_notice)

    def _select_inbound(self, index: int) -> None:
        change_set_id = self.inbound_selector.itemData(index)
        if isinstance(change_set_id, str):
            self._remember_inbound_page()
            self.render_sync(self.presenter.select_inbound(change_set_id))

    def _page_inbound(self, delta: int) -> None:
        self._remember_inbound_page()
        self.render_sync(self.presenter.page_inbound(self.presenter.state.inbound_offset + delta))

    def _remember_inbound_page(self) -> None:
        if self._rendered_inbound_id is None:
            return
        edits = []
        items = {item.item_id: item for item in self._rendered_inbound_items}
        for row in range(self.inbound_table.rowCount()):
            choice_widget = self.inbound_table.cellWidget(row, 2)
            edited_widget = self.inbound_table.cellWidget(row, 3)
            if not isinstance(choice_widget, QComboBox) or not isinstance(edited_widget, QLineEdit):
                continue
            item_id = str(choice_widget.property("item_id"))
            decision = InboundReviewDecision(str(choice_widget.currentData()))
            edited_widget.setEnabled(decision is InboundReviewDecision.EDIT)
            edits.append(InboundReviewEdit(items[item_id], decision, edited_widget.text()))
        self._review_drafts.remember(self._rendered_inbound_id, tuple(edits))

    def _inbound_edited(self, *_args) -> None:
        self._remember_inbound_page()
        self.presenter.invalidate_inbound_preview()
        self.commit_inbound_button.setEnabled(False)
        self.inbound_notice.setText("复核内容已修改，请重新预览草稿变更后提交。")

    def _preview_inbound(self) -> None:
        self._remember_inbound_page()
        self.presenter.invalidate_inbound_preview()
        self.commit_inbound_button.setEnabled(False)
        try:
            choices = self._review_drafts.choices(self.presenter.state.selected_inbound_id)
        except ValueError as exc:
            self._failed(exc)
            return
        self._run("preview-inbound", lambda: self.presenter.preview_inbound(choices))


__all__ = ["SYNC_ACTIONS", "TerminologySyncActionDescriptor", "TerminologySyncPanel", "TerminologySyncView"]
