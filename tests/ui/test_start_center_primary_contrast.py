"""Task cards retain readable text and artwork through theme and state changes."""

from collections import Counter
from types import SimpleNamespace

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QLabel
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.accessibility import contrast_ratio
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.shell.start_center import StartCenterViewState, StartCenterWidget, StartDestinationState


def _opaque_colour(pixmap):
    image = pixmap.toImage()
    colours = Counter(
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 240
    )
    return colours.most_common(1)[0][0]


def _rendered_colours(view, label):
    rectangle = label.rect().translated(label.mapTo(view, QPoint()))
    image = view.grab().toImage().copy(rectangle)
    return Counter(image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()))


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_primary_card_has_readable_text_and_icons_across_theme_switch_and_disabled_state(layout_app, mode):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    assert service.start().status is not ThemeApplyStatus.FAILED
    view = StartCenterWidget()
    view.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, revision=1))
    view.resize(850, 650)
    view.show()
    try:
        for current in (mode, ThemeMode.DARK if mode is ThemeMode.LIGHT else ThemeMode.LIGHT):
            result = service.set_preference(ThemePreference(current, DEFAULT_THEME_ID), persist=False)
            assert result.status is not ThemeApplyStatus.FAILED
            assert result.snapshot.effective_scheme is (
                ThemeScheme.LIGHT if current is ThemeMode.LIGHT else ThemeScheme.DARK
            )
            settle_layout(layout_app)
            primary = view.choose_plugin_button
            expected = layout_app.palette().color(QPalette.ColorRole.Base).name()
            background = layout_app.palette().color(QPalette.ColorRole.Link).name()
            sample = primary.mapTo(view, QPoint(primary.width() // 2, 4))
            assert view.grab().toImage().pixelColor(sample).name() == background
            assert contrast_ratio(expected, background) >= 4.5
            labels = primary.findChildren(QLabel)
            text_labels = [label for label in labels if label.text()]
            artwork = [label for label in labels if not label.pixmap().isNull()]
            assert len(text_labels) == 2
            assert len(artwork) == 2
            for label in text_labels:
                # Inspect glyphs inside the complete rendered card, not an isolated label background.
                colours = _rendered_colours(view, label)
                assert colours[expected] >= 10, (current, label.text(), colours.most_common(5))
            assert all(_opaque_colour(label.pixmap()) == expected for label in artwork)
            primary.setEnabled(False)
            settle_layout(layout_app)
            disabled = layout_app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
            # Retain the existing disabled QSS text role instead of applying the enabled on-accent colour.
            disabled_text = layout_app.palette().color(QPalette.ColorRole.Text).name()
            assert all(_rendered_colours(view, label)[disabled_text] >= 10 for label in text_labels)
            assert all(_opaque_colour(label.pixmap()) == disabled for label in artwork)
            primary.setEnabled(True)
            settle_layout(layout_app)
            assert view.grab().toImage().pixelColor(sample).name() == background
            assert all(_rendered_colours(view, label)[expected] >= 10 for label in text_labels)
            assert all(_opaque_colour(label.pixmap()) == expected for label in artwork)
    finally:
        view.close()
        service.close()


def test_secondary_task_card_icons_refresh_with_theme_and_enabled_state(layout_app):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    view = StartCenterWidget()
    view.render(StartCenterViewState(StartDestinationState.START_CENTER_EMPTY, revision=1))
    view.resize(720, 580)
    view.show()
    settle_layout(layout_app)
    cards = [
        card for card in view.findChildren(type(view.choose_plugin_button)) if card is not view.choose_plugin_button
    ]
    assert len(cards) >= 2
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
            service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            settle_layout(layout_app)
            for card in cards:
                artwork = [label for label in card.findChildren(QLabel) if not label.pixmap().isNull()]
                assert len(artwork) == 2
                for enabled in (True, False, True):
                    card.setEnabled(enabled)
                    settle_layout(layout_app)
                    for label in artwork:
                        role = (
                            QPalette.ColorRole.Shadow
                            if label.property("tbStartActionArrow")
                            else QPalette.ColorRole.Text
                        )
                        expected = (
                            layout_app.palette().color(role)
                            if enabled
                            else layout_app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
                        )
                        assert _opaque_colour(label.pixmap()) == expected.name()
    finally:
        view.close()
        view.deleteLater()
        service.close()
