"""Compile one application-wide Qt stylesheet from a validated theme snapshot."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .theme_service import ThemeSnapshot


class _CssColor:
    def __init__(self, role: str) -> None:
        self.canonical = f"palette({role})"


def compile_application_stylesheet(snapshot: ThemeSnapshot) -> str:
    """Build the single visual skin owned and reapplied by ``ThemeService``."""

    primitive = snapshot.tokens.primitives
    window = _CssColor("window")
    surface = _CssColor("base")
    text = _CssColor("text")
    muted = _CssColor("shadow")
    border = _CssColor("mid")
    focus = _CssColor("link")
    selection = _CssColor("highlight")
    selection_text = _CssColor("highlighted-text")
    disabled_text = _CssColor("text")
    disabled_surface = _CssColor("button")
    on_accent = surface
    success = _CssColor("link-visited")
    info = _CssColor("link")
    warning = _CssColor("dark")
    error = _CssColor("bright-text")
    subtle_border = _CssColor("mid")
    control_hover = _CssColor("light")
    nav_selected = _CssColor("light")
    row_alternate = _CssColor("alternate-base")
    header_surface = _CssColor("midlight")
    selected_row = _CssColor("highlight")
    selected_row_text = _CssColor("highlighted-text")
    progress_track = _CssColor("midlight")
    radius = round(primitive.radii.md)
    radius_large = round(primitive.radii.lg)
    control_height = round(primitive.sizes.control_height)
    body_size = primitive.typography.body_size
    small_size = primitive.typography.small_size
    family = primitive.typography.families[0]

    return f"""
QWidget {{
    color: {text.canonical};
    font-family: "{family}";
    font-size: {body_size:g}pt;
}}
QMainWindow, QDialog, QWidget#tbWorkspaceShell, QWidget#tbWorkbench {{
    background: {window.canonical};
}}
QToolTip {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius}px;
    padding: 6px 8px;
}}

QFrame#tbNavigationRail {{
    background: {surface.canonical};
    border: 0;
    border-right: 1px solid {subtle_border.canonical};
}}
QToolButton[tbNavItem="true"] {{
    color: {text.canonical};
    background: transparent;
    border: 0;
    border-left: 3px solid transparent;
    border-radius: {radius}px;
    min-height: 48px;
    padding: 4px 12px;
    text-align: left;
}}
QToolButton[tbNavItem="true"]:hover {{
    color: {focus.canonical};
    background: {control_hover.canonical};
}}
QToolButton[tbNavItem="true"]:checked {{
    color: {focus.canonical};
    background: {nav_selected.canonical};
    border-left: 3px solid {focus.canonical};
    font-weight: 600;
}}
QToolButton[tbNavIntent="true"]:hover {{
    color: {focus.canonical};
    background: {header_surface.canonical};
    border-left: 3px solid {subtle_border.canonical};
}}
QToolButton[tbNavIntent="true"]:pressed {{
    background: {nav_selected.canonical};
    border-left-color: {focus.canonical};
}}

