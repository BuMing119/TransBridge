"""One palette-based button treatment for ordinary, task and terminology surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import ThemeDefinition
    from .theme_service import ThemeSnapshot


_BUTTONS = (
    'QPushButton[tbComponentKind="button"]',
    'QToolButton[tbComponentKind="button"]',
    'QPushButton[tbTerminologyPrimary="true"]',
)
# Existing task/terminology hooks remain public styling contracts. Compile their
# appearance together so enabling, disabling and focus cannot diverge by surface.
_PRIMARY = (
    'QPushButton[tbSemanticState="primary"]',
    'QToolButton[tbSemanticState="primary"]',
    'QPushButton[tbComponentKind="button"][tbTaskPrimary="true"]',
    'QPushButton[tbTerminologyPrimary="true"]',
)
_DESTRUCTIVE = ('QPushButton[tbSemanticState="error"]', 'QToolButton[tbSemanticState="error"]')


def _selectors(buttons: tuple[str, ...], state: str = "") -> str:
    return ",\n".join(f"{button}{state}" for button in dict.fromkeys(buttons))


def compile_control_visual_styles(snapshot: ThemeSnapshot | ThemeDefinition) -> str:
    primitive = snapshot.tokens.primitives
    radius = round(primitive.radii.md)
    height = round(primitive.sizes.control_height) - 2
    return f"""
{_selectors(_BUTTONS)} {{
    color: palette(text);
    background: palette(base);
    border: 1px solid palette(mid);
    border-radius: {radius}px;
    min-height: {height}px;
    padding: 0 12px;
}}
{_selectors(_BUTTONS, ":enabled:hover")} {{
    color: palette(link);
    background: palette(light);
    border-color: palette(link);
}}
{_selectors(_BUTTONS, ":enabled:checked")} {{
    color: palette(link);
    background: palette(light);
    border-color: palette(link);
}}
{_selectors(_BUTTONS, ":enabled:pressed")} {{
    color: palette(text);
    background: palette(highlight);
}}
{_selectors(_PRIMARY, ":enabled")},
{_selectors(_PRIMARY, ":enabled:checked")} {{
    color: palette(base);
    background: palette(link);
    border-color: palette(link);
    font-weight: 600;
}}
{_selectors(_PRIMARY, ":enabled:hover")} {{
    color: palette(base);
    background: palette(link);
    border-color: palette(text);
}}
{_selectors(_PRIMARY, ":enabled:pressed")} {{
    color: palette(base);
    background: palette(text);
    border-color: palette(text);
}}
{_selectors(_DESTRUCTIVE, ":enabled")},
{_selectors(_DESTRUCTIVE, ":enabled:hover")} {{
    color: palette(bright-text);
    border-color: palette(bright-text);
}}
{_selectors(_BUTTONS + _PRIMARY + _DESTRUCTIVE, ":enabled:focus")} {{
    border-color: palette(text);
}}
{_selectors(_BUTTONS + _PRIMARY + _DESTRUCTIVE, ":disabled")} {{
    color: palette(shadow);
    background: palette(button);
    border-color: palette(mid);
    font-weight: 400;
}}
""".strip()
