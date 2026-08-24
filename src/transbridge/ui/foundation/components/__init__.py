"""Small composable Qt primitives for the UI Foundation.

Standard widgets receive their appearance from the application stylesheet.
These helpers only attach stable semantic properties.
"""

from __future__ import annotations

from enum import StrEnum

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QSizePolicy, QWidget


class ComponentKind(StrEnum):
    BUTTON = "button"
    INPUT = "input"
    CARD = "card"
    DIALOG = "dialog"
    TABLE = "table"
    LABEL = "label"
    BADGE = "badge"
    TOOLTIP = "tooltip"
    EMPTY_STATE = "empty-state"
    PROGRESS = "progress"
    NOTIFICATION = "notification"
    FOCUS = "focus"
    MENU = "menu"
    TABS = "tabs"


class ComponentDensity(StrEnum):
    DEFAULT = "default"
    COMPACT = "compact"


class SemanticState(StrEnum):
    DEFAULT = "default"
    PRIMARY = "primary"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"
    DISABLED = "disabled"
    HOVERED = "hovered"
    CHECKED = "checked"
    FOCUSED = "focused"


# Static layout/shape only. Theme colours belong to QPalette or adapters.
STATIC_STRUCTURE_STYLES: dict[ComponentKind, str] = {
    ComponentKind.BUTTON: '*[tbComponentKind="button"] { padding: 5px 12px; border-radius: 7px; min-height: 22px; }',
    ComponentKind.INPUT: '*[tbComponentKind="input"] { padding: 4px 8px; border-radius: 7px; min-height: 22px; }',
    ComponentKind.CARD: (
        '*[tbComponentKind="card"] { border-width: 1px; border-style: solid; border-radius: 10px; padding: 12px; }'
    ),
    ComponentKind.DIALOG: "",
    ComponentKind.TABLE: (
        'QTableView[tbComponentKind="table"] { border-radius: 8px; }'
        ' QTableView[tbComponentKind="table"]::item { padding: 4px 8px; }'
        ' QTableView[tbComponentKind="table"] QHeaderView::section { padding: 7px 8px; }'
    ),
    ComponentKind.LABEL: "",
    ComponentKind.BADGE: '*[tbComponentKind="badge"] { border-radius: 10px; padding: 3px 9px; }',
    ComponentKind.TOOLTIP: '*[tbComponentKind="tooltip"] { padding: 6px 8px; }',
    ComponentKind.EMPTY_STATE: '*[tbComponentKind="empty-state"] { padding: 20px; }',
    ComponentKind.PROGRESS: '*[tbComponentKind="progress"] { border-radius: 4px; min-height: 8px; }',
    ComponentKind.NOTIFICATION: (
        '*[tbComponentKind="notification"] {'
        " border-width: 1px; border-style: solid; border-radius: 10px; padding: 2px; }"
    ),
    ComponentKind.FOCUS: '*[tbComponentKind="focus"] { border-width: 2px; border-style: solid; }',
    ComponentKind.MENU: (
        'QMenuBar[tbComponentKind="menu"] { padding: 3px 6px; spacing: 2px; }'
        ' QMenuBar[tbComponentKind="menu"]::item { padding: 6px 9px; border-radius: 5px; }'
        " QMenu { padding: 6px; }"
        " QMenu::item { padding: 6px 28px 6px 10px; border-radius: 5px; }"
    ),
    ComponentKind.TABS: (
        '*[tbComponentKind="tabs"] QTabBar::tab {'
        " min-height: 24px; padding: 5px 14px; margin-right: 2px;"
        " border-top-left-radius: 7px; border-top-right-radius: 7px; }"
    ),
}


class ComponentStyle:
    """Apply stable component properties without owning theme colours."""

    @staticmethod
    def apply_static(
        widget: QWidget,
        component_kind: ComponentKind | str,
        density: ComponentDensity | str = ComponentDensity.DEFAULT,
    ) -> QWidget:
        kind = ComponentKind(component_kind)
        normalized_density = ComponentDensity(density)
        widget.setProperty("tbComponentKind", kind.value)
        widget.setProperty("tbDensity", normalized_density.value)
        return widget

    @staticmethod
    def apply_state(widget: QWidget, semantic_state: SemanticState | str) -> QWidget:
        state = SemanticState(semantic_state)
        widget.setProperty("tbSemanticState", state.value)
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()
        return widget


class AccentButton(QPushButton):
    """Compatibility type for primary actions styled by semantic properties."""


def make_primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    button = AccentButton(text, parent)
    ComponentStyle.apply_static(button, ComponentKind.BUTTON)
    ComponentStyle.apply_state(button, SemanticState.PRIMARY)
    if text.strip():
        button.setAccessibleName(text.strip())
    return button


def configure_dialog(dialog: QDialog) -> QDialog:
    ComponentStyle.apply_static(dialog, ComponentKind.DIALOG)
    dialog.setProperty("tbDialog", True)
    return dialog


class ThemedCard(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        ComponentStyle.apply_static(self, ComponentKind.CARD)


class StatusBadge(QLabel):
    """A text-first status cue; colour may supplement but never replace text."""

    def __init__(
        self,
        text: str = "",
        state: SemanticState | str = SemanticState.DEFAULT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        ComponentStyle.apply_static(self, ComponentKind.BADGE, ComponentDensity.COMPACT)
        self.set_status(text, state)

    def set_status(self, text: str, state: SemanticState | str) -> None:
        self.setText(text)
        ComponentStyle.apply_state(self, state)
        accessible = text.strip() or SemanticState(state).value
        self.setAccessibleName(accessible)
        self.setAccessibleDescription(accessible)


class ElidedLabel(QLabel):
    """Single-line label whose full text never creates a variable minimum width."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    @property
    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setAccessibleDescription(self._full_text)
        self._refresh_visible_text()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh_visible_text()

    def _refresh_visible_text(self) -> None:
        available = max(0, self.contentsRect().width())
        visible = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        super().setText(visible)


def reserve_text_width(widget: QWidget, candidates: tuple[str, ...]) -> int:
    """Reserve the largest size hint of a finite set of runtime labels."""

    if not candidates or not hasattr(widget, "text") or not hasattr(widget, "setText"):
        return widget.minimumWidth()
    original = widget.text()
    width = widget.minimumWidth()
    for candidate in candidates:
        widget.setText(candidate)
        width = max(width, widget.sizeHint().width())
    widget.setText(original)
    widget.setMinimumWidth(width)
    return width


__all__ = [
    "AccentButton",
    "ComponentDensity",
    "ComponentKind",
    "ComponentStyle",
    "ElidedLabel",
    "STATIC_STRUCTURE_STYLES",
    "SemanticState",
    "StatusBadge",
    "ThemedCard",
    "configure_dialog",
    "make_primary_button",
    "reserve_text_width",
]
