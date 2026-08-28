"""Centered terminology shell with project context and horizontal navigation."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
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
    """Present one centered workbench surface with a horizontal object bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("terminologyWorkbenchShell")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(0)
        outer.addStretch(1)

        self.surface = QFrame(self)
        self.surface.setObjectName("terminologyWorkbenchSurface")
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.surface.setMinimumWidth(900)
        self.surface.setMaximumWidth(1280)
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        outer.addWidget(self.surface, 20)
        outer.addStretch(1)

        header = QFrame(self.surface)
        header.setObjectName("terminologyHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(26, 20, 26, 18)
        header_layout.setSpacing(16)

        brand = QHBoxLayout()
        brand.setSpacing(12)
        logo = QLabel("TB", header)
        logo.setObjectName("terminologyBrandMark")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(52, 52)
        brand.addWidget(logo)
        identity = QVBoxLayout()
        identity.setSpacing(1)
        title = QLabel("术语工作台", header)
        title.setProperty("tbTerminologyBrandTitle", True)
        self.brand_context = QLabel("项目术语与版本管理", header)
        self.brand_context.setProperty("tbSecondary", True)
        identity.addWidget(title)
        identity.addWidget(self.brand_context)
        brand.addLayout(identity)
        brand.addStretch(1)
        header_layout.addLayout(brand)

        project = QFrame(header)
        project.setObjectName("terminologyProjectCard")
        project_layout = QHBoxLayout(project)
        project_layout.setContentsMargins(16, 12, 16, 12)
        project_layout.setSpacing(12)
        project_icon = QLabel(project)
        project_icon.setPixmap(tabler_icon(project_icon, "folder", 21).pixmap(QSize(21, 21)))
        project_icon.setAccessibleName("当前项目")
        project_layout.addWidget(project_icon)
        project_identity = QVBoxLayout()
        project_identity.setSpacing(1)
        self.project_name = QLabel("正在读取当前项目", project)
        self.project_name.setProperty("tbTerminologyProjectTitle", True)
        self.project_caption = QLabel("正在检查翻译版本和来源", project)
        self.project_caption.setProperty("tbSecondary", True)
        project_identity.addWidget(self.project_name)
        project_identity.addWidget(self.project_caption)
        project_layout.addLayout(project_identity, 1)
        chevron = QLabel("⌄", project)
        chevron.setProperty("tbSecondary", True)
        project_layout.addWidget(chevron)
        header_layout.addWidget(project)
        surface_layout.addWidget(header)

        self.navigation = QFrame(self.surface)
        self.navigation.setObjectName("terminologyTopNavigation")
        self.navigation.setAccessibleName("术语工作台导航")
        navigation_layout = QHBoxLayout(self.navigation)
        navigation_layout.setContentsMargins(12, 10, 12, 10)
        navigation_layout.setSpacing(8)

        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        self._area_buttons: dict[TerminologyArea, QToolButton] = {}
        self._area_indices: dict[TerminologyArea, int] = {}
        for index, (area, label, icon_id) in enumerate(TERMINOLOGY_AREAS):
            button = QToolButton(self.navigation)
            button.setText(label)
            button.setIcon(tabler_icon(button, icon_id, 20))
            button.setIconSize(QSize(20, 20))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setProperty("tbTerminologyNav", True)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(label)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            button.clicked.connect(lambda _checked=False, value=index: self.set_current_index(value))
            self._buttons.addButton(button, index)
            self._area_buttons[area] = button
            self._area_indices[area] = index
            navigation_layout.addWidget(button, 1)
        surface_layout.addWidget(self.navigation)

        self.pages = QStackedWidget(self.surface)
        self.pages.setObjectName("terminologyObjectPages")
        self.pages.setAccessibleName("术语工作区")
        surface_layout.addWidget(self.pages, 1)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for _area, label, _icon in TERMINOLOGY_AREAS)

    def set_context(self, project_name: str, variant_name: str, source_count: int) -> None:
        project = project_name.strip() or "当前项目"
        variant = variant_name.strip() or "当前翻译版本"
        self.project_name.setText(project)
        self.project_caption.setText(f"{variant} · {source_count} 个来源")
        self.brand_context.setText(project)

    def add_area(self, area: TerminologyArea, widget: QWidget) -> None:
        expected = self._area_indices[area]
        actual = self.pages.addWidget(widget)
        if actual != expected:
            raise ValueError(f"术语工作区页面顺序错误：{area.value}")
        if actual == 0:
            self.set_current_index(0)

    def set_current_area(self, area: TerminologyArea) -> None:
        self.set_current_index(self._area_indices[area])

    def set_current_index(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        button = self._buttons.button(index)
        if button is not None:
            button.setChecked(True)

    def current_area(self) -> TerminologyArea:
        return TERMINOLOGY_AREAS[self.pages.currentIndex()][0]


__all__ = ["TerminologyWorkbenchShell"]
