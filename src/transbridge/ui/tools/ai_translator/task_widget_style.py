"""Shared semantic styling hooks for current-content and multi-plugin AI tasks."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QGroupBox,
    QLabel,
    QListWidget,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState


def configure_task_host(widget: QWidget) -> None:
    ComponentStyle.apply_static(widget, ComponentKind.DIALOG)
    widget.setProperty("tbDialog", True)
    widget.setProperty("tbTaskDialog", True)


def configure_task_title(label: QLabel, role: str = "title") -> None:
    property_name = {
        "title": "tbTaskTitle",
        "subtitle": "tbTaskSubtitle",
        "meta": "tbTaskMeta",
        "section": "tbTaskSectionTitle",
        "hint": "tbTaskHint",
    }[role]
    label.setProperty(property_name, True)


def configure_task_tabs(tabs: QTabWidget) -> None:
    ComponentStyle.apply_static(tabs, ComponentKind.TABS)
    tabs.setDocumentMode(True)
    tabs.tabBar().setExpanding(True)


def configure_task_input(widget: QComboBox | QSpinBox | QWidget) -> None:
    ComponentStyle.apply_static(widget, ComponentKind.INPUT)


def configure_task_button(button: QAbstractButton, *, primary: bool = False) -> None:
    ComponentStyle.apply_static(button, ComponentKind.BUTTON)
    if primary:
        button.setProperty("tbTaskPrimary", True)
        ComponentStyle.apply_state(button, SemanticState.PRIMARY)


def configure_task_panel(group: QGroupBox) -> None:
    group.setProperty("tbTaskPanel", True)


def configure_task_list(widget: QListWidget) -> None:
    widget.setProperty("tbTaskList", True)


def configure_task_surface(frame: QFrame) -> None:
    frame.setProperty("tbTaskSurface", True)


def configure_task_service_bar(frame: QFrame) -> None:
    frame.setProperty("tbTaskServiceBar", True)


def configure_task_footer(frame: QFrame) -> None:
    frame.setProperty("tbTaskFooter", True)


def configure_task_segment(button: QAbstractButton) -> None:
    button.setProperty("tbTaskSegment", True)


__all__ = [
    "configure_task_button",
    "configure_task_footer",
    "configure_task_host",
    "configure_task_input",
    "configure_task_list",
    "configure_task_panel",
    "configure_task_segment",
    "configure_task_service_bar",
    "configure_task_surface",
    "configure_task_tabs",
    "configure_task_title",
]
