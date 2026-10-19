"""Terminology header artwork follows palette changes without changing navigation."""

from collections import Counter
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtGui import QIcon, QPalette
from PyQt6.QtWidgets import QLabel, QWidget
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.tools.terminology.view_models import TERMINOLOGY_AREAS, TerminologyArea
from transbridge.ui.tools.terminology.workbench_shell import TerminologyWorkbenchShell


@pytest.fixture
def icon_theme(layout_app):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    shells = []

    def create_shell():
        shell = TerminologyWorkbenchShell()
        for area, _label, _icon in TERMINOLOGY_AREAS:
            shell.add_area(area, QWidget(shell))
        shell.resize(920, 620)
        shell.show()
        shells.append(shell)
        settle_layout(layout_app)
        return shell

    yield service, create_shell
    for shell in shells:
        if not sip.isdeleted(shell):
            shell.close()
            shell.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle_layout(layout_app)
    service.close()


def _opaque_colours(pixmap):
    image = pixmap.toImage()
    return Counter(
        image.pixelColor(x, y).name()
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 240
    )


def _assert_icon_colours(shell, palette):
    normal = palette.color(QPalette.ColorRole.Text).name()
    checked = palette.color(QPalette.ColorRole.Link).name()
    disabled = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name()
    folder = next(label for label in shell.findChildren(QLabel) if label.accessibleName() == "当前项目")
    assert _opaque_colours(folder.pixmap())[normal] > 10
    for button in shell._area_buttons.values():
        icon = button.icon()
        assert _opaque_colours(icon.pixmap(20, 20, QIcon.Mode.Normal, QIcon.State.Off))[normal] > 10
        assert _opaque_colours(icon.pixmap(20, 20, QIcon.Mode.Normal, QIcon.State.On))[checked] > 10
        assert _opaque_colours(icon.pixmap(20, 20, QIcon.Mode.Disabled, QIcon.State.Off))[disabled] > 10


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_header_icons_use_the_initial_theme(layout_app, icon_theme, mode):
    service, create_shell = icon_theme
    service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
    shell = create_shell()

    _assert_icon_colours(shell, layout_app.palette())


def test_header_icons_follow_light_dark_light_without_changing_navigation(layout_app, icon_theme):
    service, create_shell = icon_theme
    shell = create_shell()
    shell.set_current_area(TerminologyArea.VERSIONS)
    shell._area_buttons[TerminologyArea.REPORTS].setEnabled(False)
    settle_layout(layout_app)
    states = [(button.isChecked(), button.isEnabled()) for button in shell._area_buttons.values()]
    page = shell.pages.currentWidget()

    for mode in (ThemeMode.LIGHT, ThemeMode.DARK, ThemeMode.LIGHT):
        service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
        settle_layout(layout_app)

        _assert_icon_colours(shell, layout_app.palette())
        assert shell.current_area() is TerminologyArea.VERSIONS
        assert shell.pages.currentWidget() is page
        assert [(button.isChecked(), button.isEnabled()) for button in shell._area_buttons.values()] == states


def test_destroying_shell_cancels_pending_icon_refresh(layout_app, icon_theme):
    service, create_shell = icon_theme
    shell = create_shell()
    service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    # Deliver the notification without processing the queued redraw timer.
    QCoreApplication.sendEvent(shell, QEvent(QEvent.Type.PaletteChange))
    pending = [timer for timer in shell.findChildren(QTimer) if timer.isActive()]
    assert len(pending) == 1
    fired = []
    pending[0].timeout.connect(lambda: fired.append(True))

    shell.deleteLater()
    QCoreApplication.sendPostedEvents(shell, QEvent.Type.DeferredDelete)
    settle_layout(layout_app)

    assert sip.isdeleted(shell)
    assert sip.isdeleted(pending[0])
    assert fired == []


def test_palette_only_changes_refresh_icons_without_reapplying_stylesheet(layout_app, icon_theme):
    service, create_shell = icon_theme
    shell = create_shell()
    stylesheet = layout_app.styleSheet()

    for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
        palette = service.preview(ThemePreference(mode, DEFAULT_THEME_ID)).palette
        layout_app.setPalette(palette)
        settle_layout(layout_app)

        assert layout_app.styleSheet() == stylesheet
        _assert_icon_colours(shell, palette)