QFrame#terminologyWorkbenchSurface {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius_large + 4}px;
    font-family: "Microsoft YaHei UI";
}}
QFrame#terminologyWorkbenchSurface QWidget {{
    font-family: "Microsoft YaHei UI";
}}
QFrame#terminologyHeader {{
    background: {surface.canonical};
    border: 0;
    border-bottom: 1px solid {subtle_border.canonical};
}}
QLabel#terminologyBrandMark {{
    color: {on_accent.canonical};
    background: {error.canonical};
    border: 0;
    border-radius: 12px;
    font-size: 15pt;
    font-weight: 600;
}}
QLabel[tbTerminologyBrandTitle="true"] {{
    color: {text.canonical};
    font-size: 18pt;
    font-weight: 650;
}}
QLabel[tbTerminologyProjectTitle="true"] {{
    color: {text.canonical};
    font-size: 12pt;
    font-weight: 600;
}}
QFrame#terminologyProjectCard {{
    background: {row_alternate.canonical};
    border: 0;
    border-radius: {radius_large}px;
}}
QFrame#terminologyTopNavigation {{
    background: {header_surface.canonical};
    border: 0;
    border-bottom: 1px solid {subtle_border.canonical};
}}
QToolButton[tbTerminologyNav="true"] {{
    color: {text.canonical};
    background: transparent;
    border: 0;
    border-radius: {radius_large}px;
    min-height: 54px;
    padding: 0 18px;
    font-size: 12pt;
}}
QToolButton[tbTerminologyNav="true"]:hover {{
    color: {focus.canonical};
    background: {control_hover.canonical};
}}
QToolButton[tbTerminologyNav="true"]:checked {{
    color: {focus.canonical};
    background: {nav_selected.canonical};
    font-weight: 600;
}}
QStackedWidget#terminologyObjectPages {{
    background: {surface.canonical};
    border: 0;
}}
QLabel[tbTerminologyPageTitle="true"] {{
    color: {text.canonical};
    font-size: 22pt;
    font-weight: 650;
}}
QLabel[tbTerminologySectionTitle="true"] {{
    color: {text.canonical};
    font-size: 14pt;
    font-weight: 600;
}}
QLabel[tbTerminologyMetric="true"] {{
    color: {text.canonical};
    font-size: 20pt;
    font-weight: 650;
}}
QFrame[tbTerminologyCard="true"] {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius_large}px;
}}
QFrame[tbTerminologySoftCard="true"] {{
    color: {text.canonical};
    background: {row_alternate.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius_large}px;
}}
QFrame[tbTerminologyAlert="true"] {{
    color: {warning.canonical};
    background: {header_surface.canonical};
    border: 1px solid {warning.canonical};
    border-radius: {radius_large}px;
}}
QFrame[tbTerminologyListRow="true"] {{
    background: transparent;
    border: 0;
    border-bottom: 1px solid {header_surface.canonical};
}}
QPushButton[tbTerminologyPrimary="true"] {{
    color: {on_accent.canonical};
    background: {focus.canonical};
    border: 1px solid {focus.canonical};
    border-radius: {radius}px;
    min-height: {control_height}px;
    padding: 0 14px;
    font-weight: 600;
}}
QPushButton[tbTerminologyPrimary="true"]:hover {{
    color: {on_accent.canonical};
    background: {selected_row.canonical};
}}
QPushButton[tbTerminologyPrimary="true"]:disabled {{
    color: {disabled_text.canonical};
    background: {disabled_surface.canonical};
    border-color: {subtle_border.canonical};
}}
QPushButton[tbTerminologyFilter="true"] {{
    color: {muted.canonical};
    background: transparent;
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    min-height: {control_height}px;
    padding: 0 12px;
}}
QPushButton[tbTerminologyFilter="true"]:checked {{
    color: {focus.canonical};
    border-bottom-color: {focus.canonical};
    font-weight: 600;
}}
QPushButton#tbNavigationUser {{
    color: {text.canonical};
    background: transparent;
    border: 0;
    border-top: 1px solid {subtle_border.canonical};
    border-radius: {radius}px;
    padding: 0;
    text-align: left;
}}
QPushButton#tbNavigationUser:hover, QPushButton#tbNavigationUser:focus {{
    background: {control_hover.canonical};
    border-top-color: {focus.canonical};
}}
QPushButton#tbNavigationUser:pressed {{
    background: {nav_selected.canonical};
}}
QLabel[tbSecondary="true"] {{ color: {muted.canonical}; }}
QLabel[tbAvatar="true"] {{
    color: {focus.canonical};
    background: {header_surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: 16px;
    font-weight: 600;
}}
QLabel[tbConnectionState="online"] {{ color: {success.canonical}; }}
QLabel[tbConnectionState="local"] {{ color: {muted.canonical}; }}
QLabel[tbConnectionState="offline"] {{ color: {error.canonical}; }}

QMenuBar[tbComponentKind="menu"] {{
    color: {text.canonical};
    background: {header_surface.canonical};
    border: 0;
    border-bottom: 1px solid {subtle_border.canonical};
    padding: 2px 6px;
    spacing: 2px;
}}
QMenuBar[tbComponentKind="menu"]::item {{
    color: {text.canonical};
    background: transparent;
    border-radius: {radius}px;
    padding: 5px 9px;
}}
QMenuBar[tbComponentKind="menu"]::item:selected {{
    color: {focus.canonical};
    background: {control_hover.canonical};
}}
QMenuBar[tbComponentKind="menu"]::item:pressed {{
    color: {focus.canonical};
    background: {nav_selected.canonical};
}}

*[tbComponentKind="card"], *[tbComponentKind="notification"], QFrame#tbTableSurface {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius_large}px;
}}
*[tbComponentKind="notification"] {{
    background: {header_surface.canonical};
}}

