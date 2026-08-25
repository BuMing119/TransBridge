"""Brand header for the floating Smart Assistant workspace."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import BUTTON_STRUCTURE_STYLE, HEADER_STRUCTURE_STYLE, SmartAssistantTheme


class _HeaderSummary(QWidget):
    """Compact read-only state summary; it never pretends to be a selector."""

    def __init__(self, caption: str, value: str, *, theme: SmartAssistantTheme) -> None:
        super().__init__()
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(0)
        self._caption = QLabel(caption)
        self._caption.setFont(QFont("Microsoft YaHei", 8))
        self._value = ElidedLabel(value)
        self._value.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
        layout.addWidget(self._caption)
        layout.addWidget(self._value)
        self.apply_theme(theme)

    def set_value(self, value: str) -> None:
        self._value.set_full_text(value)
        self._value.setToolTip(value)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_surface(self, alternate=True)
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
        self._drag_global_origin: QPoint | None = None
        self._drag_window_origin: QPoint | None = None
        self.setObjectName("smartAssistantHeader")
        self.setStyleSheet(HEADER_STRUCTURE_STYLE)
        self.setAccessibleName("智能助手标题栏")
        self.setFixedHeight(72)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 8, 10, 8)
        layout.setSpacing(10)

        self._brand_icon = QLabel()
        self._brand_icon.setFixedSize(30, 30)
        self._brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._brand_icon.setAccessibleName("TransBridge 智能助手")
        layout.addWidget(self._brand_icon)

        brand = QVBoxLayout()
        brand.setSpacing(0)
        self._title = QLabel("TransBridge 智能助手")
        self._title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self._status = QLabel("○  待配置")
        self._status.setFont(QFont("Microsoft YaHei", 8))
        brand.addWidget(self._title)
        brand.addWidget(self._status)
        layout.addLayout(brand)
        layout.addStretch(1)

        self._mode = _HeaderSummary("工作模式", "翻译协作", theme=self._theme)
        self._model = _HeaderSummary("当前模型", "模型未配置", theme=self._theme)
        self._mode.setFixedWidth(126)
        self._model.setFixedWidth(150)
        layout.addWidget(self._mode)
        layout.addWidget(self._model)

        self._minimize_button = QPushButton()
        self._minimize_button.setAccessibleName("最小化智能助手")
        self._minimize_button.setToolTip("最小化")
        self._minimize_button.setFixedSize(34, 34)
        self._minimize_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._minimize_button.clicked.connect(self.minimize_requested)
        layout.addWidget(self._minimize_button)

        self._close_button = QPushButton()
        self._close_button.setAccessibleName("关闭智能助手")
        self._close_button.setToolTip("关闭")
        self._close_button.setFixedSize(34, 34)
        self._close_button.setStyleSheet(BUTTON_STRUCTURE_STYLE)
        self._close_button.clicked.connect(self.close_requested)
        layout.addWidget(self._close_button)
        self.apply_theme(self._theme)

    def set_model_name(self, model_name: str) -> None:
        value = model_name.strip()
        self._model_configured = bool(value and value != "模型未配置")
        self._model.set_value(value or "模型未配置")
        self._status.setText("●  已配置" if self._model_configured else "○  待配置")
        self.apply_theme(self._theme)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        theme.apply_surface(self)
        theme.apply_semantic(self._title, "default")
        theme.apply_semantic(self._status, "success" if self._model_configured else "muted")
        self._mode.apply_theme(theme)
        self._model.apply_theme(theme)
        for button in (self._minimize_button, self._close_button):
            theme.apply_surface(button, alternate=True)
        self._brand_icon.setPixmap(tabler_icon(self._brand_icon, "sparkles", 25, semantic="accent").pixmap(25, 25))
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
