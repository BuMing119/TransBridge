"""The common cloud controls remain readable with the real 14pt theme."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.paratranz import files_tab, string_detail_dialog, strings_tab

from .test_paratranz_query_context import _FILES, _ROWS, _Context, _Worker


@pytest.fixture
def theme(layout_app):
    service = ThemeService(
        layout_app,
        create_builtin_registry(),
        SimpleNamespace(
            load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
        ),
    )
    service.start()
    yield service
    service.close()


def test_detail_filters_have_readable_labels_in_a_narrow_sidebar(layout_app, theme, monkeypatch):
    monkeypatch.setattr(string_detail_dialog, "ApiWorker", _Worker)
    context = _Context()
    context.current_user = {"id": 1}
    widget = string_detail_dialog.StringDetailDialog(_ROWS, 0, context, 7)
    widget.resize(760, 560)
    widget.show()
    try:
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            theme.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            apply_layout_font(widget, theme.snapshot(), 14)
            widget._filter_toggle.setChecked(True)
            settle_layout(layout_app)
            assert widget._stage_edit.font().pointSizeF() == 14
            for control in (*widget._stage_checks.values(), *widget._modifier_group.buttons()):
                assert control.width() >= control.minimumSizeHint().width(), control.text()
                assert widget.rect().contains(
                    control.rect().translated(control.mapTo(widget, control.rect().topLeft()))
                )
            assert widget.width() == 760
            assert widget._save_btn.isVisible()
            assert widget._nav_list.height() >= 60
    finally:
        widget.close()
        widget.deleteLater()
        settle_layout(layout_app)


@pytest.mark.parametrize("module,kind", [(files_tab, "FilesTab"), (strings_tab, "StringsTab")])
def test_common_page_actions_fit_the_space_beside_the_project_list(layout_app, theme, monkeypatch, module, kind):
    monkeypatch.setattr(module, "ApiWorker", _Worker)
    context = _Context()
    widget = getattr(module, kind)(context)
    context.select({"id": 7})
    widget.resize(520, 440)
    widget.show()
    try:
        if kind == "FilesTab":
            widget._workers[-1].result.emit(_FILES)
        else:
            widget._workers[-2].result.emit(_FILES)
            widget._workers[-1].result.emit(_ROWS)
        widget._table.selectRow(0)
        for mode in (ThemeMode.LIGHT, ThemeMode.DARK):
            theme.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
            apply_layout_font(widget, theme.snapshot(), 14)
            settle_layout(layout_app)
            widget.resize(520, 440)
            settle_layout(layout_app)
            assert widget._table.font().pointSizeF() == 14
            assert widget.width() == 520
            for button in widget.findChildren(QPushButton):
                if button.isVisible():
                    assert button.width() >= button.minimumSizeHint().width(), button.text()
                    assert widget.rect().contains(
                        button.rect().translated(button.mapTo(widget, button.rect().topLeft()))
                    )
    finally:
        widget.close()
        widget.deleteLater()
        settle_layout(layout_app)
