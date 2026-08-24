"""Modern desktop navigation rail without owning application state."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.tabler_icons import tabler_icon
from transbridge.ui.shell.action_catalog import IntentId

_AVATAR_SIZE = 32


class NavigationButton(QToolButton):
    """Compatibility type for application-styled navigation actions."""


class NavigationRail(QFrame):
    """Emit page and intent requests while retaining no business state."""

    page_requested = pyqtSignal(int)
    intent_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, theme_view=None) -> None:
        super().__init__(parent)
        self.setObjectName("tbNavigationRail")
        self.setAccessibleName("主导航")
        self.setFixedWidth(176)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(6)

        self._page_group = QButtonGroup(self)
        self._page_group.setExclusive(True)
        self._page_buttons: list[QToolButton] = []
        self._icon_buttons: list[tuple[QToolButton, str]] = []
        self._add_page(layout, "工作台", "layout-dashboard", 0)
        self._add_page(layout, "ParaTranz 管理", "language", 1)
        layout.addStretch(1)

        self._add_intent(layout, "设置", "settings", IntentId.SETTINGS_APPEARANCE)
        self._add_intent(layout, "帮助", "help-circle", IntentId.HELP_CONTEXT)
        self._add_intent(layout, "关于", "info-circle", IntentId.HELP_ABOUT)

        user_panel = QFrame(self)
        user_panel.setObjectName("tbNavigationUser")
        user_layout = QHBoxLayout(user_panel)
        user_layout.setContentsMargins(4, 10, 4, 0)
        user_layout.setSpacing(8)
        self._avatar = QLabel("本", user_panel)
        self._avatar.setProperty("tbAvatar", True)
        self._avatar.setAccessibleName("ParaTranz 用户头像")
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
        user_layout.addWidget(self._avatar)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        self._user_name = QLabel("本地用户", user_panel)
        presence = QHBoxLayout()
        presence.setSpacing(4)
        self._presence_dot = QLabel("●", user_panel)
        self._presence_dot.setProperty("tbConnectionState", "local")
        self._user_state = QLabel("本地模式", user_panel)
        self._user_state.setProperty("tbSecondary", True)
        self._user_state.setAccessibleName("用户连接状态")
        self._user_state.setAccessibleDescription("当前未连接 ParaTranz")
        presence.addWidget(self._presence_dot)
        presence.addWidget(self._user_state)
        presence.addStretch(1)
        identity.addWidget(self._user_name)
        identity.addLayout(presence)
        user_layout.addLayout(identity, 1)
        layout.addWidget(user_panel)
        self._refresh_icons()
        if theme_view is not None:
            theme_view.subscribe(self, lambda _snapshot: self._refresh_icons())
        self.set_current_page(0)

    def _add_page(self, layout: QVBoxLayout, text: str, icon_id: str, index: int) -> None:
        button = self._button(text, icon_id)
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, value=index: self.page_requested.emit(value))
        self._page_group.addButton(button, index)
        self._page_buttons.append(button)
        layout.addWidget(button)

    def _add_intent(self, layout: QVBoxLayout, text: str, icon_id: str, intent: IntentId) -> None:
        button = self._button(text, icon_id)
        button.setProperty("tbNavIntent", True)
        button.clicked.connect(lambda _checked=False, value=intent: self.intent_requested.emit(value.value))
        layout.addWidget(button)

    def _button(self, text: str, icon_id: str) -> QToolButton:
        button = NavigationButton(self)
        button.setText(text)
        button.setProperty("tbNavItem", True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        button.setMouseTracking(True)
        button.setAccessibleName(text.strip())
        self._icon_buttons.append((button, icon_id))
        return button

    def _refresh_icons(self) -> None:
        for button, icon_id in self._icon_buttons:
            button.setIcon(tabler_icon(button, icon_id, 18))

    def set_current_page(self, index: int) -> None:
        if 0 <= index < len(self._page_buttons):
            self._page_buttons[index].setChecked(True)

    def set_user(self, user: dict | None) -> None:
        if not user:
            self._user_name.setText("本地用户")
            self._user_state.setText("本地模式")
            self._presence_dot.setProperty("tbConnectionState", "local")
            self._user_state.setAccessibleDescription("当前未连接 ParaTranz")
            self._set_avatar_fallback("本地用户")
            self._refresh_dynamic_style(self._presence_dot)
            return
        name = user.get("nickname") or user.get("username") or "已登录"
        self._user_name.setText(str(name))
        self._user_state.setText("在线")
        self._presence_dot.setProperty("tbConnectionState", "online")
        self._user_state.setAccessibleDescription("ParaTranz 已连接，当前在线")
        self._set_avatar_fallback(str(name))
        self._refresh_dynamic_style(self._presence_dot)
        payload = user.get("_avatar_bytes")
        if isinstance(payload, (bytes, bytearray, memoryview)):
            self._apply_avatar_payload(bytes(payload))

    def _apply_avatar_payload(self, payload: bytes) -> None:
        source = QPixmap()
        if payload and source.loadFromData(payload):
            self._avatar.setText("")
            self._avatar.setPixmap(self._circular_avatar(source))

    def _set_avatar_fallback(self, name: str) -> None:
        self._avatar.clear()
        self._avatar.setText(next((char for char in name.strip() if not char.isspace()), "本"))

    @staticmethod
    def _circular_avatar(source: QPixmap) -> QPixmap:
        scaled = source.scaled(
            _AVATAR_SIZE,
            _AVATAR_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = max(0, (scaled.width() - _AVATAR_SIZE) // 2)
        top = max(0, (scaled.height() - _AVATAR_SIZE) // 2)
        cropped = scaled.copy(left, top, _AVATAR_SIZE, _AVATAR_SIZE)
        result = QPixmap(_AVATAR_SIZE, _AVATAR_SIZE)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addEllipse(0, 0, _AVATAR_SIZE, _AVATAR_SIZE)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
        return result

    @staticmethod
    def _refresh_dynamic_style(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()


class WorkspaceShell(QWidget):
    """Compatibility page container with a modern persistent navigation rail."""

    intent_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, *, theme_view=None) -> None:
        super().__init__(parent)
        self.setObjectName("tbWorkspaceShell")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.navigation = NavigationRail(self, theme_view=theme_view)
        self.pages = QStackedWidget(self)
        self.pages.setAccessibleName("主工作区")
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.navigation.page_requested.connect(self.setCurrentIndex)
        self.navigation.intent_requested.connect(self.intent_requested.emit)
        self.pages.currentChanged.connect(self.navigation.set_current_page)

    def addTab(self, widget: QWidget, _label: str) -> int:  # noqa: N802 - QTabWidget compatibility
        return self.pages.addWidget(widget)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - QTabWidget compatibility
        self.pages.setCurrentIndex(index)

    def currentIndex(self) -> int:  # noqa: N802 - QTabWidget compatibility
        return self.pages.currentIndex()

    def widget(self, index: int) -> QWidget | None:
        return self.pages.widget(index)


__all__ = ["NavigationRail", "WorkspaceShell"]
