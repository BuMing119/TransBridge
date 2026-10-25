"""LLM response and workflow diagnostic log viewer."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.components import ElidedLabel

from .llm_log_reader import LogReadRequest, read_log_snapshot, read_log_tail
from .read_projection import LatestReadController


class _LLMLogViewer(QWidget):
    """Display one explicitly selected log without eagerly loading every file."""

    _MAX_VISIBLE_BYTES = 2 * 1024 * 1024
    _MAX_VISIBLE_BLOCKS = 30_000

    def __init__(self, log_dir: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._log_dir = log_dir
        self._known_files: tuple[str, ...] = ()
        self._active_path = ""
        self._active_stamp = None
        self._suspended = False
        self._reader = LatestReadController(read_log_snapshot, self)
        self._reader.ready.connect(self._loaded)
        self._reader.failed.connect(self._read_failed)

        self.setWindowTitle(f"LLM 运行日志 — {os.path.basename(log_dir)}")
        self.resize(900, 640)
        self._init_ui()
        self._refresh_index()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_index)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        path_lbl = ElidedLabel(self._log_dir)
        path_lbl.setAccessibleName("LLM 日志目录")
        path_lbl.setAccessibleDescription(self._log_dir)
        path_lbl.setToolTip(self._log_dir)
        path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._path_label = path_lbl
        toolbar.addWidget(path_lbl, 1)

        refresh_btn = QPushButton("刷新列表")
        refresh_btn.setFixedWidth(76)
        refresh_btn.clicked.connect(self._scan_and_refresh)
        self._refresh_btn = refresh_btn
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("日志："))
        selector = QComboBox()
        selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        selector.setMinimumContentsLength(24)
        selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        selector.setAccessibleName("日志文件选择")
        selector.currentIndexChanged.connect(self._on_selection_changed)
        self._log_selector = selector
        selector_row.addWidget(selector, 1)

        count_label = QLabel("0 个日志")
        self._count_label = count_label
        selector_row.addWidget(count_label)
        layout.addLayout(selector_row)

        self._refresh_status = QLabel("自动刷新 · 上翻阅读时暂停更新正文")
        self._refresh_status.setWordWrap(True)
        self._refresh_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._refresh_status)

        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 9))
        text_edit.document().setMaximumBlockCount(self._MAX_VISIBLE_BLOCKS)
        self._text_edit = text_edit
        layout.addWidget(text_edit, 1)

    def _scan_and_refresh(self) -> None:
        """Refresh the file list and reload only the selected file."""
        self._request_read(scan=True, force=True)

    def _refresh_index(self) -> None:
        # A timer must not continuously supersede a slow disk read.
        if not self._reader.busy:
            self._request_read(scan=True)

    def _request_read(self, *, scan=False, force=False):
        if self._suspended:
            return
        scrollbar = self._text_edit.verticalScrollBar()
        self._reader.submit(
            LogReadRequest(
                self._log_dir,
                str(self._log_selector.currentData(Qt.ItemDataRole.UserRole) or ""),
                self._active_path,
                self._active_stamp,
                scrollbar.value() >= scrollbar.maximum() - 1,
                force,
                scan,
                self._read_visible_text,
            )
        )

    @pyqtSlot(object, object)
    def _loaded(self, request, snapshot):
        if self._suspended:
            return
        if snapshot.empty:
            self._show_empty_state(snapshot.message)
            return
        files = snapshot.files
        if files is not None and files != self._known_files:
            self._known_files = files
            self._log_selector.blockSignals(True)
            self._log_selector.clear()
            selected_index = -1
            for index, filename in enumerate(files):
                path = os.path.join(self._log_dir, filename)
                self._log_selector.addItem(f"{self._tab_label(filename)}  —  {filename}", path)
                if path == snapshot.path:
                    selected_index = index
            self._log_selector.setCurrentIndex(selected_index)
            self._log_selector.blockSignals(False)
        self._count_label.setText(f"{len(self._known_files)} 个日志")
        self._log_selector.setEnabled(bool(self._known_files))
        if snapshot.message:
            self._refresh_status.setText(snapshot.message)
            if snapshot.message.startswith("无法读取"):
                self._active_stamp = None
            return
        if snapshot.content is None:
            return
        scrollbar = self._text_edit.verticalScrollBar()
        previous_scroll = scrollbar.value()
        following = previous_scroll >= scrollbar.maximum() - 1
        same_path = snapshot.path == self._active_path
        # A user may scroll upwards while the background read is in flight.
        if same_path and not request.force and not following:
            self._refresh_status.setText("有新日志 · 正在保留阅读位置，滚动到底部或点击刷新以更新")
            return
        self._active_path, self._active_stamp = snapshot.path, snapshot.stamp
        self._refresh_status.setText("自动刷新 · 上翻阅读时暂停更新正文")
        self._text_edit.setPlainText(snapshot.content)
        scrollbar.setValue(scrollbar.maximum() if following or not same_path else previous_scroll)

    @pyqtSlot(str)
    def _read_failed(self, error):
        self._refresh_status.setText(f"无法读取日志：{error}")

    def _on_selection_changed(self, _index: int) -> None:
        self._load_selected(force=True)

    def _load_selected(self, *, force: bool = False) -> None:
        self._request_read(force=force)

    @classmethod
    def _read_visible_text(cls, path: str) -> str:
        return read_log_tail(path, cls._MAX_VISIBLE_BYTES)

    def _show_empty_state(self, message: str) -> None:
        self._known_files = ()
        self._active_path = ""
        self._active_stamp = None
        self._log_selector.blockSignals(True)
        self._log_selector.clear()
        self._log_selector.blockSignals(False)
        self._log_selector.setEnabled(False)
        self._count_label.setText("0 个日志")
        self._text_edit.setPlainText(message)

    @staticmethod
    def _tab_label(filename: str) -> str:
        stem = filename.removesuffix(".log")
        if stem.startswith("batch_"):
            number = stem.removeprefix("batch_").lstrip("0") or "1"
            return f"翻译批次 {number}"
        if stem.startswith("stage_"):
            phase = stem.removeprefix("stage_")
            labels = {
                "terms": "术语进度",
                "detect": "检测",
                "refine": "修复",
                "polish": "润色",
                "arbitrate": "裁决",
                "execute": "汇总",
            }
            return labels.get(phase, phase)
        if stem == "term_llm":
            return "术语抽取对话"
        if stem.startswith("term_llm_"):
            number = stem.removeprefix("term_llm_").lstrip("0") or "1"
            return f"术语调用 {number}"
        if stem.startswith("translation_call_"):
            number = stem.removeprefix("translation_call_").lstrip("0") or "1"
            return f"翻译调用 {number}"
        if stem.startswith("proofread_call_"):
            number = stem.removeprefix("proofread_call_").lstrip("0") or "1"
            return f"校改调用 {number}"
        if stem.startswith("llm_call_"):
            number = stem.removeprefix("llm_call_").lstrip("0") or "1"
            return f"LLM 调用 {number}"
        if stem == "workflow":
            return "流程诊断"
        return stem

    def stop_auto_refresh(self) -> None:
        self._refresh_timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt compatibility
        was_suspended = self._suspended
        self._suspended = False
        self._refresh_timer.start()
        if was_suspended:
            self._request_read(scan=True)
        else:
            self._refresh_index()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt compatibility
        self.stop_auto_refresh()
        self._suspended = True
        self._reader.invalidate()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt compatibility
        self.stop_auto_refresh()
        self._suspended = True
        self._reader.invalidate()
        event.accept()
