"""Theme-aware styles for task-oriented dialogs and configuration surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .theme_service import ThemeSnapshot


def compile_task_visual_styles(snapshot: ThemeSnapshot) -> str:
    """Compile the shared task-dialog layer without adding feature rules to the core skin."""

    primitive = snapshot.tokens.primitives
    radius = round(primitive.radii.md)
    radius_large = round(primitive.radii.lg)
    control_height = round(primitive.sizes.control_height)
    small_size = primitive.typography.small_size
    return f"""
*[tbTaskDialog="true"], *[tbTaskDialog="true"] QWidget {{
    font-family: "Microsoft YaHei UI";
}}
*[tbTaskDialog="true"] QLabel[tbTaskTitle="true"] {{
    color: palette(text);
    font-size: 18pt;
    font-weight: 600;
}}
*[tbTaskDialog="true"] QLabel[tbTaskSubtitle="true"] {{
    color: palette(shadow);
    font-size: 11pt;
}}
*[tbTaskDialog="true"] QLabel[tbTaskMeta="true"] {{
    color: palette(shadow);
    font-size: {small_size:g}pt;
}}
QFrame[tbTaskSurface="true"] {{
    color: palette(text);
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: {radius_large}px;
}}
QLabel[tbTaskSectionTitle="true"] {{
    color: palette(text);
    font-size: 12pt;
    font-weight: 600;
}}
QLabel[tbTaskHint="true"] {{ color: palette(shadow); }}
QFrame[tbTaskServiceBar="true"] {{
    background: palette(midlight);
    border: 0;
    border-top: 1px solid palette(mid);
}}
QFrame[tbTaskFooter="true"] {{
    background: transparent;
    border: 0;
    border-top: 1px solid palette(mid);
}}
QFrame[tbTaskModeBar="true"] {{
    background: palette(midlight);
    border: 1px solid palette(mid);
    border-radius: {radius_large}px;
}}
QRadioButton[tbTaskSegment="true"] {{
    color: palette(shadow);
    background: transparent;
    border: 1px solid transparent;
    border-radius: {radius}px;
    min-height: 34px;
    padding: 4px 18px;
    spacing: 0;
}}
QRadioButton[tbTaskSegment="true"]::indicator {{ width: 0; height: 0; }}
QRadioButton[tbTaskSegment="true"]:hover {{
    color: palette(link);
    background: palette(light);
}}
QRadioButton[tbTaskSegment="true"]:checked {{
    color: palette(link);
    background: palette(light);
    border-color: palette(link);
    font-weight: 600;
}}
QTabWidget[tbComponentKind="tabs"]::pane {{
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: {radius_large}px;
    top: -1px;
}}
QTabWidget[tbComponentKind="tabs"] QTabBar::tab {{
    color: palette(shadow);
    background: palette(midlight);
    border: 1px solid palette(mid);
    min-height: 38px;
    min-width: 112px;
    padding: 6px 14px;
}}
QTabWidget[tbComponentKind="tabs"] QTabBar::tab:hover {{
    color: palette(link);
    background: palette(light);
}}
QTabWidget[tbComponentKind="tabs"] QTabBar::tab:selected {{
    color: palette(link);
    background: palette(light);
    border-color: palette(link);
    font-weight: 600;
}}
QListWidget[tbTaskList="true"] {{
    color: palette(text);
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: {radius}px;
    outline: 0;
}}
QListWidget[tbTaskList="true"]::item {{
    border: 0;
    border-bottom: 1px solid palette(midlight);
    padding: 6px 8px;
}}
QListWidget[tbTaskList="true"]::item:hover {{ background: palette(light); }}
QListWidget[tbTaskList="true"]::item:selected {{
    color: palette(highlighted-text);
    background: palette(highlight);
}}
QGroupBox[tbTaskPanel="true"] {{
    color: palette(text);
    background: palette(midlight);
    border: 1px solid palette(mid);
    border-radius: {radius}px;
    margin-top: 14px;
    padding: 12px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox[tbTaskPanel="true"]::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}
QPushButton[tbComponentKind="button"][tbTaskPrimary="true"] {{
    color: palette(base);
    background: palette(link);
    border-color: palette(link);
    min-height: {control_height + 4}px;
    font-weight: 600;
}}
QPushButton[tbComponentKind="button"][tbTaskPrimary="true"]:hover,
QPushButton[tbComponentKind="button"][tbTaskPrimary="true"]:pressed {{
    color: palette(base);
    background: palette(link);
    border-color: palette(link);
}}
""".strip()


__all__ = ["compile_task_visual_styles"]
