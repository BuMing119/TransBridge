"""Compact session header for the floating Smart Assistant workspace."""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from transbridge.smart_assistant.service_readiness import ServiceAvailability
from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import HEADER_STRUCTURE_STYLE, SmartAssistantTheme


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
    """Session and model summary inside the native window's client area."""

    def __init__(self, *, theme: SmartAssistantTheme | None = None) -> None:
        super().__init__()
        self._theme = theme or SmartAssistantTheme()
        self._model_configured = False
        self._service_availability = ServiceAvailability("", False)
        self.setObjectName("smartAssistantHeader")
        self.setStyleSheet(HEADER_STRUCTURE_STYLE)
        self.setAccessibleName("会话与模型信息")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 6)
        layout.setSpacing(8)
        self._identity = QLabel()
        self._identity.setFixedSize(20, 20)
        self._identity.setAccessibleName("智能助手")
        self._identity.setToolTip("智能助手")
        layout.addWidget(self._identity)
        self._title = ElidedLabel("新对话")
        self._title.setAccessibleName("当前会话")
        self._title.setStyleSheet("QLabel { font-weight: 600; }")
        layout.addWidget(self._title, 2)

        self._model = _HeaderSummary("模型", "模型未配置", theme=self._theme)
        self._model.setMinimumWidth(0)
        self._model.setMaximumWidth(220)
        layout.addWidget(self._model, 1)

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
        if title == "New conversation":
            title = "新对话"
        self._title.set_full_text(title)
        self._title.setToolTip(title)
        self._title.setAccessibleDescription(title)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_surface(self, alternate=True)
        theme.apply_semantic(self._title, "default")
        theme.apply_semantic(self._identity, "primary")
        self._identity.setPixmap(tabler_icon(self._identity, "sparkles", 20).pixmap(20, 20))
        self._model.apply_theme(theme)


__all__ = ["AssistantPanelHeader"]
