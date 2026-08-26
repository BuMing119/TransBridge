"""LLM response and workflow diagnostic log viewer."""

from __future__ import annotations

import os

from PyQt6.QtCore import Qt
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


class _LLMLogViewer(QWidget):
    """Display one explicitly selected log without eagerly loading every file."""

    _MAX_VISIBLE_BYTES = 2 * 1024 * 1024
    _MAX_VISIBLE_BLOCKS = 30_000

    def __init__(self, log_dir: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._log_dir = log_dir
        self._known_files: tuple[str, ...] = ()
        self._active_path = ""

        self.setWindowTitle(f"LLM 运行日志 — {os.path.basename(log_dir)}")
        self.resize(900, 640)
        self._init_ui()
        self._refresh_index()

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

        text_edit = QPlainTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 9))
        text_edit.document().setMaximumBlockCount(self._MAX_VISIBLE_BLOCKS)
        self._text_edit = text_edit
        layout.addWidget(text_edit, 1)

    def _scan_and_refresh(self) -> None:
        """Refresh the file list and reload only the selected file."""
        self._refresh_index()
        self._load_selected(force=True)

    def _refresh_index(self) -> None:
        if not os.path.isdir(self._log_dir):
            self._show_empty_state("日志目录不存在")
            return

        try:
            files = tuple(
                sorted(
                    entry.name for entry in os.scandir(self._log_dir) if entry.is_file() and entry.name.endswith(".log")
                )
            )
        except OSError as exc:
            self._show_empty_state(f"无法读取日志目录：{exc}")
            return

        selected_path = self._log_selector.currentData(Qt.ItemDataRole.UserRole)
        if files != self._known_files:
            self._known_files = files
            self._log_selector.blockSignals(True)
            self._log_selector.clear()
            selected_index = -1
            for index, filename in enumerate(files):
                path = os.path.join(self._log_dir, filename)
                self._log_selector.addItem(f"{self._tab_label(filename)}  —  {filename}", path)
                if path == selected_path:
                    selected_index = index
            if selected_index < 0 and files:
                selected_index = files.index("workflow.log") if "workflow.log" in files else len(files) - 1
            self._log_selector.setCurrentIndex(selected_index)
            self._log_selector.blockSignals(False)

        self._count_label.setText(f"{len(files)} 个日志")
        self._log_selector.setEnabled(bool(files))
        if files:
            self._load_selected(force=True)
        else:
            self._show_empty_state("暂无日志，点击“刷新列表”重新扫描")

    def _on_selection_changed(self, _index: int) -> None:
        self._load_selected(force=True)

    def _load_selected(self, *, force: bool = False) -> None:
        path = self._log_selector.currentData(Qt.ItemDataRole.UserRole)
        if not path or (path == self._active_path and not force):
            return
        self._active_path = str(path)
        try:
            content = self._read_visible_text(self._active_path)
        except OSError as exc:
            self._text_edit.setPlainText(f"无法读取日志：{exc}")
            return
        self._text_edit.setPlainText(content)
        scrollbar = self._text_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    @classmethod
    def _read_visible_text(cls, path: str) -> str:
        size = os.path.getsize(path)
        start = max(0, size - cls._MAX_VISIBLE_BYTES)
        with open(path, "rb") as stream:
            stream.seek(start)
            data = stream.read(cls._MAX_VISIBLE_BYTES)
        if start:
            newline = data.find(b"\n")
            if newline >= 0:
                data = data[newline + 1 :]
            prefix = f"[日志过大，仅显示最后 {cls._MAX_VISIBLE_BYTES // (1024 * 1024)} MiB]\n\n"
        else:
            prefix = ""
        return prefix + data.decode("utf-8", errors="replace")

    def _show_empty_state(self, message: str) -> None:
        self._known_files = ()
        self._active_path = ""
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
        """Compatibility hook: this viewer is intentionally manual-only."""

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt compatibility
        event.accept()
