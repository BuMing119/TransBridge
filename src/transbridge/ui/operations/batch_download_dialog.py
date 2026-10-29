"""Download and save selected sources, asking only about unresolved translations."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event

from PyQt6.QtCore import QSignalBlocker, Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog
from transbridge.ui.workers import ApiWorker

from .batch_download_view import DownloadPreview, DownloadReceipt, DownloadSourceOption
from .categorized_upload_progress import LatestProgress
from .download_run import run_download
from .sync_review_dialog import SyncReviewDialog


class BatchDownloadDialog(QDialog):
    """Own selection and worker lifetime; callbacks own snapshots and writes."""

    def __init__(
        self,
        project_label: str,
        variant_label: str,
        target_label: str,
        *,
        load_sources: Callable,
        compare: Callable,
        apply: Callable,
        discard=None,
        default_policy="keep_current",
        parent=None,
    ) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("从 ParaTranz 更新工程译文")
        self.resize(820, 650)
        self._load_sources_callback = load_sources
        self._compare = compare
        self._apply = apply
        self._discard = discard
        self._default_policy = default_policy
        self._sources: tuple[DownloadSourceOption, ...] | None = None
        self._preview: DownloadPreview | None = None
        self._completed_label: str | None = None
        self._retry_available = False
        self._decisions: tuple[tuple[str, str], ...] = ()
        self._worker = None
        self._cancel_event = Event()
        self._phase = ""
        self._generation = 0
        self._run_generation = 0
        self._outcome = self._error = None
        self._mailbox: LatestProgress | None = None
        self._status_state = None
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._consume_progress)
        self._build_widgets(project_label, variant_label, target_label)
        self._sync_controls()
        QTimer.singleShot(0, self._load_sources)

    def _build_widgets(self, project: str, variant: str, target: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        scope = QLabel(f"当前工程：{project}  ·  翻译版本：{variant}\nParaTranz 项目：{target}", self)
        scope.setTextFormat(Qt.TextFormat.PlainText)
        scope.setWordWrap(True)
        root.addWidget(scope)
        notice = QLabel(
            "选择插件后下载并保存译文。分歧可在本轮确认，关闭后清理下载数据，再次下载会重新读取云端。", self
        )
        notice.setWordWrap(True)
        root.addWidget(notice)
        sources_header = QHBoxLayout()
        sources_header.addWidget(QLabel("本次下载的插件", self))
        sources_header.addStretch()
        self._select_all = QPushButton("全选", self)
        self._select_all.clicked.connect(lambda: self._set_all_sources(True))
        self._select_none = QPushButton("全不选", self)
        self._select_none.clicked.connect(lambda: self._set_all_sources(False))
        for button in (self._select_all, self._select_none):
            button.setAutoDefault(False)
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            sources_header.addWidget(button)
        root.addLayout(sources_header)
        self._source_list = QListWidget(self)
        self._source_list.setAccessibleName("选择下载更新的插件")
        self._source_list.itemChanged.connect(self._invalidate)
        root.addWidget(self._source_list, 1)
        self._policy = QComboBox(self)
        self._policy.addItem("遇到本轮新编辑时保留当前译文", "keep_current")
        self._policy.addItem("遇到本轮新编辑时采用下载成果", "prefer_incoming")
        self._policy.addItem("遇到本轮新编辑时暂缓处理", "defer")
        self._policy.setCurrentIndex(max(0, self._policy.findData(self._default_policy)))
        self._policy.setAccessibleName("下载译文更新策略")
        self._policy.currentIndexChanged.connect(self._invalidate)
        root.addWidget(self._policy)
        self._summary = QLabel("正在读取工程插件…", self)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)
        self._status = QLabel("正在读取工程插件…", self)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        ComponentStyle.apply_static(self._status, ComponentKind.NOTIFICATION)
        root.addWidget(self._status)
        self._details = QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setAccessibleName("逐插件下载结果")
        self._details.setMaximumHeight(170)
        self._details.hide()
        root.addWidget(self._details)
        self._progress = QProgressBar(self)
        self._progress.hide()
        root.addWidget(self._progress)
        auxiliary = QHBoxLayout()
        self._review_button = QPushButton("确认这几条翻译", self)
        self._review_button.clicked.connect(self._review)
        self._review_button.setAutoDefault(False)
        ComponentStyle.apply_static(self._review_button, ComponentKind.BUTTON)
        auxiliary.addWidget(self._review_button)
        auxiliary.addStretch()
        root.addLayout(auxiliary)
        footer = QHBoxLayout()
        self._cancel = QPushButton("关闭", self)
        self._cancel.clicked.connect(self.reject)
        self._reload_button = QPushButton("重新读取插件", self)
        self._reload_button.clicked.connect(self._load_sources)
        self._primary_button = QPushButton("下载并保存", self)
        self._primary_button.clicked.connect(self._request_primary)
        for button in (
            self._cancel,
            self._reload_button,
        ):
            button.setAutoDefault(False)
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            footer.addWidget(button)
        footer.addStretch()
        self._primary_button.setAutoDefault(False)
        ComponentStyle.apply_static(self._primary_button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._primary_button, SemanticState.PRIMARY)
        footer.addWidget(self._primary_button)
        root.addLayout(footer)

    def selection(self) -> tuple[str, ...]:
        return tuple(
            self._source_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._source_list.count())
            if self._source_list.item(index).checkState() == Qt.CheckState.Checked
        )

    def _load_sources(self) -> None:
        if self._worker is None:
            self._discard_round()
            self._start_worker("load", self._load_sources_callback)

    def _render_sources(self, sources: tuple[DownloadSourceOption, ...]) -> None:
        previous = set(self.selection()) if self._sources is not None else None
        self._sources = sources
        with QSignalBlocker(self._source_list):
            self._source_list.clear()
            for source in sources:
                item = QListWidgetItem(f"{source.label}  ·  {source.count:,} 条", self._source_list)
                item.setData(Qt.ItemDataRole.UserRole, source.source_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = source.selected if previous is None else source.source_id in previous
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._invalidate()

    def _invalidate(self, *_args) -> None:
        self._generation += 1
        self._preview = None
        self._completed_label = None
        self._retry_available = False
        chosen = set(self.selection())
        count = sum(source.count for source in self._sources or () if source.source_id in chosen)
        self._summary.setText(f"已选 {len(chosen)} 个插件，共 {count:,} 条本地词条。")
        if self._worker is None:
            self._discard_round()
            self._set_status("选好插件和更新方式后，点击“下载并保存”。", SemanticState.INFO)
            self._details.hide()
        self._sync_controls()

    def _set_all_sources(self, checked: bool) -> None:
        if self._worker is not None:
            return
        with QSignalBlocker(self._source_list):
            for index in range(self._source_list.count()):
                self._source_list.item(index).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self._invalidate()

    def _request_primary(self) -> None:
        if self._worker is not None or self._completed_label is not None or not self.selection():
            return
        sources, policy, decisions = self.selection(), self._policy.currentData(), self._decisions
        compare, apply = self._compare, self._apply
        self._preview = None
        self._retry_available = False
        self._start_worker(
            "run", lambda progress, cancel: run_download(sources, policy, decisions, compare, apply, progress, cancel)
        )

    def _review(self) -> None:
        if self._worker is not None or self._preview is None or not self._preview.review_rows:
            return
        dialog = SyncReviewDialog(self._preview.review_rows, self, submit_on_accept=True)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._decisions = tuple(sorted(dialog.decisions().items()))
                self._completed_label = None
                self._retry_available = False
                self._request_primary()
        finally:
            dialog.deleteLater()

    def _start_worker(self, phase: str, operation: Callable) -> None:
        self._phase = phase
        self._run_generation = self._generation
        self._cancel_event = Event()
        self._outcome = self._error = None
        self._progress.setRange(0, 0)
        self._progress.show()
        self._set_status(
            "正在读取工程插件…" if phase == "load" else "正在下载并保存工程译文…",
            SemanticState.INFO,
        )
        mailbox = self._mailbox = LatestProgress()
        cancel = self._cancel_event
        worker = ApiWorker(lambda: operation(mailbox.publish, cancel), route_http_errors=False)
        self._worker = worker
        worker.result.connect(lambda value: self._received(value, mailbox))
        worker.error.connect(lambda error: self._failed(error, mailbox))
        worker.finished.connect(lambda: self._finished(mailbox))
        self._timer.start()
        self._sync_controls()
        try:
            worker.start()
        except Exception as exc:
            self._error = str(exc)
            self._finished(mailbox)

    def _received(self, value, mailbox: LatestProgress) -> None:
        if mailbox is self._mailbox:
            self._outcome = value

    def _failed(self, error: str, mailbox: LatestProgress) -> None:
        if mailbox is self._mailbox:
            self._error = error

    def _finished(self, mailbox: LatestProgress) -> None:
        if mailbox is not self._mailbox:
            return
        self._timer.stop()
        update = mailbox.finish()
        if update is not None:
            self._render_progress(*update)
        self._mailbox = None
        self._worker = None
        self._progress.hide()
        phase, outcome = self._phase, self._outcome
        self._phase = ""
        if self._cancel_event.is_set() and not isinstance(outcome, DownloadReceipt):
            self._set_status("已取消，本地译文未更改；本次选择已保留。", SemanticState.INFO)
        elif self._error is not None:
            self._retry_available = True
            self._set_status(f"{self._error}。本次选择已保留，可在此重试。", SemanticState.ERROR)
        elif outcome is None:
            self._retry_available = True
            self._set_status("未收到处理结果，本次选择已保留，可在此重试。", SemanticState.ERROR)
        elif phase == "load":
            self._render_sources(tuple(outcome))
        elif self._run_generation != self._generation and not isinstance(outcome, DownloadReceipt):
            self._preview = None
            self._set_status("所选插件已变化，可按当前选择重新下载。", SemanticState.INFO)
        elif isinstance(outcome, DownloadPreview):
            self._render_preview(outcome)
        else:
            self._render_receipt(outcome)
            if self._run_generation != self._generation:
                self._preview = None
                self._completed_label = None
                self._retry_available = False
        self._outcome = None
        if (
            self._error is not None
            or self._cancel_event.is_set()
            or self._preview is None
            or not any(not row.decision for row in self._preview.review_rows)
        ):
            self._discard_round()
        self._sync_controls()

    def _discard_round(self):
        if self._discard is not None:
            self._discard()
        self._preview = None
        self._decisions = ()

    def _render_preview(self, preview: DownloadPreview) -> None:
        self._preview = preview
        if preview.source_ids:
            with QSignalBlocker(self._source_list):
                for index in range(self._source_list.count()):
                    item = self._source_list.item(index)
                    item.setCheckState(
                        Qt.CheckState.Checked
                        if item.data(Qt.ItemDataRole.UserRole) in preview.source_ids
                        else Qt.CheckState.Unchecked
                    )
        if preview.policy:
            with QSignalBlocker(self._policy):
                self._policy.setCurrentIndex(max(0, self._policy.findData(preview.policy)))
        self._retry_available = (
            preview.retryable
            or preview.updates > 0
            or preview.can_save
            or not preview.can_apply
            and not preview.deferred
        )
        self._completed_label = None if self._retry_available else "已完成"
        self._summary.setText(f"保持不变 {preview.unchanged:,} 条，待确认 {preview.deferred:,} 条。")
        if preview.status:
            message = preview.status
        elif preview.retryable:
            message = "本轮仍有未完成项，请查看文件结果后重试。"
        elif not preview.updates and not preview.deferred:
            message = (
                "所选工程译文无需更新。" if preview.can_apply else "本轮没有可更新译文，请查看文件结果；可原地重试。"
            )
        elif not preview.updates:
            message = "待确认项保留本地内容；请确认要采用的译文，其他译文无需更新。"
        elif not preview.can_apply:
            message = "本轮尚不能更新译文，请查看文件结果；可原地重试。"
        else:
            message = "本次尚未保存这些译文，选择已保留，可原地重试下载并保存。"
        self._set_status(
            message, SemanticState.WARNING if preview.deferred or not preview.can_apply else SemanticState.INFO
        )
        self._set_details(preview.details)

    def _render_receipt(self, receipt: DownloadReceipt) -> None:
        if receipt.requires_review:
            if receipt.preview is not None:
                self._render_preview(receipt.preview)
            else:
                self._preview = None
            pending = 0 if receipt.preview is None else receipt.preview.deferred
            if receipt.updated:
                message = f"已更新并保存 {receipt.updated:,} 条工程译文；仍有 {pending:,} 条需要确认，保留本地内容。"
                self._summary.setText(f"已更新并保存 {receipt.updated:,} 条，待确认 {pending:,} 条。")
            else:
                message = (
                    receipt.preview.status
                    if receipt.preview is not None and receipt.preview.status
                    else "本次尚未保存译文，选择已保留，可原地重试。"
                )
            self._set_status(message, SemanticState.WARNING)
        else:
            self._preview = None
            if receipt.retryable:
                self._set_status(
                    f"已更新并保存 {receipt.updated:,} 条工程译文；仍有未完成项，可原地重试。", SemanticState.WARNING
                )
                self._summary.setText(f"已保存 {receipt.updated:,} 条，未完成的来源可重试。")
            else:
                self._set_status(
                    f"已更新 {receipt.updated:,} 条工程译文，并保存到当前本地版本。", SemanticState.SUCCESS
                )
                self._summary.setText("本次更新与保存已完成，临时下载数据已清理。")
        preview = receipt.preview
        self._retry_available = (
            receipt.retryable
            or preview is not None
            and preview.retryable
            or receipt.requires_review
            and (preview is None or preview.updates > 0 or preview.can_save)
        )
        self._completed_label = None if self._retry_available else "已保存"
        if receipt.details:
            self._set_details(receipt.details)

    def _set_details(self, details: tuple[str, ...]) -> None:
        parts, remaining, truncated = [], 32_000, len(details) > 200
        for detail in details[:200]:
            parts.append(detail[:remaining])
            if len(detail) > remaining:
                truncated = True
                break
            remaining -= len(detail) + 1
            if remaining <= 0:
                truncated = True
                break
        lines = "\n".join(parts)
        self._details.setPlainText(lines + ("\n…详情已截断，统计包含全部结果。" if truncated else ""))
        self._details.setVisible(bool(lines))

    def _consume_progress(self) -> None:
        update = self._mailbox.take() if self._mailbox is not None else None
        if update is not None:
            self._render_progress(*update)

    def _render_progress(self, current: int, total: int, message: str) -> None:
        self._progress.setRange(0, max(total, 0))
        self._progress.setValue(max(0, min(current, total)))
        if message and not self._cancel_event.is_set():
            self._set_status(message, SemanticState.INFO)

    def _sync_controls(self) -> None:
        busy = self._worker is not None
        self._source_list.setEnabled(not busy)
        self._policy.setEnabled(not busy)
        self._select_all.setEnabled(not busy and bool(self._sources))
        self._select_none.setEnabled(not busy and bool(self._sources))
        self._reload_button.setVisible(self._sources is None)
        self._reload_button.setEnabled(not busy)
        self._review_button.setVisible(self._preview is not None and bool(self._preview.review_rows))
        self._review_button.setEnabled(not busy)
        self._primary_button.setText(
            self._completed_label or ("重试未完成项" if self._retry_available else "下载并保存")
        )
        self._primary_button.setEnabled(not busy and self._completed_label is None and bool(self.selection()))
        self._cancel.setText("取消任务" if busy else "关闭")
        self._cancel.setEnabled(not busy or not self._cancel_event.is_set())

    def _set_status(self, message: str, state: SemanticState) -> None:
        self._status.setText(message)
        if state != self._status_state:
            self._status_state = state
            ComponentStyle.apply_state(self._status, state)

    def done(self, result: int) -> None:
        if self._worker is not None:
            self._cancel_event.set()
            self._set_status("正在取消，等待当前处理结束；本次选择会保留。", SemanticState.INFO)
            self._sync_controls()
        else:
            self._discard_round()
            super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
