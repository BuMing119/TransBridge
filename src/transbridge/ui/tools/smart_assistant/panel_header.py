"""Compact session header for the floating Smart Assistant workspace."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from transbridge.smart_assistant.service_readiness import ServiceAvailability
from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import HEADER_STRUCTURE_STYLE, QUIET_BUTTON_STYLE, SmartAssistantTheme


class _HeaderSummary(QWidget):
    """Compact read-only state summary; it never pretends to be a selector."""

    def __init__(self, caption: str, value: str, *, theme: SmartAssistantTheme) -> None:
        super().__init__()
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._caption = QLabel(caption)
        self._caption.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._value = ElidedLabel(value)
        self._value.setStyleSheet("QLabel { font-weight: 600; }")
        layout.addWidget(self._caption)
        layout.addWidget(self._value)
        self.apply_theme(theme)

    def set_value(self, value: str) -> None:
        self._value.set_full_text(value)
        self._value.setToolTip(value)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_surface(self, alternate=True)
        self.setAutoFillBackground(False)
        theme.apply_semantic(self._caption, "muted")
        theme.apply_semantic(self._value, "default")


class AssistantPanelHeader(QFrame):
    """Custom QDockWidget title bar with truthful application state."""

    close_requested = pyqtSignal()
    minimize_requested = pyqtSignal()

    def __init__(self, *, theme: SmartAssistantTheme | None = None) -> None:
        super().__init__()
        self._theme = theme or SmartAssistantTheme()
        self._model_configured = False
        self._service_availability = ServiceAvailability("", False)
        self._drag_global_origin: QPoint | None = None
        self._drag_window_origin: QPoint | None = None
        self.setObjectName("smartAssistantHeader")
        self.setStyleSheet(HEADER_STRUCTURE_STYLE)
        self.setAccessibleName("智能助手标题栏")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 6)
        layout.setSpacing(8)
        self._title = ElidedLabel("新对话")
        self._title.setAccessibleName("当前会话")
        self._title.setStyleSheet("QLabel { font-weight: 600; }")
        layout.addWidget(self._title, 2)

        self._model = _HeaderSummary("模型", "模型未配置", theme=self._theme)
        self._model.setMinimumWidth(0)
        self._model.setMaximumWidth(220)
        layout.addWidget(self._model, 1)

        self._minimize_button = QPushButton()
        self._minimize_button.setAccessibleName("最小化智能助手")
        self._minimize_button.setToolTip("最小化")
        self._minimize_button.setFixedSize(32, 32)
        self._minimize_button.setStyleSheet(QUIET_BUTTON_STYLE + "QPushButton { padding: 0; }")
        self._minimize_button.clicked.connect(self.minimize_requested)
        layout.addWidget(self._minimize_button)

        self._close_button = QPushButton()
        self._close_button.setAccessibleName("关闭智能助手")
        self._close_button.setToolTip("关闭")
        self._close_button.setFixedSize(32, 32)
        self._close_button.setStyleSheet(QUIET_BUTTON_STYLE + "QPushButton { padding: 0; }")
        self._close_button.clicked.connect(self.close_requested)
        layout.addWidget(self._close_button)
        self.apply_theme(self._theme)

    def set_model_name(self, model_name: str) -> None:
        """Change the identifier only; a name alone cannot prove service readiness."""
        value = model_name.strip()
        self._model_configured = False
        self._service_availability = ServiceAvailability(value, False, ("configuration",), "请检查 AI 服务配置。")
        self._model.set_value(value or "模型未配置")
        self.setAccessibleDescription(self._service_availability.message)
        self.apply_theme(self._theme)

    def set_service_availability(self, status: ServiceAvailability) -> None:
        self._service_availability = status
        self._model_configured = status.ready
        name = status.model_name or "模型未配置"
        self._model.set_value(name if status.ready or not status.model_name else f"{name} · 待配置")
        self._model.setToolTip(status.message)
        self._model._value.setToolTip(f"{name}\n{status.message}")
        self.setAccessibleDescription(status.message)
        self.apply_theme(self._theme)

    @property
    def service_availability(self) -> ServiceAvailability:
        return self._service_availability

    @property
    def model_configured(self) -> bool:
        return self._model_configured

    def set_session_name(self, name: str) -> None:
        title = name.strip() or "新对话"
        self._title.set_full_text(title)
        self._title.setToolTip(title)
        self._title.setAccessibleDescription(title)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_surface(self, alternate=True)
        theme.apply_semantic(self._title, "default")
        self._model.apply_theme(theme)
        for button in (self._minimize_button, self._close_button):
            theme.apply_semantic(button, "muted")
        self._minimize_button.setIcon(tabler_icon(self._minimize_button, "minus", 17))
        self._minimize_button.setIconSize(QSize(17, 17))
        self._close_button.setIcon(tabler_icon(self._close_button, "x", 18))
        self._close_button.setIconSize(QSize(18, 18))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        dock = self.parentWidget()
        if event.button() == Qt.MouseButton.LeftButton and dock is not None and dock.isFloating():
            self._drag_global_origin = event.globalPosition().toPoint()
            self._drag_window_origin = dock.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        dock = self.parentWidget()
        if (
            dock is not None
            and dock.isFloating()
            and self._drag_global_origin is not None
            and self._drag_window_origin is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            dock.move(self._drag_window_origin + event.globalPosition().toPoint() - self._drag_global_origin)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_global_origin = None
        self._drag_window_origin = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # The assistant is an overlay, never a layout-consuming dock.
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


__all__ = ["AssistantPanelHeader"]
