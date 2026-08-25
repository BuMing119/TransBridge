from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .quick_actions import QuickActionsChips
from .theme_support import (
    BUTTON_STRUCTURE_STYLE,
    CARD_STRUCTURE_STYLE,
    CHIP_STRUCTURE_STYLE,
    INPUT_STRUCTURE_STYLE,
    SmartAssistantTheme,
)


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
        theme: SmartAssistantTheme | None = None,
    ) -> None:
        self._set_input = set_input
        self._select_skill = select_skill
        self._upload = upload
        self._clear = clear
        self._send = send
        self._toggle_auto = toggle_auto
        self._auto_mode = auto_mode
        self._theme = theme or SmartAssistantTheme()
        self.input: QTextEdit | None = None
        self.upload_label: ElidedLabel | None = None
        self.send_button: QPushButton | None = None
        self.auto_checkbox: QPushButton | None = None
        self._chips: QuickActionsChips | None = None
        self._card: QFrame | None = None
        self._card_layout: QVBoxLayout | None = None
        self._action_layout: QHBoxLayout | None = None
        self._upload_button: QPushButton | None = None
        self._clear_button: QPushButton | None = None
        self._closed = False

    def build_toolbar(self, layout: QVBoxLayout) -> None:
        card = QFrame()
        card.setProperty("tbSurface", "card")
        card.setStyleSheet(CARD_STRUCTURE_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 10, 10, 8)
        card_layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.setContentsMargins(0, 0, 0, 0)
        chips = QuickActionsChips(theme=self._theme)
        chips.action_clicked.connect(self._set_input)
        chips.skill_triggered.connect(self._select_skill)
        toolbar.addWidget(chips, 1)
        self.upload_label = ElidedLabel("")
        self.upload_label.setAccessibleName("已上传参考文件")
        upload_button = QPushButton("附件")
        upload_button.setAccessibleName("上传参考文件")
        upload_button.setToolTip("上传纠错表/术语参考/风格指南（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_button.setStyleSheet(CHIP_STRUCTURE_STYLE)
        upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_button.clicked.connect(self._upload)
        toolbar.addWidget(upload_button)
        toolbar.addWidget(self.upload_label, 1)
        card_layout.addLayout(toolbar)
        layout.addWidget(card)
        self._chips = chips
        self._card = card
        self._card_layout = card_layout
        self._action_layout = toolbar
        self._upload_button = upload_button
        self.apply_theme(self._theme)

    def build_editor(self, layout: QVBoxLayout, event_filter) -> None:
        if self._card_layout is None or self._action_layout is None:
            raise RuntimeError("build_toolbar() must run before build_editor()")
        editor = QTextEdit()
        editor.setAccessibleName("消息输入框")
        editor.setMinimumHeight(96)
        editor.setMaximumHeight(112)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor.document().setMaximumBlockCount(500)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor.setPlaceholderText("输入消息，或输入 / 使用工具（Ctrl+Enter 发送）")
        editor.setStyleSheet(INPUT_STRUCTURE_STYLE)
        editor.installEventFilter(event_filter)
        self._card_layout.insertWidget(0, editor)
        self.input = editor

        clear_button = QPushButton()
        clear_button.setAccessibleName("清空对话")
        clear_button.setToolTip("清空对话")
        clear_button.setFixedSize(36, 32)
        clear_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(self._clear)
        self._action_layout.addWidget(clear_button)

        checkbox = QPushButton("Auto")
        checkbox.setAccessibleName("自动执行模式")
        checkbox.setToolTip("自动模式：LLM返回工具/计划时直接执行，不显示确认卡片（admin级工具始终确认）")
        checkbox.setCheckable(True)
        checkbox.setChecked(self._auto_mode)
        checkbox.toggled.connect(self._toggle_auto)
        self.auto_checkbox = checkbox
        self._action_layout.addWidget(checkbox)

        send_button = QPushButton("发送")
        send_button.setAccessibleName("发送消息按钮")
        send_button.setToolTip("发送消息")
        send_button.setFixedSize(76, 36)
        send_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        send_button.clicked.connect(self._send)
        send_button.setEnabled(False)
        editor.textChanged.connect(lambda: send_button.setEnabled(bool(editor.toPlainText().strip())))
        self.send_button = send_button
        self._clear_button = clear_button
        self._action_layout.addWidget(send_button)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        if self._chips is not None:
            self._chips.apply_theme(theme)
        if self._card is not None:
            theme.apply_surface(self._card)
        for widget in (self.upload_label, self._upload_button, self._clear_button, self.auto_checkbox):
            if widget is not None:
                theme.apply_semantic(widget, "muted", background=isinstance(widget, QPushButton))
        if self._upload_button is not None:
            self._upload_button.setIcon(tabler_icon(self._upload_button, "paperclip", 15))
        if self._clear_button is not None:
            self._clear_button.setIcon(tabler_icon(self._clear_button, "trash", 15))
        if self.input is not None:
            theme.apply_surface(self.input)
        if self.send_button is not None:
            theme.apply_accent(self.send_button)
            self.send_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            self.send_button.setIcon(tabler_icon(self.send_button, "send", 18, semantic="on-accent"))
            self.send_button.setIconSize(QSize(18, 18))

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

    def select_files(self, label: ElidedLabel | None) -> None:
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
            summary = f"已上传 {len(self._documents)} 个: {names}" if names else ""
            label.set_full_text(summary)
            label.setToolTip(names)
            label.setAccessibleDescription(summary)

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