QPushButton[tbComponentKind="button"], QToolButton[tbComponentKind="button"] {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius}px;
    min-height: {control_height - 2}px;
    padding: 0 12px;
}}
QPushButton[tbComponentKind="button"]:hover, QToolButton[tbComponentKind="button"]:hover {{
    color: {focus.canonical};
    background: {control_hover.canonical};
    border-color: {focus.canonical};
}}
QPushButton[tbComponentKind="button"]:pressed, QToolButton[tbComponentKind="button"]:pressed {{
    background: {nav_selected.canonical};
}}
QPushButton[tbComponentKind="button"]:disabled, QToolButton[tbComponentKind="button"]:disabled {{
    color: {disabled_text.canonical};
    background: {disabled_surface.canonical};
    border-color: {subtle_border.canonical};
}}
QPushButton[tbSemanticState="primary"], QToolButton[tbSemanticState="primary"] {{
    color: {focus.canonical};
    background: {control_hover.canonical};
    border-color: {focus.canonical};
    font-weight: 600;
}}
QPushButton[tbSemanticState="primary"]:hover, QToolButton[tbSemanticState="primary"]:hover {{
    color: {focus.canonical};
    background: {selected_row.canonical};
    border-color: {focus.canonical};
}}
QPushButton[tbSemanticState="primary"]:pressed, QToolButton[tbSemanticState="primary"]:pressed {{
    color: {focus.canonical};
    background: {nav_selected.canonical};
    border-color: {focus.canonical};
}}

QLabel#startCenterTitle {{
    color: {text.canonical};
    font-size: 26pt;
    font-weight: 600;
}}
QLabel#startCenterSubtitle {{
    color: {muted.canonical};
    font-size: 12pt;
}}
QLabel[tbStartPanelHeading="true"] {{
    color: {text.canonical};
    font-size: 15pt;
    font-weight: 600;
}}
QPushButton[tbStartAction="true"] {{
    min-height: 112px;
    padding: 0;
    text-align: left;
}}
QPushButton[tbStartAction="true"] QLabel[tbStartActionTitle="true"] {{
    font-size: 14pt;
    font-weight: 600;
}}
QPushButton[tbStartAction="true"] QLabel[tbStartActionDescription="true"] {{
    font-size: 10.5pt;
}}
QPushButton[tbStartAction="true"] QLabel[tbStartActionArrow="true"] {{
    font-size: 22pt;
}}
QPushButton[tbStartAction="true"] QLabel[tbStartActionIcon="true"] {{
    background: {header_surface.canonical};
    border-radius: {radius}px;
}}
QPushButton[tbProjectOpenMode="true"] {{
    min-height: 88px;
    max-height: 88px;
    padding: 0;
    text-align: left;
}}
QPushButton[tbProjectOpenMode="true"] QLabel[tbProjectOpenModeTitle="true"] {{
    font-size: 12pt;
    font-weight: 600;
}}
QPushButton[tbProjectOpenMode="true"] QLabel[tbProjectOpenModeDescription="true"] {{
    font-size: 9.5pt;
}}
QPushButton#startCenterPrimaryAction {{
    color: {on_accent.canonical};
    background: {focus.canonical};
    border-color: {focus.canonical};
    min-height: 120px;
}}
QPushButton#startCenterPrimaryAction:hover,
QPushButton#startCenterPrimaryAction:pressed {{
    color: {on_accent.canonical};
    background: {focus.canonical};
    border-color: {focus.canonical};
}}
QPushButton#startCenterPrimaryAction QLabel[tbStartActionIcon="true"] {{ background: transparent; }}
QPushButton#startCenterPrimaryAction:disabled {{
    color: {disabled_text.canonical};
    background: {disabled_surface.canonical};
    border-color: {subtle_border.canonical};
}}
QPushButton#startCenterPrimaryAction:disabled QLabel {{ color: {disabled_text.canonical}; }}
QPushButton[tbStartAdvanced="true"] {{
    color: {focus.canonical};
    background: transparent;
    border: 0;
    padding: 4px 0;
}}
QPushButton[tbStartAdvanced="true"]:hover {{
    color: {focus.canonical};
    background: {control_hover.canonical};
    border: 0;
}}
QLabel[tbStartProjectName="true"] {{
    color: {text.canonical};
    font-size: 13.5pt;
    font-weight: 600;
}}
QLabel[tbStartProjectPath="true"] {{
    color: {muted.canonical};
    font-size: 10.5pt;
}}
QLabel[tbStartEmptyTitle="true"] {{
    color: {text.canonical};
    font-size: 14pt;
    font-weight: 600;
}}
QLabel[tbStartEmptyDescription="true"] {{
    color: {muted.canonical};
    font-size: 11pt;
}}
QListWidget#startCenterProjectList {{
    background: transparent;
    border: 0;
    outline: 0;
}}
QListWidget#startCenterProjectList::item {{
    border: 0;
    border-bottom: 1px solid {header_surface.canonical};
    padding: 0;
}}
QListWidget#startCenterProjectList::item:selected {{
    background: {control_hover.canonical};
}}
QWidget[tbProjectActive="true"] {{
    background: {control_hover.canonical};
    border-left: 3px solid {focus.canonical};
}}
QWidget[tbProjectAvailable="false"] QLabel[tbStartProjectName="true"],
QWidget[tbProjectAvailable="false"] QLabel[tbStartProjectPath="true"] {{
    color: {muted.canonical};
}}

