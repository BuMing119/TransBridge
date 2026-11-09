"""Animated update action; network work remains owned by UpdateController."""

from PyQt6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QVariantAnimation
from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolButton

from transbridge.ui.foundation.tabler_icons import tabler_icon, tabler_pixmap


def _blend(first: QColor, second: QColor, progress: float) -> QColor:
    return QColor.fromRgbF(*(a + (b - a) * progress for a, b in zip(first.getRgbF(), second.getRgbF(), strict=True)))


class UpdateButton(QToolButton):
    """Animate paint values without changing layout or rebuilding SVGs per frame."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._checking = False
        self._available = False
        self._version: str | None = None
        self.setObjectName("tbCheckUpdateButton")
        self.setProperty("tbNavIntent", True)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 18))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hover = self._animation(180)
        self._press = self._animation(100)
        self._rotation = self._animation(1000)
        self._rotation.setEasingCurve(QEasingCurve.Type.Linear)
        self._rotation.setEndValue(360.0)
        self._rotation.setLoopCount(-1)
        self.pressed.connect(lambda: self._transition(self._press, 1.0))
        self.released.connect(lambda: self._transition(self._press, 0.0))
        self._sync_state()

    def _animation(self, duration: int) -> QVariantAnimation:
        animation = QVariantAnimation(self)
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(0.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda _value: self.update())
        return animation

    def _transition(self, animation: QVariantAnimation, target: float) -> None:
        current = animation.currentValue()
        animation.stop()
        animation.setStartValue(current)
        animation.setEndValue(target)
        if self.isVisible():
            animation.start()
        else:
            animation.setCurrentTime(animation.duration())

    def set_available(self, available: bool, version: str | None = None) -> None:
        self._available, self._version = available, version
        self.setProperty("tbUpdateAvailable", available)
        self._sync_state()

    def set_checking(self, checking: bool) -> None:
        if self._checking == checking:
            return
        self._checking = checking
        self.setEnabled(not checking)
        self._sync_state()
        if checking and self.isVisible():
            self._rotation.start()
        else:
            self._rotation.stop()
            self._rotation.setCurrentTime(0)

    def _sync_state(self) -> None:
        label = "正在检查…" if self._checking else "发现新版本" if self._available else "检查更新"
        detail = (
            f"TransBridge v{self._version} 可用，点击查看更新"
            if self._available and self._version and not self._checking
            else label
        )
        self.setText(label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(detail)
        self.setToolTip(detail)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        icon_id = "download" if self._available and not self._checking else "refresh"
        palette = QPalette(self.palette())
        palette.setCurrentColorGroup(QPalette.ColorGroup.Active)
        icon = tabler_icon(self, icon_id, 18, palette=palette)
        self.setIcon(icon)
        # Keep supersampled pixmaps for smooth subpixel rotation on all screens.
        dpr = max(2.0, self.devicePixelRatioF())
        self._normal_icon = tabler_pixmap(icon_id, 18, palette.color(QPalette.ColorRole.Text), dpr=dpr)
        self._accent_icon = tabler_pixmap(icon_id, 18, palette.color(QPalette.ColorRole.Link), dpr=dpr)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        labels = ("检查更新", "正在检查…", "发现新版本")
        width = max(self.fontMetrics().horizontalAdvance(label) for label in labels)
        return QSize(width + 48, max(40, self.fontMetrics().height() + 16))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        if self.isEnabled():
            self._transition(self._hover, 1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._transition(self._hover, 0.0)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if not hasattr(self, "_hover"):
            return
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._transition(self._hover, 0.0)
            self._transition(self._press, 0.0)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.EnabledChange):
            self.refresh_icons()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt override
        for animation in (self._hover, self._press, self._rotation):
            animation.stop()
            animation.setCurrentTime(0)
        for animation in (self._hover, self._press):
            animation.setStartValue(0.0)
            animation.setEndValue(0.0)
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.refresh_icons()
        if self._checking:
            self._rotation.start()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        hover, press = self._hover.currentValue(), self._press.currentValue()
        palette = self.palette()
        group = QPalette.ColorGroup.Active if self.isEnabled() or self._checking else QPalette.ColorGroup.Disabled
        normal = palette.color(group, QPalette.ColorRole.Text)
        accent = palette.color(group, QPalette.ColorRole.Link)
        emphasis = 1.0 if self._checking or self._available else hover
        foreground = _blend(normal, accent, emphasis)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        background = _blend(palette.color(QPalette.ColorRole.Light), palette.color(QPalette.ColorRole.Highlight), press)
        background.setAlphaF(max(hover, press))
        painter.setBrush(background)
        painter.setPen(QPen(accent, 1) if self.hasFocus() and self.isEnabled() else Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bounds, 6, 6)
        painter.translate(0, press)
        icon_bounds = QRectF(11, (self.height() - 18) / 2, 18, 18)
        painter.save()
        painter.translate(icon_bounds.center())
        if self._checking:
            painter.rotate(self._rotation.currentValue())
        target = QRectF(-9, -9, 18, 18)
        if not self.isEnabled() and not self._checking:
            painter.setOpacity(0.4)
            painter.drawPixmap(target, self._normal_icon, QRectF(self._normal_icon.rect()))
        else:
            for pixmap, opacity in ((self._normal_icon, 1 - emphasis), (self._accent_icon, emphasis)):
                painter.setOpacity(opacity)
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.restore()
        painter.setPen(foreground)
        text_bounds = QRectF(37, 0, self.width() - 48, self.height())
        painter.drawText(text_bounds, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()
