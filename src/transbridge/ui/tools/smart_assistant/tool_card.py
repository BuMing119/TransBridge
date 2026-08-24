from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel, reserve_text_width

from .theme_support import BUTTON_STRUCTURE_STYLE, CARD_STRUCTURE_STYLE, SmartAssistantTheme


class ToolCard(QWidget):
    executed = pyqtSignal(dict)
    ignored = pyqtSignal(dict)

    def __init__(self, step: dict, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._step = step
        self._theme = theme or SmartAssistantTheme()
        self.setObjectName("ToolCard")
        self.setProperty("tbSurface", "card")
        self.setStyleSheet(CARD_STRUCTURE_STYLE)
        tool_name = step.get("tool", "?")
        self.setAccessibleName(f"工具确认：{tool_name}")
        self.setAccessibleDescription("等待选择执行或忽略")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        title_text = f"[Tool] {tool_name}"
        self._title = ElidedLabel(title_text)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setToolTip(title_text)
        self._title.setAccessibleDescription(title_text)
        layout.addWidget(self._title)
        self._args_label: QLabel | None = None
        args = step.get("args", {})
        if args:
            self._args_label = QLabel("参数: " + ", ".join(f"{key}={value}" for key, value in args.items()))
            self._args_label.setTextFormat(Qt.TextFormat.PlainText)
            self._args_label.setWordWrap(True)
            layout.addWidget(self._args_label)
        row = QHBoxLayout()
        self._exec_btn = QPushButton("执行")
        self._exec_btn.setAccessibleName(f"执行工具 {tool_name}")
        self._ignore_btn = QPushButton("忽略")
        self._ignore_btn.setAccessibleName(f"忽略工具 {tool_name}")
        for button in (self._exec_btn, self._ignore_btn):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        reserve_text_width(self._exec_btn, ("执行", "执行中..."))
        self._exec_btn.clicked.connect(self._on_execute)
        self._ignore_btn.clicked.connect(self._on_ignore)
        row.addStretch()
        row.addWidget(self._exec_btn)
        row.addWidget(self._ignore_btn)
        layout.addLayout(row)
        self._result_label = ElidedLabel("")
        self._result_label.setTextFormat(Qt.TextFormat.PlainText)
        self._result_label.setVisible(False)
        layout.addWidget(self._result_label)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_semantic(self, "warning", background=True)
        theme.apply_semantic(self._title, "default")
        if self._args_label is not None:
            theme.apply_semantic(self._args_label, "muted")
        theme.apply_semantic(self._exec_btn, "success", background=True)
        theme.apply_semantic(self._ignore_btn, "muted", background=True)
        state = self._result_label.property("tbSemanticState") or "muted"
        theme.apply_semantic(self._result_label, str(state))

    def set_result(self, success: bool, message: str) -> None:
        state = "success" if success else "error"
        result_text = f"{'[OK]' if success else '[FAIL]'} {message}"
        self._result_label.set_full_text(result_text)
        self._result_label.setToolTip(result_text)
        self._result_label.setAccessibleDescription(result_text)
        self._result_label.setVisible(True)
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)
        self._theme.mark_status(self._result_label, message, state)
        self._theme.apply_semantic(self._result_label, state)
        self.setAccessibleDescription(f"工具执行{message}")

    def _on_execute(self) -> None:
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.setAccessibleDescription("工具执行中")
        self.executed.emit(self._step)

    def _on_ignore(self) -> None:
        self._exec_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)
        self.setAccessibleDescription("工具已忽略")
        self.ignored.emit(self._step)


class BatchToolCard(QWidget):
    all_executed = pyqtSignal(list)
    all_ignored = pyqtSignal(list)

    def __init__(self, steps: list, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._steps = steps
        self._theme = theme or SmartAssistantTheme()
        self.setObjectName("BatchToolCard")
        self.setProperty("tbSurface", "card")
        self.setStyleSheet(CARD_STRUCTURE_STYLE)
        self.setAccessibleName(f"批量工具确认，共 {len(steps)} 步")
        self.setAccessibleDescription("等待选择全部执行或跳过")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        self._labels = [QLabel(f"[Tool] 批量执行（共 {len(steps)} 步）")]
        layout.addWidget(self._labels[0])
        for step in steps:
            label = QLabel(f"  · {step.get('tool', '?')}")
            label.setTextFormat(Qt.TextFormat.PlainText)
            self._labels.append(label)
            layout.addWidget(label)
        row = QHBoxLayout()
        self._exec_btn = QPushButton("全部执行")
        self._skip_btn = QPushButton("跳过")
        for button in (self._exec_btn, self._skip_btn):
            button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exec_btn.setAccessibleName("全部执行工具")
        self._skip_btn.setAccessibleName("跳过全部工具")
        self._exec_btn.clicked.connect(self._on_execute)
        self._skip_btn.clicked.connect(self._on_ignore)
        row.addStretch()
        row.addWidget(self._exec_btn)
        row.addWidget(self._skip_btn)
        layout.addLayout(row)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_semantic(self, "warning", background=True)
        for label in self._labels:
            theme.apply_semantic(label, "default")
        theme.apply_semantic(self._exec_btn, "success", background=True)
        theme.apply_semantic(self._skip_btn, "muted", background=True)

    def _on_execute(self) -> None:
        self._exec_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self._exec_btn.setText("执行中...")
        self.setAccessibleDescription("批量工具执行中")
        self.all_executed.emit(self._steps)

    def _on_ignore(self) -> None:
        self._exec_btn.setEnabled(False)
        self._skip_btn.setEnabled(False)
        self.setAccessibleDescription("批量工具已跳过")
        self.all_ignored.emit(self._steps)


__all__ = ["BatchToolCard", "ToolCard"]
