"""Select local categories and upload with background cloud verification."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog
from transbridge.ui.operations.categorized_upload_progress import LatestProgress
from transbridge.ui.workers import ApiWorker


@dataclass(frozen=True, slots=True)
class FileOption:
    file_key: str
    path: str
    count: int


@dataclass(frozen=True, slots=True)
class SourceOption:
    source_id: str
    label: str
    files: tuple[FileOption, ...]


@dataclass(frozen=True, slots=True)
class Selection:
    source_ids: tuple[str, ...]
    file_keys: tuple[str, ...]
    migrate_existing: bool = False
    translation_mode: str = "orig_only"


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    success_count: int
    failed_count: int
    cancelled_count: int = 0
    details: tuple[str, ...] = ()
    pending_count: int = 0


class CategorizedUploadDialog(QDialog):
    """Own UI selection and worker lifetime; injected callbacks own application authority."""

    def __init__(
        self,
        project_label: str,
        variant_label: str,
        target_label: str,
        *,
        load_preview: Callable,
        upload: Callable,
        batch: bool = True,
        categorized: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._kind = "分类" if categorized else "文件"
        self._file_label = "分类文件" if categorized else "文件"
        self.setWindowTitle(f"{'批量' if batch else '当前插件'}{self._kind}上传到 ParaTranz")
        self.resize(850, 700)
        self._batch = batch
        self._load_preview = load_preview
        self._upload = upload
        self._sources: tuple[SourceOption, ...] | None = None
        self._file_selected: dict[str, bool] = {}
        self._worker = None
        self._phase = ""
        self._cancel_event = Event()
        self._outcome = None
        self._error = None
        self._status_state: SemanticState | None = None
        self._progress_mailbox: LatestProgress | None = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._consume_progress)
        self._build_widgets(project_label, variant_label, target_label)
        self._sync_controls()
        QTimer.singleShot(0, self._load_sources)

    def _build_widgets(self, project: str, variant: str, target: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        self._scope = QLabel(f"当前工程：{project}  ·  翻译版本：{variant}\nParaTranz 项目：{target}", self)
        self._scope.setTextFormat(Qt.TextFormat.PlainText)
        self._scope.setWordWrap(True)
        root.addWidget(self._scope)
        root.addWidget(
            QLabel(
                "选择文件后整批上传；增量更新保留云端其他词条，安全导入保留人工译文。"
                if self._batch
                else "选择文件后整批上传；增量更新保留云端其他词条，安全导入保留人工译文。",
                self,
            )
        )
        root.addWidget(QLabel("工程中的插件" if self._batch else "当前插件", self))
        self._source_list = QListWidget(self)
        self._source_list.setAccessibleName("选择分类上传的插件")
        self._source_list.setMaximumHeight(130)
        self._source_list.itemChanged.connect(self._sources_changed)
        root.addWidget(self._source_list)
        root.addWidget(QLabel(f"选择本次上传的{self._file_label}", self))
        self._files = QTreeWidget(self)
        self._files.setColumnCount(2)
        self._files.setHeaderLabels([self._file_label, "词条数"])
        self._files.setRootIsDecorated(False)
        self._files.setColumnWidth(0, 300)
        self._files.setColumnWidth(1, 75)
        self._files.setAccessibleName("选择分类上传文件")
        self._files.itemChanged.connect(self._files_changed)
        root.addWidget(self._files, 1)
        self._summary = QLabel("尚未读取分类", self)
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)
        self._mode = QComboBox(self)
        for label, value in (
            ("仅更新原文（增量保留云端其他词条，不修改已有译文）", "orig_only"),
            ("仅安全导入译文（保留人工译文，不新增文件或词条）", "trans_safe"),
            ("仅强制导入译文（覆盖人工译文，不新增文件或词条）", "trans_force"),
            ("增量更新原文并安全导入译文", "both"),
        ):
            self._mode.addItem(label, value)
        self._mode.setAccessibleName("文件上传内容与覆盖策略")
        self._mode.setToolTip("译文导入需要 ParaTranz 项目管理员以上权限。")
        root.addWidget(self._mode)
        self._status = QLabel("正在读取当前工程的插件和分类…", self)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        ComponentStyle.apply_static(self._status, ComponentKind.NOTIFICATION)
        root.addWidget(self._status)
        self._details = QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setMaximumHeight(125)
        self._details.hide()
        root.addWidget(self._details)
        self._progress = QProgressBar(self)
        self._progress.hide()
        root.addWidget(self._progress)
        footer = QHBoxLayout()
        footer.addStretch()
        self._cancel = QPushButton("关闭", self)
        self._cancel.clicked.connect(self.reject)
        self._reload_button = QPushButton("重新加载", self)
        self._reload_button.clicked.connect(self._load_sources)
        self._upload_button = QPushButton("上传所选分类" if self._kind == "分类" else "上传所选文件", self)
        self._upload_button.clicked.connect(self._request_upload)
        for button in (self._cancel, self._reload_button, self._upload_button):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
            footer.addWidget(button)
        ComponentStyle.apply_state(self._upload_button, SemanticState.PRIMARY)
        root.addLayout(footer)

    def selection(self) -> Selection:
        sources = tuple(
            self._source_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self._source_list.count())
            if self._source_list.item(index).checkState() == Qt.CheckState.Checked
        )
        files = tuple(
            self._files.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)
            for index in range(self._files.topLevelItemCount())
            if self._files.topLevelItem(index).checkState(0) == Qt.CheckState.Checked
        )
        return Selection(sources, files, translation_mode=self._mode.currentData())

    def _load_sources(self) -> None:
        if self._worker is None:
            self._start_worker("load", self._load_preview)

    def _render_sources(self, sources: tuple[SourceOption, ...]) -> None:
        self._sources = sources
        with QSignalBlocker(self._source_list):
            self._source_list.clear()
            for source in sources:
                item = QListWidgetItem(source.label, self._source_list)
                item.setData(Qt.ItemDataRole.UserRole, source.source_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
        self._sources_changed()
        self._set_status(
            "选择插件和分类后，点击“上传所选分类”。" if sources else "当前工程没有可分类上传的插件。",
            SemanticState.INFO,
        )

    def _sources_changed(self, *_args) -> None:
        selected_sources = set(self.selection().source_ids)
        with QSignalBlocker(self._files):
            self._files.clear()
            for source in self._sources or ():
                if source.source_id not in selected_sources:
                    continue
                for file in source.files:
                    item = QTreeWidgetItem(self._files, [file.path, str(file.count)])
                    item.setData(0, Qt.ItemDataRole.UserRole, file.file_key)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    checked = self._file_selected.get(file.file_key, True)
                    item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self._selection_changed()

    def _files_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self._file_selected[item.data(0, Qt.ItemDataRole.UserRole)] = item.checkState(0) == Qt.CheckState.Checked
            self._selection_changed()

    def _selection_changed(self, *_args) -> None:
        selection = self.selection()
        selected = set(selection.file_keys)
        count = sum(file.count for source in self._sources or () for file in source.files if file.file_key in selected)
        self._summary.setText(
            f"已选择 {len(selection.source_ids)} 个插件、{len(selected)} 个{self._file_label}，共 {count} 条词条。"
        )
        if self._worker is None:
            self._details.hide()
            self._set_status("选择至少一个插件和分类文件后即可上传，云端核验将在后台自动完成。", SemanticState.INFO)
        self._sync_controls()

    def _request_upload(self) -> None:
        if self._worker is not None or self._sources is None:
            return
        selection = self.selection()
        if not selection.source_ids or not selection.file_keys:
            return
        self._start_worker("upload", lambda progress, cancel: self._upload(selection, progress, cancel))

    def _start_worker(self, phase: str, operation: Callable) -> None:
        self._phase = phase
        self._cancel_event = Event()
        self._outcome = self._error = None
        self._details.hide()
        self._progress.setRange(0, 0)
        self._progress.setFormat("处理中…")
        self._progress.show()
        self._set_status(
            {"load": "正在读取工程分类…", "upload": "正在读取云端并核验所选分类…"}[phase],
            SemanticState.INFO,
        )
        mailbox = self._progress_mailbox = LatestProgress()
        cancel_event = self._cancel_event
        worker = ApiWorker(lambda: operation(mailbox.publish, cancel_event), route_http_errors=False)
        self._worker = worker
        worker.result.connect(lambda outcome: self._received(outcome, mailbox))
        worker.error.connect(lambda message: self._failed(message, mailbox))
        worker.finished.connect(lambda: self._finished(mailbox))
        self._progress_timer.start()
        self._sync_controls()
        try:
            worker.start()
        except Exception as exc:
            self._error = str(exc)
            self._finished(mailbox)

    def _received(self, outcome, mailbox: LatestProgress) -> None:
        if mailbox is self._progress_mailbox:
            self._outcome = outcome

    def _failed(self, message: str, mailbox: LatestProgress) -> None:
        if mailbox is self._progress_mailbox:
            self._error = message

    def _finished(self, mailbox: LatestProgress) -> None:
        if mailbox is not self._progress_mailbox:
            return
        self._progress_timer.stop()
        update = mailbox.finish()
        if update is not None:
            self._render_progress(*update)
        self._progress_mailbox = None
        phase, outcome = self._phase, self._outcome
        self._worker = None
        self._progress.hide()
        if self._error is not None:
            action = "读取工程分类" if phase == "load" else "分类上传"
            retry = "请重新加载。" if phase == "load" else "可再次点击上传；结果待核实的写入不会自动重发。"
            self._set_status(f"{action}失败：{self._error}。{retry}", SemanticState.ERROR)
        elif self._cancel_event.is_set() and phase == "load":
            self._set_status("已取消读取分类，尚未上传内容。可重新加载。", SemanticState.INFO)
        elif outcome is None:
            self._set_status("操作未返回结果，请重试；上传前会重新核验云端。", SemanticState.ERROR)
        elif phase == "load":
            self._render_sources(tuple(outcome))
        else:
            self._render_receipt(outcome)
        self._phase = ""
        self._outcome = None
        self._sync_controls()

    def _render_receipt(self, receipt: UploadReceipt) -> None:
        cancelled = self._cancel_event.is_set() or receipt.cancelled_count > 0
        unfinished = receipt.failed_count or receipt.pending_count or cancelled
        outcome = "已取消" if cancelled else "未全部完成" if unfinished else "完成"
        self._set_status(
            f"{self._kind}上传{outcome}（按文件计数）：成功 {receipt.success_count}，"
            f"失败或待核实 {receipt.failed_count}，取消 {receipt.cancelled_count}，待处理 {receipt.pending_count}。"
            + ("请查看文件结果，可再次点击上传；结果待核实的写入不会自动重发。" if unfinished else ""),
            SemanticState.WARNING if unfinished else SemanticState.SUCCESS,
        )
        self._set_details(receipt.details)

    def _set_details(self, details: tuple[str, ...]) -> None:
        parts, remaining, remaining_lines, consumed = [], 32_000, 200, 0
        for detail in details:
            if not remaining or not remaining_lines:
                break
            lines = detail[:remaining].splitlines() or [""]
            parts.extend(lines[:remaining_lines])
            if len(detail) > remaining or len(lines) > remaining_lines:
                break
            remaining = max(0, remaining - len(detail) - 1)
            remaining_lines -= len(lines)
            consumed += 1
        if consumed < len(details):
            parts.append("…详情过多，已截断显示；统计包含全部结果。")
        self._details.setPlainText("\n".join(parts))
        self._details.setVisible(bool(parts))

    def _consume_progress(self) -> None:
        mailbox = self._progress_mailbox
        update = mailbox.take() if mailbox is not None else None
        if update is not None:
            self._render_progress(*update)

    def _render_progress(self, current: int, total: int, message: str) -> None:
        self._progress.setRange(0, max(total, 0))
        self._progress.setValue(max(0, min(current, total)))
        self._progress.setFormat("已处理 %v/%m（%p%）" if total > 0 else "处理中…")
        if message and not self._cancel_event.is_set():
            self._set_status(message, SemanticState.INFO)

    def _sync_controls(self) -> None:
        busy = self._worker is not None
        for widget in (self._source_list, self._files):
            widget.setEnabled(not busy)
        selection = self.selection()
        has_selection = bool(selection.source_ids and selection.file_keys)
        self._reload_button.setVisible(self._sources is None and not busy)
        self._reload_button.setEnabled(not busy)
        self._upload_button.setEnabled(not busy and self._sources is not None and has_selection)
        self._mode.setEnabled(not busy)
        self._cancel.setText("取消任务" if busy else "关闭")
        self._cancel.setEnabled(not busy or not self._cancel_event.is_set())

    def _set_status(self, message: str, state: SemanticState) -> None:
        if message != self._status.text():
            self._status.setText(message)
        if state != self._status_state:
            self._status_state = state
            ComponentStyle.apply_state(self._status, state)

    def done(self, result: int) -> None:
        if self._worker is not None:
            self._cancel_event.set()
            self._set_status("正在取消；等待当前请求结束后将显示已完成和未完成的结果。", SemanticState.INFO)
            self._sync_controls()
        else:
            super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
