from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMenu,
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
        self.auto_checkbox: QAction | None = None
        self._card: QFrame | None = None
        self._card_layout: QVBoxLayout | None = None
        self._action_layout: QHBoxLayout | None = None
        self._upload_button: QPushButton | None = None
        self._more_button: QPushButton | None = None
        self._closed = False
        self._running = False
        self._stop = lambda: None
        self._details = lambda: None

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
        self.upload_label = ElidedLabel("")
        self.upload_label.setAccessibleName("已上传参考文件")
        self.upload_label.hide()
        upload_button = QPushButton("附件")
        upload_button.setAccessibleName("上传参考文件")
        upload_button.setToolTip("上传纠错表/术语参考/风格指南（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_button.setStyleSheet(CHIP_STRUCTURE_STYLE)
        upload_button.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_button.clicked.connect(self._upload)
        toolbar.addWidget(upload_button)
        card_layout.addWidget(self.upload_label)
        card_layout.addLayout(toolbar)
        layout.addWidget(card)
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
        editor.setMinimumHeight(80)
        editor.setMaximumHeight(88)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor.document().setMaximumBlockCount(500)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        editor.setPlaceholderText("输入要求，或补充当前任务…（Ctrl+Enter 发送）")
        editor.setStyleSheet(INPUT_STRUCTURE_STYLE)
        editor.installEventFilter(event_filter)
        self._card_layout.insertWidget(0, editor)
        self.input = editor

        checkbox = QAction("自动执行常规操作", self._card)
        checkbox.setToolTip("高权限操作仍需确认")
        checkbox.setCheckable(True)
        checkbox.setChecked(self._auto_mode)
        checkbox.toggled.connect(self._toggle_auto)
        self.auto_checkbox = checkbox
        self._more_button = QPushButton("更多")
        self._more_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._more_button.clicked.connect(self._show_more)
        self._action_layout.addWidget(self._more_button)
        self._action_layout.addStretch(1)

        send_button = QPushButton("发送")
        send_button.setAccessibleName("发送消息按钮")
        send_button.setToolTip("发送消息")
        send_button.setFixedSize(76, 36)
        send_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        send_button.clicked.connect(lambda: self._stop() if self._running else self._send())
        send_button.setEnabled(False)
        editor.textChanged.connect(self._update_primary)
        self.send_button = send_button
        self._action_layout.addWidget(send_button)
        self.apply_theme(self._theme)

    def set_running(self, running: bool) -> None:
        if self._running == running:
            return
        self._running = running
        self._update_primary()

    def _update_primary(self) -> None:
        if self.send_button is None:
            return
        label = "停止" if self._running else "发送"
        self.send_button.setText(label)
        self.send_button.setAccessibleName("停止当前生成" if self._running else "发送消息按钮")
        self.send_button.setToolTip("停止推进，保留已完成的修改" if self._running else "发送消息")
        self.send_button.setIcon(
            tabler_icon(self.send_button, "x" if self._running else "send", 18, semantic="on-accent")
        )
        self.send_button.setEnabled(self._running or bool(self.input and self.input.toPlainText().strip()))

    def configure_actions(self, *, stop, details) -> None:
        self._stop, self._details = stop, details

    def _show_more(self) -> None:
        menu = QMenu(self._more_button)
        for label, prompt, _icon in QuickActionsChips._ACTIONS:
            menu.addAction(label, lambda value=prompt: self._set_input(value))
        skills = menu.addMenu("技能")
        from transbridge.smart_assistant.skills import SkillRegistry

        for spec in SkillRegistry.list_all():
            skills.addAction(spec.display_name, lambda value=spec.name: self._select_skill(value))
        if not skills.actions():
            skills.addAction("暂无可用技能").setEnabled(False)
        menu.addSeparator()
        menu.addAction(self.auto_checkbox)
        menu.addAction("任务与会话详情", self._details)
        menu.addSeparator()
        menu.addAction("清空对话", self._clear)
        menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        if self._card is not None:
            theme.apply_surface(self._card)
        for widget in (self.upload_label, self._upload_button, self._more_button):
            if widget is not None:
                theme.apply_semantic(widget, "muted", background=isinstance(widget, QPushButton))
        if self._upload_button is not None:
            self._upload_button.setIcon(tabler_icon(self._upload_button, "paperclip", 15))
        if self.input is not None:
            theme.apply_surface(self.input)
        if self.send_button is not None:
            theme.apply_accent(self.send_button)
            self.send_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
            self.send_button.setIcon(tabler_icon(self.send_button, "send", 18, semantic="on-accent"))
            self.send_button.setIconSize(QSize(18, 18))
            self._update_primary()

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
            label.setVisible(bool(summary))
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
