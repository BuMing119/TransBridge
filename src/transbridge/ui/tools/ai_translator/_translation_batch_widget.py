"""Per-batch streaming log widget used by the translation progress window."""

from __future__ import annotations

from collections import deque

from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import QFrame, QLabel, QTextEdit, QVBoxLayout


class _BatchWidget(QFrame):
    """Single-batch log that collapses to a summary after completion."""

    def __init__(self, batch_idx: int, parent=None):
        super().__init__(parent)
        self._batch_idx = batch_idx
        self._phase = "init"
        self._title = f"任务{batch_idx}"
        self._footer_lines: list[str] = []
        self._trans_cursors: deque = deque()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)
        self._header_label = QLabel(self._title)
        self._header_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        layout.addWidget(self._header_label)
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 8))
        self._text.setFixedHeight(160)
        layout.addWidget(self._text)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 4px; margin: 2px; }")

    def append_line(self, line: str) -> None:
        stripped = line.strip()
        if "开始翻译：" in line:
            self._phase = "header"
            return
        if self._phase == "header":
            clean = line.lstrip("\n")
            summary = clean.strip()
            if summary.startswith("任务") and "：" in summary:
                self._title = summary
                self._header_label.setText(summary)
            elif summary == "-----------------------":
                self._phase = "trans"
            return
        if self._phase == "trans":
            if stripped == "-----------------------":
                self._phase = "footer"
                return
            if " -> " in line:
                self._text.append(line)
                document = self._text.document()
                block = document.findBlockByNumber(document.blockCount() - 1)
                cursor = QTextCursor(block)
                self._trans_cursors.append(cursor)
                if len(self._trans_cursors) > 10:
                    oldest = self._trans_cursors.popleft()
                    oldest.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    oldest.movePosition(
                        QTextCursor.MoveOperation.Down,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    oldest.removeSelectedText()
            else:
                self._text.append(line)
            return
        if self._phase == "footer":
            if stripped == "已完成：":
                self._footer_lines = []
                return
            self._footer_lines.append(stripped)
            self._text.append(line)
            if stripped.startswith("新增术语数："):
                self._phase = "done"
                self._collapse()
            return
        self._text.append(line)

    def _collapse(self):
        total_time = ""
        entries_count = ""
        new_terms = ""
        for line in self._footer_lines:
            if line.startswith("总时长："):
                total_time = line.replace("总时长：", "").strip()
            elif line.startswith("翻译词条数："):
                entries_count = line.replace("翻译词条数：", "").strip()
            elif line.startswith("新增术语数："):
                new_terms = line.replace("新增术语数：", "").strip()
        summary = f"✅ {self._title}"
        parts = []
        if entries_count:
            parts.append(f"{entries_count} 条")
        if total_time:
            parts.append(total_time)
        if new_terms and new_terms != "0":
            parts.append(f"新增术语 {new_terms}")
        if parts:
            summary += " — " + " | ".join(parts)
        self._header_label.setText(summary)
        self._text.hide()
        self.setStyleSheet(
            "QFrame { border: 1px solid #bdbdbd; border-radius: 4px; "
            "margin: 2px; background: #f5f5f5; }"
            "QLabel { color: #424242; }"
        )

    def force_collapse(self):
        if self._phase != "done":
            self._header_label.setText(f"⚠ {self._title}（未完成）")
            self._text.hide()
            self._phase = "done"
            self.setStyleSheet(
                "QFrame { border: 1px solid #ffe082; border-radius: 4px; margin: 2px; background: #fffde7; }"
            )
