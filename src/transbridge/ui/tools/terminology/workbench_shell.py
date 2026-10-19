"""Compact terminology shell with project context and secondary navigation."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .view_models import TERMINOLOGY_AREAS, TerminologyArea


class TerminologyWorkbenchShell(QWidget):
    """Keep project and source context above a full-width terminology table."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminologyWorkbenchShell")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.surface = QFrame(self)
        self.surface.setObjectName("terminologyWorkbenchSurface")
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.surface.setMinimumWidth(760)
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        outer.addWidget(self.surface, 20)

        header = QFrame(self.surface)
        header.setObjectName("terminologyHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 8)
        header_layout.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        title = QLabel("项目术语", header)
        title.setProperty("tbTerminologyBrandTitle", True)
        title.setAccessibleDescription("管理当前项目术语")
        brand.addWidget(title)
        header_layout.addLayout(brand)

        self.brand_context = QLabel("管理当前项目术语", header)
        self.brand_context.hide()

        project = QFrame(header)
        project.setObjectName("terminologyProjectContext")
        project_layout = QHBoxLayout(project)
        project_layout.setContentsMargins(16, 0, 0, 0)
        project_layout.setSpacing(12)
        self._project_icon = QLabel(project)
        self._project_icon.setAccessibleName("当前项目")
        project_layout.addWidget(self._project_icon)
        self.project_name = QLabel("正在读取当前项目", project)
        self.project_name.setProperty("tbTerminologyProjectTitle", True)
        project_layout.addWidget(self.project_name, 1)
        self.project_caption = QLabel("", project)
        self.project_caption.setProperty("tbSecondary", True)
        self.project_caption.setWordWrap(True)
        context_row = QHBoxLayout()
        context_row.addWidget(self.project_caption, 1)
        header_layout.addLayout(context_row)
        self.current_version = QLabel("当前术语版本：尚未发布", header)
        self.current_version.setProperty("tbSecondary", True)
        self.current_version.setTextFormat(Qt.TextFormat.PlainText)
        self.current_version.setWordWrap(False)
        context_row.addWidget(self.current_version)
        brand.addWidget(project)
        brand.addStretch(1)

        self._scheme_slot = QVBoxLayout()
        self._scheme_slot.setContentsMargins(0, 0, 0, 0)
        self._scheme_slot.setSpacing(0)
        header_layout.addLayout(self._scheme_slot)
        surface_layout.addWidget(header)

        self.navigation = QFrame(header)
        self.navigation.setObjectName("terminologyTopNavigation")
        self.navigation.setAccessibleName("术语工作台导航")
        navigation_layout = QHBoxLayout(self.navigation)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_layout.setSpacing(8)

        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        self._area_buttons: dict[TerminologyArea, QToolButton] = {}
        self._area_indices: dict[TerminologyArea, int] = {}
        for index, (area, label, _icon_id) in enumerate(TERMINOLOGY_AREAS):
            button = QToolButton(self.navigation)
            button.setText(label)
            button.setIconSize(QSize(20, 20))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setProperty("tbTerminologyCompactNav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _checked=False, value=index: self.set_current_index(value))
            self._buttons.addButton(button, index)
            self._area_buttons[area] = button
            self._area_indices[area] = index
            navigation_layout.addWidget(button)
        brand.addWidget(self.navigation)

        self.pages = QStackedWidget(self.surface)
        self.pages.setObjectName("terminologyObjectPages")
        self.pages.setAccessibleName("术语工作区")
        surface_layout.addWidget(self.pages, 1)
        self._notice_slot = QVBoxLayout()
        self._notice_slot.setContentsMargins(0, 0, 0, 0)
        surface_layout.addLayout(self._notice_slot)
        self._icon_refresh_timer = QTimer(self)
        self._icon_refresh_timer.setSingleShot(True)
        self._icon_refresh_timer.timeout.connect(self._refresh_icons)
        self._refresh_icons()

    def _refresh_icons(self) -> None:
        # QSS can replace per-widget disabled roles; artwork uses the theme palette.
        palette = QApplication.palette()
        self._project_icon.setPixmap(
            tabler_icon(self._project_icon, "folder", 21, palette=palette).pixmap(QSize(21, 21))
        )
        for area, _label, icon_id in TERMINOLOGY_AREAS:
            button = self._area_buttons[area]
            button.setIcon(tabler_icon(button, icon_id, 20, palette=palette))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        timer = getattr(self, "_icon_refresh_timer", None)
        if timer is not None and event.type() in {
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            # Child palettes and QSS finish updating before artwork is rendered.
            timer.start(0)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for _area, label, _icon in TERMINOLOGY_AREAS)

    def set_context(self, project_name: str, variant_name: str, source_count: int) -> None:
        project = project_name.strip() or "当前项目"
        variant = variant_name.strip() or "当前翻译版本"
        self.project_name.setText(project)
        self.project_caption.setText(f"翻译版本：{variant} · {source_count} 个来源")
        self.project_name.setAccessibleDescription(f"{variant}，{source_count} 个来源")

    def set_scheme_switcher(self, widget: QWidget) -> None:
        """Place output naming selection beside the Project/Variant context."""

        self._scheme_slot.addWidget(widget)

    def set_operation_notice(self, widget: QWidget) -> None:
        self._notice_slot.addWidget(widget)

    def set_terminology_version(self, version_id: str | None) -> None:
        self.current_version.setText(f"当前术语版本：{version_id or '尚未发布'}")

    def add_area(self, area: TerminologyArea, widget: QWidget) -> None:
        expected = self._area_indices[area]
        actual = self.pages.addWidget(widget)
        if actual != expected:
            raise ValueError(f"术语工作区页面顺序错误：{area.value}")
        if actual == 0:
            self.set_current_index(0)

    def set_current_area(self, area: TerminologyArea) -> None:
        if area in {TerminologyArea.OVERVIEW, TerminologyArea.SCHEMES}:
            area = TerminologyArea.TERMS
        self.set_current_index(self._area_indices[area])

    def set_current_index(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        button = self._buttons.button(index)
        if button is not None:
            button.setChecked(True)

    def current_area(self) -> TerminologyArea:
        return TERMINOLOGY_AREAS[self.pages.currentIndex()][0]


__all__ = ["TerminologyWorkbenchShell"]
