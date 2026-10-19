from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QSettings, QSize, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLayout,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .composer_editor import ComposerEditor
from .execution_mode_view import ExecutionModeButton
from .quick_actions import QuickActionsChips
from .theme_support import (
    CARD_STRUCTURE_STYLE,
    INPUT_STRUCTURE_STYLE,
    QUIET_BUTTON_STYLE,
    SmartAssistantTheme,
)
from .upload_binding import UploadBinding  # Public import retained for existing callers.


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
        self.input: ComposerEditor | None = None
        self.upload_label: ElidedLabel | None = None
        self.send_button: QPushButton | None = None
        self.stop_button: QPushButton | None = None
        self.auto_checkbox: QAction | None = None
        self.mode_button: ExecutionModeButton | None = None
        self._card: QFrame | None = None
        self._card_layout: QVBoxLayout | None = None
        self._action_layout: QHBoxLayout | None = None
        self._upload_button: QPushButton | None = None
        self._more_button: QPushButton | None = None
        self._closed = False
        self._running = False
        self._preparing_attachments = False
        self._action_appearance: tuple[bool, bool] | None = None
        self._stop = lambda: None
        self._details = lambda: None

    def build_toolbar(self, layout: QVBoxLayout) -> None:
        card = QFrame()
        card.setProperty("tbSurface", "card")
        card.setStyleSheet(CARD_STRUCTURE_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        card_layout.setContentsMargins(10, 8, 10, 6)
        card_layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.upload_label = ElidedLabel("")
        self.upload_label.setAccessibleName("已上传参考文件")
        self.upload_label.hide()
        upload_button = QPushButton()
        upload_button.setAccessibleName("上传参考文件")
        upload_button.setToolTip("添加、预览或移除参考文件（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_button.setFixedSize(32, 32)
        upload_button.setStyleSheet(QUIET_BUTTON_STYLE + "QPushButton { padding: 0; }")
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
        editor = ComposerEditor()
        editor.setAccessibleName("消息输入框")
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
        self.mode_button = ExecutionModeButton(checkbox, self._card)
        self._action_layout.addWidget(self.mode_button)
        self._more_button = QPushButton()
        self._more_button.setAccessibleName("更多输入操作")
        self._more_button.setToolTip("更多：快捷请求、技能和会话操作")
        self._more_button.setFixedSize(32, 32)
        self._more_button.setStyleSheet(QUIET_BUTTON_STYLE + "QPushButton { padding: 0; }")
        self._more_button.clicked.connect(self._show_more)
        self._action_layout.addWidget(self._more_button)
        self._action_layout.addStretch(1)

        self.stop_button = QPushButton("停止")
        self.stop_button.setAccessibleName("停止当前生成")
        self.stop_button.setToolTip("停止推进，保留已完成的修改")
        self.stop_button.setFixedSize(56, 32)
        self.stop_button.setStyleSheet(QUIET_BUTTON_STYLE)
        self.stop_button.clicked.connect(lambda: self._stop())
        self.stop_button.hide()
        self._action_layout.addWidget(self.stop_button)

        send_button = QPushButton("发送")
        send_button.setAccessibleName("发送消息按钮")
        send_button.setToolTip("发送消息")
        send_button.setFixedSize(96, 32)
        send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        send_button.clicked.connect(self._send)
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
        ready_to_send = not self._preparing_attachments and bool(self.input and self.input.toPlainText().strip())
        self.send_button.setToolTip(
            "参考文件正在提取文本…"
            if self._preparing_attachments
            else "补充当前任务的要求（Ctrl+Enter）"
            if self._running
            else "发送消息（Ctrl+Enter）"
        )
        self.send_button.setText("发送补充" if self._running else "发送")
        self.send_button.setEnabled(ready_to_send)
        self.stop_button.setVisible(self._running)
        self.stop_button.setEnabled(self._running)
        appearance = (ready_to_send, self._running and not ready_to_send)
        if appearance == self._action_appearance:
            return
        self._action_appearance = appearance
        for button, primary in zip((self.send_button, self.stop_button), appearance, strict=True):
            if primary:
                self._theme.apply_accent(button)
            else:
                self._theme.apply_surface(button, secondary_text=True)
                self._theme.apply_semantic(button, "muted")
                button.setStyleSheet(QUIET_BUTTON_STYLE)
        self.send_button.setIcon(
            tabler_icon(self.send_button, "send", 18, semantic="on-accent" if ready_to_send else "navigation")
        )
        self.send_button.setIconSize(QSize(18, 18))

    def set_preparing_attachments(self, preparing: bool) -> None:
        self._preparing_attachments = preparing
        self._update_primary()

    def configure_actions(self, *, stop, details) -> None:
        self._stop, self._details = stop, details

    def refresh_mode(self) -> None:
        if self.mode_button is not None:
            self.mode_button.refresh()

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
        menu.addAction("任务与会话详情", self._details)
        menu.addSeparator()
        menu.addAction("清空对话", self._clear)
        menu.exec(self._more_button.mapToGlobal(self._more_button.rect().bottomLeft()))

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        self._action_appearance = None
        if self._card is not None:
            theme.apply_surface(self._card)
        for widget in (self.upload_label, self._upload_button, self._more_button, self.stop_button, self.mode_button):
            if widget is not None:
                theme.apply_semantic(widget, "muted")
        if self._upload_button is not None:
            self._upload_button.setIcon(tabler_icon(self._upload_button, "paperclip", 18))
            self._upload_button.setIconSize(QSize(18, 18))
        if self._more_button is not None:
            self._more_button.setIcon(tabler_icon(self._more_button, "dots", 18))
            self._more_button.setIconSize(QSize(18, 18))
        if self.input is not None:
            theme.apply_surface(self.input)
        if self.send_button is not None:
            self._update_primary()

    def set_text(self, text: str) -> None:
        if not self._closed and self.input is not None:
            self.input.setPlainText(text)
            self.input.setFocus()

    def close(self) -> None:
        self._closed = True


__all__ = ["ChatInputView", "UploadBinding"]


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
        self._skill_launcher = None

    @property
    def observability_visible(self) -> bool:
        return self._observability_visible

    def select_skill(self, skill_name: str) -> None:
        from .skill_launcher import SkillLauncher

        if self._skill_launcher is None:
            self._skill_launcher = SkillLauncher(self._chat_facade)
        self._skill_launcher.open(skill_name)

    def close_skill_preview(self) -> None:
        if self._skill_launcher is not None:
            self._skill_launcher.close()

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
