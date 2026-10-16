"""Synchronization workbench composed from bounded review and result views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import JobSnapshot, JobState
from transbridge.application.tasks.activity import TaskActivityViewState
from transbridge.application.terminology_sync.models import TerminologySyncMode

from .sync_copy import ACTION_LABELS, STATUS_LABELS, label, target_text
from .sync_details import SyncDetailsTable, plan_rows
from .sync_dispatch import SyncCall, SyncSignals
from .sync_inbound_view import SyncInboundReviewPanel
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
    def render_sync(self, state: TerminologySyncViewState) -> None: ...


class TerminologySyncPanel(QFrame):
    """Mount workflow controls while the presenter owns application state."""

    draft_committed = pyqtSignal(object)

    def __init__(self, presenter: TerminologySyncPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.presenter = presenter
        self._cancel_handler: Callable[[JobRef], JobSnapshot] | None = None
        self.setProperty("tbTerminologySoftCard", True)
        self.setAccessibleName("ParaTranz 术语备份与双向同步")
        self._pool = QThreadPool.globalInstance()
        self._signals = SyncSignals()
        self._signals.completed.connect(self._completed, Qt.ConnectionType.QueuedConnection)
        self._signals.failed.connect(self._failed, Qt.ConnectionType.QueuedConnection)
        self._signals.activity.connect(self.render_activity, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(self._signals.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        title = QLabel("ParaTranz 备份与双向同步", self)
        title.setProperty("tbTerminologySectionTitle", True)
        self.summary = QLabel("请选择备份或双向同步，生成计划后检查全部影响。", self)
        self.summary.setWordWrap(True)
        self.target = QLabel(self)
        self.target.setWordWrap(True)
        self.target.setAccessibleName("本次计划的同步目标")
        self.confirmation = QLabel(self)
        self.confirmation.setWordWrap(True)
        self.confirmation.setAccessibleName("远端变更确认说明")
        for dynamic_label in (self.summary, self.target, self.confirmation):
            dynamic_label.setTextFormat(Qt.TextFormat.PlainText)
        for widget in (title, self.summary, self.target, self.confirmation):
            layout.addWidget(widget)
        self.backup_button = QPushButton("备份已发布版本…", self)
        self.backup_button.setAccessibleName(SYNC_ACTIONS[0].accessible_name)
        self.bidirectional_button = QPushButton("双向同步…", self)
        self.bidirectional_button.setAccessibleName(SYNC_ACTIONS[1].accessible_name)
        self.activate_button = QPushButton("关联当前翻译版本", self)
        self.confirm_button = QPushButton("确认以上远端影响", self)
        self.execute_button = QPushButton("执行已确认计划", self)
        self.inbound_button = QPushButton("复核远端变化", self)
        self.retry_button = QPushButton("重试失败项", self)
        self.retry_button.setAccessibleName("重试已确定失败的术语同步项")
        self.reconcile_button = QPushButton("核对未知远端结果", self)
        self.reconcile_button.setAccessibleName("核对术语同步中未知的远端结果")
        self.cancel_button = QPushButton("停止同步", self)
        for buttons in (
            (self.backup_button, self.bidirectional_button, self.activate_button, self.inbound_button),
            (self.confirm_button, self.execute_button, self.retry_button, self.reconcile_button, self.cancel_button),
        ):
            row = QHBoxLayout()
            for button in buttons:
                row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
        for button in (
            self.activate_button,
            self.confirm_button,
            self.execute_button,
            self.retry_button,
            self.reconcile_button,
            self.cancel_button,
        ):
            button.hide()
        self.plan_details = SyncDetailsTable("计划逐项影响", ("操作", "变更前", "变更后", "原因 / 影响"), self)
        self.result_details = SyncDetailsTable("执行逐项结果", ("术语", "操作", "结果", "原因 / 诊断"), self)
        for details in (self.plan_details, self.result_details):
            details.hide()
            layout.addWidget(details)
        self.inbound_review = SyncInboundReviewPanel(
            presenter,
            lambda pending, call: self._run(pending, call),
            lambda error: self._failed(error),
            self,
        )
        layout.addWidget(self.inbound_review)
        self.inbound_review.draft_committed.connect(self.draft_committed)
        # Keep the mounted panel's existing public review controls available to hosts.
        self.inbound_section = self.inbound_review
        self.inbound_notice = self.inbound_review.notice
        self.inbound_selector = self.inbound_review.selector
        self.inbound_table = self.inbound_review.table
        self.previous_inbound_button = self.inbound_review.previous_button
        self.next_inbound_button = self.inbound_review.next_button
        self.inbound_page_label = self.inbound_review.page_label
        self.preview_inbound_button = self.inbound_review.preview_button
        self.commit_inbound_button = self.inbound_review.commit_button

        self.backup_button.clicked.connect(lambda: self._plan(TerminologySyncMode.BACKUP))
        self.bidirectional_button.clicked.connect(lambda: self._plan(TerminologySyncMode.BIDIRECTIONAL))
        self.activate_button.clicked.connect(lambda: self._run("activate", self.presenter.activate_mapping))
        self.confirm_button.clicked.connect(lambda: self._run("confirm", self.presenter.confirm))
        self.execute_button.clicked.connect(lambda: self._run("execute", self.presenter.execute))
        self.inbound_button.clicked.connect(lambda: self._run("inbound", self.presenter.load_inbound))
        self.retry_button.clicked.connect(lambda: self._run("retry", self.presenter.retry))
        self.reconcile_button.clicked.connect(lambda: self._run("reconcile", self.presenter.reconcile))
        self.cancel_button.clicked.connect(self._cancel)
        self.plan_details.page_requested.connect(self._page_plan)

    def set_cancel_handler(self, handler: Callable[[JobRef], JobSnapshot]) -> None:
        self._cancel_handler = handler
        self._render_cancel(self.presenter.state)

    def render_sync(self, state: TerminologySyncViewState) -> None:
        if self._signals.closed:
            return
        self._set_busy(state.busy)
        self.summary.setText(_summary_text(state))
        target = getattr(state.summary, "target", None) or getattr(state.preflight, "target", None)
        self.target.setText(target_text(target, getattr(state.summary, "target_identity", "")))
        self.target.setVisible(target is not None or state.summary is not None)
        self.confirmation.setText(state.confirmation_copy or "")
        self.confirmation.setVisible(state.confirmation_copy is not None)
        show_activate = (
            state.preflight is not None
            and not state.preflight.available
            and any("同步映射" in item for item in state.preflight.diagnostics)
        )
        self.activate_button.setVisible(show_activate)
        if show_activate:
            replacing = any("另一 Variant" in item for item in state.preflight.diagnostics)
            self.activate_button.setText("确认更换关联版本" if replacing else "关联当前翻译版本")
        self.confirm_button.setVisible(
            state.confirmation_copy is not None and state.confirmation_token is None and state.job_ref is None
        )
        self.confirm_button.setEnabled(
            not state.busy
            and not state.error
            and state.summary is not None
            and not state.summary.blocked
            and not state.summary.has_conflicts
        )
        self.execute_button.setVisible(state.summary is not None and state.summary.execution_available)
        self.execute_button.setEnabled(state.can_execute)
        self.retry_button.setVisible(state.retry_token is not None)
        self.retry_button.setEnabled(state.can_retry)
        self.reconcile_button.setVisible(state.retry_token is not None and bool(state.retry_token.unknown_item_ids))
        self.reconcile_button.setEnabled(state.can_reconcile)
        self._render_cancel(state)
        self.plan_details.setVisible(state.summary is not None)
        self.plan_details.set_page(
            plan_rows(state.items),
            offset=state.plan_offset,
            total=state.plan_total,
            previous=state.plan_offset > 0,
            following=state.next_cursor is not None,
            busy=state.busy,
        )
        self.result_details.setVisible(state.result is not None)
        self.result_details.set_projection(state.result_projection, busy=state.busy)
        self.inbound_review.render(state)

    def render_activity(self, activity: TaskActivityViewState) -> None:
        if self._signals.closed:
            return
        ref = JobRef(activity.job_id, activity.owner.owner_id, activity.run_id)
        if not self.presenter.observe_activity(ref, activity.state, terminal=activity.is_terminal):
            return
        self.summary.setText(f"{activity.display_context.title}：{label(activity.state, STATUS_LABELS)}")
        self._set_busy(not activity.is_terminal)
        self._render_cancel(self.presenter.state)
        if activity.is_terminal:
            self._run("result", lambda: self.presenter.complete_job(ref))

    @property
    def activity_sink(self) -> Callable[[TaskActivityViewState], None]:
        return self._signals.post_activity

    def dispose(self) -> None:
        if self._signals.closed:
            return
        self._signals.close()
        self._signals.completed.disconnect(self._completed)
        self._signals.failed.disconnect(self._failed)
        self._signals.activity.disconnect(self.render_activity)

    def _plan(self, mode: TerminologySyncMode) -> None:
        self.inbound_review.remember_page()

        def call():
            state = self.presenter.preflight(mode)
            return self.presenter.create_plan() if state.preflight and state.preflight.available else state

        self._run("plan", call)

    def _page_plan(self, direction: int) -> None:
        self._run("page", self.presenter.load_previous_page if direction < 0 else self.presenter.load_next_page)

    def _cancel(self) -> None:
        ref, handler = self.presenter.state.job_ref, self._cancel_handler
        if ref is not None and handler is not None:
            self._run("cancel", lambda: self.presenter.update_job(handler(ref)))

    def _render_cancel(self, state: TerminologySyncViewState) -> None:
        active = state.job_state in (JobState.QUEUED, JobState.RUNNING, JobState.PAUSED, JobState.CANCELLING)
        self.cancel_button.setVisible(self._cancel_handler is not None and state.job_ref is not None and active)
        self.cancel_button.setText("正在停止…" if state.job_state is JobState.CANCELLING else "停止同步")
        self.cancel_button.setEnabled(state.job_state is not JobState.CANCELLING)

    def _run(self, pending: str, call) -> None:
        if self._signals.closed:
            return
        self._set_busy(True)
        messages = {
            "plan": "正在检查目标并生成同步计划…",
            "inbound": "正在读取待复核远端变化…",
            "preview-inbound": "正在预览草稿变更…",
            "commit-inbound": "正在提交到待发布草稿…",
            "page": "正在读取计划明细…",
            "activate": "正在关联当前翻译版本…",
            "cancel": "正在请求停止同步…",
        }
        if pending in messages:
            self.summary.setText(messages[pending])
        self._pool.start(SyncCall(call, self._signals))

    def _completed(self, value: object) -> None:
        if self._signals.closed:
            return
        if isinstance(value, TerminologySyncViewState):
            current = self.presenter.state
            if value.job_ref is not None and current.job_ref is not None and value.job_ref != current.job_ref:
                return
            self.render_sync(current if isinstance(self.presenter, TerminologySyncPresenter) else value)
        elif isinstance(value, JobRef):
            self._run("result", lambda: self.presenter.complete_job(value))
        else:
            self._set_busy(False)

    def _failed(self, error: object) -> None:
        if self._signals.closed:
            return
        report = getattr(self.presenter, "report_error", None)
        state = report(error) if callable(report) else replace(self.presenter.state, busy=False, error=str(error))
        self.render_sync(state)

    def _set_busy(self, busy: bool) -> None:
        for button in (self.backup_button, self.bidirectional_button, self.activate_button, self.inbound_button):
            button.setEnabled(not busy)
        self.inbound_review.set_busy(busy)
        if busy:
            for button in (
                self.confirm_button,
                self.execute_button,
                self.retry_button,
                self.reconcile_button,
                self.plan_details.previous_button,
                self.plan_details.next_button,
            ):
                button.setEnabled(False)

    def _preview_inbound(self) -> None:
        self.inbound_review.preview()


def _summary_text(state: TerminologySyncViewState) -> str:
    if state.error:
        return f"操作未完成：{state.error}"
    if state.result is not None:
        projection = state.result_projection
        counts, detail = projection.counts, projection.detail
        prefix = "同步完成"
        if projection.partial:
            prefix = "同步部分完成" if counts.get("succeeded", 0) + counts.get("reconciled", 0) else "同步未完成"
        if state.job_state is JobState.CANCELLED:
            prefix = "同步已停止；已确认的成功项会保留"
        if state.result.reconcile_required:
            prefix = "同步结果含未知远端状态，必须先核对"
        recovery = "；可重试失败项，成功项不会重复执行。" if state.can_retry else "。"
        return f"{prefix}：{detail}{recovery}请在下方查看逐项结果。"
    if state.job_state in (JobState.FAILED, JobState.CANCELLED):
        prefix = "同步失败" if state.job_state is JobState.FAILED else "同步已停止"
        failure = "" if state.failure is None else f"：{state.failure.message}（{state.failure.code}）"
        return f"{prefix}{failure}。未取得逐项执行结果，请重新生成计划后复核。"
    if state.job_state is not None:
        return f"同步任务：{label(state.job_state, STATUS_LABELS)}。可关闭窗口，后台任务仍会继续。"
    if state.summary is not None:
        detail = (
            "、".join(f"{label(name, ACTION_LABELS)} {count} 项" for name, count in state.summary.counts) or "无变更"
        )
        suffix = (
            "；请先处理下方冲突或阻断项，再重新生成计划。"
            if state.summary.blocked or state.summary.has_conflicts
            else ""
        )
        diagnostics = "；".join(state.summary.diagnostics)
        if not state.summary.execution_available:
            suffix += "；当前运行环境未接入该模式执行器"
        return f"同步计划：{detail}{suffix}" + (f"；{diagnostics}" if diagnostics else "")
    if state.preflight is not None:
        return "预检通过，可以生成计划。" if state.preflight.available else "；".join(state.preflight.diagnostics)
    return "请选择备份或双向同步，生成计划后检查全部影响。"


__all__ = ["SYNC_ACTIONS", "TerminologySyncActionDescriptor", "TerminologySyncPanel", "TerminologySyncView"]