QLineEdit[tbComponentKind="input"], QComboBox[tbComponentKind="input"],
QSpinBox[tbComponentKind="input"], QDoubleSpinBox[tbComponentKind="input"] {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius}px;
    min-height: {control_height - 2}px;
    padding: 0 10px;
    selection-background-color: {selection.canonical};
    selection-color: {selection_text.canonical};
}}
QLineEdit[tbComponentKind="input"]:focus, QComboBox[tbComponentKind="input"]:focus,
QSpinBox[tbComponentKind="input"]:focus, QDoubleSpinBox[tbComponentKind="input"]:focus {{
    border: 1px solid {focus.canonical};
}}
QComboBox[tbComponentKind="input"]::drop-down {{
    border: 0;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    selection-background-color: {selected_row.canonical};
    selection-color: {selected_row_text.canonical};
    outline: 0;
}}

QPushButton[tbSummaryItem="true"] {{
    background: transparent;
    border: 0;
    border-right: 1px solid {subtle_border.canonical};
    border-radius: 0;
    min-height: 62px;
    padding: 0;
}}
QPushButton[tbSummaryItem="true"]:hover {{ background: {control_hover.canonical}; }}
QPushButton[tbSummaryItem="true"][summaryKey="completed"] {{ border-right: 0; }}
QPushButton[tbSummaryItem="true"] QLabel[tbSummaryIcon="true"] {{
    color: {focus.canonical};
    font-size: 20pt;
}}
QPushButton[tbSummaryItem="true"] QLabel[tbSummaryLabel="true"] {{
    color: {muted.canonical};
    font-size: {small_size:g}pt;
}}
QPushButton[tbSummaryItem="true"] QLabel[tbSummaryValue="true"] {{
    color: {text.canonical};
    font-size: 15pt;
    font-weight: 600;
}}
QPushButton[tbSummaryItem="true"][summaryKey="completed"] QLabel[tbSummaryIcon="true"] {{
    color: {success.canonical};
}}

QPushButton[tbComponentKind="badge"] {{
    color: {muted.canonical};
    background: {header_surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: 10px;
    min-height: 20px;
    padding: 1px 9px;
}}
QPushButton[tbComponentKind="badge"]:checked {{
    color: {focus.canonical};
    background: {nav_selected.canonical};
    border-color: {focus.canonical};
}}

QWidget#tbWorkflowActions {{
    background: {surface.canonical};
    border: 0;
    border-bottom: 1px solid {subtle_border.canonical};
}}
QTableView[tbComponentKind="table"] {{
    color: {text.canonical};
    background: {surface.canonical};
    alternate-background-color: {row_alternate.canonical};
    border: 0;
    border-radius: 0;
    gridline-color: {subtle_border.canonical};
    selection-background-color: {selected_row.canonical};
    selection-color: {selected_row_text.canonical};
    outline: 0;
}}
QTableView[tbComponentKind="table"]::item {{
    border: 0;
    border-bottom: 1px solid {header_surface.canonical};
    padding: 3px 8px;
}}
QTableView[tbComponentKind="table"]::item:selected {{
    color: {selected_row_text.canonical};
    background: {selected_row.canonical};
}}
QTableView[tbComponentKind="table"] QHeaderView::section {{
    color: {muted.canonical};
    background: {header_surface.canonical};
    border: 0;
    border-right: 1px solid {subtle_border.canonical};
    border-bottom: 1px solid {subtle_border.canonical};
    padding: 7px 8px;
    font-size: {small_size:g}pt;
    font-weight: 600;
}}

QMenu {{
    color: {text.canonical};
    background: {surface.canonical};
    border: 1px solid {subtle_border.canonical};
    border-radius: {radius}px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: {radius}px; }}
QMenu::item:selected:enabled {{ color: {focus.canonical}; background: {selected_row.canonical}; }}
QMenu::item:selected:disabled {{ color: {disabled_text.canonical}; background: {selected_row.canonical}; }}
QMenu::separator {{ height: 1px; background: {subtle_border.canonical}; margin: 5px 8px; }}

QProgressBar[tbComponentKind="progress"] {{
    color: {text.canonical};
    background: {progress_track.canonical};
    border: 0;
    border-radius: 4px;
    min-height: 7px;
}}
QProgressBar[tbComponentKind="progress"]::chunk {{
    background: {focus.canonical};
    border-radius: 4px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {border.canonical};
    min-height: 30px;
    border-radius: 4px;
    margin: 2px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QLabel[tbSemanticState="success"] {{ color: {success.canonical}; }}
QLabel[tbSemanticState="warning"] {{ color: {warning.canonical}; }}
QLabel[tbSemanticState="error"] {{ color: {error.canonical}; }}
QLabel[tbSemanticState="info"] {{ color: {info.canonical}; }}
""".strip()


__all__ = ["compile_application_stylesheet"]
