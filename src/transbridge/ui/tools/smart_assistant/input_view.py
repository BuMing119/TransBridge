from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .quick_actions import QuickActionsChips


class ChatInputView:
    """Builds and owns the chat toolbar/editor while emitting narrow intents."""

    def __init__(
        self,
        *,
        set_input: Callable[[str], None],
        select_skill: Callable[[str], None],
        upload: Callable[[], None],
        clear: Callable[[], None],
        send: Callable[[], None],
        toggle_auto: Callable[[bool], None],
        auto_mode: bool,
    ) -> None:
        self._set_input = set_input
        self._select_skill = select_skill
        self._upload = upload
        self._clear = clear
        self._send = send
        self._toggle_auto = toggle_auto
        self._auto_mode = auto_mode
        self.input: QTextEdit | None = None
        self.upload_label: QLabel | None = None
        self.send_button: QPushButton | None = None
        self.auto_checkbox: QCheckBox | None = None
        self._closed = False

    def build_toolbar(self, layout: QVBoxLayout) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.setContentsMargins(4, 2, 4, 2)
        chips = QuickActionsChips()
        chips.action_clicked.connect(self._set_input)
        chips.skill_triggered.connect(self._select_skill)
        toolbar.addWidget(chips)
        self.upload_label = QLabel("")
        self.upload_label.setStyleSheet("color: #888; font-size: 11px;")
        upload_button = QPushButton("上传")
        upload_button.setToolTip("上传纠错表/术语参考/风格指南（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_button.setStyleSheet(
            "QPushButton { background-color: #f5f5f5; border: 1px solid #ddd;"
            " border-radius: 12px; padding: 3px 10px; font-size: 11px; color: #666; }"
            "QPushButton:hover { background-color: #e8e8e8; }"
        )
        upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_button.clicked.connect(self._upload)
        toolbar.addWidget(upload_button)
        toolbar.addWidget(self.upload_label)
        layout.addLayout(toolbar)

    def build_editor(self, layout: QVBoxLayout, event_filter) -> None:
        editor = QTextEdit()
        editor.setAccessibleName("消息输入框")
        editor.setMaximumHeight(100)
        editor.setMinimumHeight(40)
        editor.document().setMaximumBlockCount(500)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor.setPlaceholderText("输入消息，Ctrl+Enter 发送  |  输入 /obs 切换观测信息显示")
        editor.setStyleSheet(
            "QTextEdit { border: 1px solid #ddd; border-radius: 8px; padding: 6px 10px;"
            " font-size: 13px; background: #fff; margin: 0 4px; }"
            "QTextEdit:focus { border-color: #4CAF50; }"
        )
        editor.installEventFilter(event_filter)
        layout.addWidget(editor)
        self.input = editor

        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 2)
        row.setSpacing(8)
        clear_button = QPushButton("清空对话")
        clear_button.setStyleSheet(
            "QPushButton { background-color: #f5f5f5; border: 1px solid #ddd;"
            " border-radius: 8px; padding: 5px 12px; font-size: 12px; color: #666; }"
            "QPushButton:hover { background-color: #e8e8e8; }"
        )
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(self._clear)
        row.addWidget(clear_button)

        checkbox = QCheckBox("Auto")
        checkbox.setToolTip("自动模式：LLM返回工具/计划时直接执行，不显示确认卡片（admin级工具始终确认）")
        checkbox.setChecked(self._auto_mode)
        checkbox.toggled.connect(self._toggle_auto)
        checkbox.setStyleSheet(
            "QCheckBox { font-size: 11px; color: #888; spacing: 4px; }QCheckBox:hover { color: #555; }"
        )
        self.auto_checkbox = checkbox
        row.addWidget(checkbox)
        row.addStretch()

        send_button = QPushButton("发送")
        send_button.setAccessibleName("发送消息按钮")
        send_button.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; border: none;"
            " border-radius: 8px; padding: 5px 18px; font-size: 13px; font-weight: bold; }"
            "QPushButton:hover { background-color: #43A047; }"
            "QPushButton:pressed { background-color: #388E3C; }"
            "QPushButton:disabled { background-color: #A5D6A7; }"
        )
        send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        send_button.clicked.connect(self._send)
        send_button.setEnabled(False)
        editor.textChanged.connect(lambda: send_button.setEnabled(bool(editor.toPlainText().strip())))
        self.send_button = send_button
        row.addWidget(send_button)
        layout.addLayout(row)

    def set_text(self, text: str) -> None:
        if not self._closed and self.input is not None:
            self.input.setPlainText(text)
            self.input.setFocus()

    def close(self) -> None:
        self._closed = True


__all__ = ["ChatInputView"]


class UploadBinding:
    """Owns file selection/parsing without access to the application context."""

    def __init__(self, *, parent, documents: dict[str, object], notify, max_bytes: int) -> None:
        self._parent = parent
        self._documents = documents
        self._notify = notify
        self._max_bytes = max_bytes
        self._closed = False

    def select_files(self, label: QLabel | None) -> None:
        if self._closed:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self._parent,
            "选择参考文件",
            "",
            "文档 (*.xlsx *.csv *.md *.txt *.json *.pdf *.docx *.zip);;全部 (*.*)",
        )
        if not paths:
            return
        from transbridge.smart_assistant.file_parser import FileParser

        for raw_path in paths:
            path = Path(raw_path)
            parser = FileParser.get_parser(path)
            if parser is None:
                self._notify(f"不支持的文件格式: {path.name}")
                continue
            size = os.path.getsize(str(path))
            if size > self._max_bytes:
                self._notify(
                    f"文件过大 ({size / (1024 * 1024):.1f} MB)，已超过 "
                    f"{self._max_bytes / (1024 * 1024):.0f} MB 上限: {path.name}"
                )
                continue
            try:
                self._documents[path.name] = parser.parse(path)
            except Exception as error:
                message = str(error).replace(str(path), path.name)
                self._notify(f"解析文件失败: {path.name} — {message}")
        if label is not None:
            names = ", ".join(self._documents)
            label.setText(f"已上传: {names}" if names else "")

    def close(self) -> None:
        self._closed = True


__all__.append("UploadBinding")


class ChatInputActions:
    """Handles input-toolbar intents through public backend ports."""

    def __init__(
        self,
        *,
        chat_facade,
        orchestrator,
        controller,
        auto_mode: bool,
        notify: Callable[[str], None],
    ) -> None:
        self._chat_facade = chat_facade
        self._orchestrator = orchestrator
        self._controller = controller
        self._notify = notify
        self._observability_visible = False
        self._auto_mode = auto_mode

    @property
    def observability_visible(self) -> bool:
        return self._observability_visible

    def select_skill(self, skill_name: str) -> None:
        from transbridge.smart_assistant.skills import SkillExecutor, SkillRegistry

        spec = SkillRegistry.get(skill_name)
        if spec:
            SkillExecutor(self._chat_facade).execute(spec)

    def toggle_auto(self, checked: bool) -> None:
        self._auto_mode = checked
        self._orchestrator.auto_mode = checked
        self._controller.auto_mode = checked
        try:
            QSettings("TransBridge", "SmartAssistant").setValue("auto_mode", checked)
        except Exception:
            # Persistence is best effort; runtime state is already authoritative.
            pass

    def toggle_observability(self) -> None:
        self._observability_visible = not self._observability_visible
        if self._observability_visible:
            self._notify("[观测] Token 统计和工具调用记录已开启。输入 /obs 关闭。")
        else:
            self._notify("[观测] 观测信息已关闭。输入 /obs 重新开启。")


__all__.append("ChatInputActions")
