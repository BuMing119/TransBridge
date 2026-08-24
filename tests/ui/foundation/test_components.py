from __future__ import annotations

import re

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QWidget
import pytest

from transbridge.ui.foundation.components import (
    STATIC_STRUCTURE_STYLES,
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    SemanticState,
    StatusBadge,
    ThemedCard,
    configure_dialog,
    make_primary_button,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_structure_styles_cover_the_component_contract_without_theme_colours() -> None:
    assert set(STATIC_STRUCTURE_STYLES) == set(ComponentKind)
    forbidden = re.compile(r"#|\brgba?\s*\(|\b(?:background-)?color\s*:|gradient|url\s*\(", re.IGNORECASE)
    assert not any(forbidden.search(stylesheet) for stylesheet in STATIC_STRUCTURE_STYLES.values())


def test_component_style_applies_stable_kind_density_and_state_properties(qapp) -> None:
    for kind in ComponentKind:
        widget = QWidget()
        ComponentStyle.apply_static(widget, kind, ComponentDensity.COMPACT)
        for state in SemanticState:
            ComponentStyle.apply_state(widget, state)
            assert widget.property("tbSemanticState") == state.value

        assert widget.property("tbComponentKind") == kind.value
        assert widget.property("tbDensity") == ComponentDensity.COMPACT.value
        assert widget.styleSheet() == ""
        widget.deleteLater()
    qapp.processEvents()


def test_common_helpers_preserve_qt_ownership_and_accessible_text(qapp) -> None:
    host = QWidget()
    button = make_primary_button("开始翻译", host)
    card = ThemedCard(host)
    badge = StatusBadge("运行中", SemanticState.INFO, host)
    dialog = configure_dialog(QDialog(host))

    assert button.parent() is host
    assert button.accessibleName() == "开始翻译"
    assert button.property("tbSemanticState") == SemanticState.PRIMARY.value
    assert card.property("tbComponentKind") == ComponentKind.CARD.value
    assert badge.text() == "运行中"
    assert badge.accessibleDescription() == "运行中"
    assert dialog.parent() is host
    assert dialog.property("tbDialog") is True
    host.deleteLater()
    qapp.processEvents()


def test_palette_propagates_to_existing_and_new_standard_components(qapp) -> None:
    original = QPalette(qapp.palette())
    host = QWidget()
    existing = QLineEdit(host)
    ComponentStyle.apply_static(existing, ComponentKind.INPUT)
    changed = QPalette(original)
    changed.setColor(QPalette.ColorRole.Base, QColor(17, 31, 47))
    try:
        qapp.setPalette(changed)
        qapp.processEvents()
        created_after = QPushButton("After", host)
        ComponentStyle.apply_static(created_after, ComponentKind.BUTTON)

        assert existing.palette().color(QPalette.ColorRole.Base) == QColor(17, 31, 47)
        assert created_after.palette().color(QPalette.ColorRole.Base) == QColor(17, 31, 47)
    finally:
        qapp.setPalette(original)
        host.deleteLater()
        qapp.processEvents()
