"""Parsing and migration explain missing input without discarding the open form."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QFormLayout, QStyle, QStyleOptionComboBox
import pytest

from tests.ui.responsive_layout_support import apply_layout_font, settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeService
from transbridge.ui.workbench._parse_config_dialog import ParseConfigDialog


@pytest.mark.parametrize("source", ["esp", "eet", "migrate"])
def test_missing_source_stays_open_and_explains_how_to_continue(layout_app, source):
    dialog = ParseConfigDialog("migrate" if source == "migrate" else "parse")
    dialog.show()
    try:
        if source == "eet":
            dialog._rb_eet_only.setChecked(True)
        dialog._ok_btn.click()
        settle_layout(layout_app)
        assert dialog.isVisible()
        assert dialog._feedback.isVisible()
        assert "选择" in dialog._feedback.text()
        assert dialog._feedback.property("tbSemanticState") == "error"
        assert dialog._feedback.accessibleDescription() == dialog._feedback.text()

        if source == "esp":
            dialog._esp_input.setText("D:/example.esp")
        elif source == "eet":
            dialog._eet_input.setText("D:/example.xml")
        else:
            dialog.prefill_migration_source("D:/example.json", "json", "json.dsd")
        settle_layout(layout_app)
        assert not dialog._feedback.isVisible()
        assert dialog._feedback.accessibleDescription() == ""
        dialog._ok_btn.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        if source == "migrate":
            assert dialog.get_config().json_format_id == "json.dsd"
    finally:
        dialog.close()
        dialog.deleteLater()
        settle_layout(layout_app)


def test_source_mode_updates_the_whole_form_row_and_required_hint(layout_app):
    dialog = ParseConfigDialog()
    dialog.show()
    try:
        form = next(form for form in dialog.findChildren(QFormLayout) if form.labelForField(dialog._esp_row))
        dialog._esp_input.setText("D:/keep-this-selection.esp")
        dialog._rb_eet_only.setChecked(True)
        settle_layout(layout_app)
        assert not form.labelForField(dialog._esp_row).isVisible()
        assert "*" in form.labelForField(dialog._eet_row).text()
        assert "可选" not in dialog._eet_input.placeholderText()
        dialog._rb_esp.setChecked(True)
        settle_layout(layout_app)
        assert form.labelForField(dialog._esp_row).isVisible()
        assert "可选" in dialog._eet_input.placeholderText()
        assert dialog._esp_input.text() == "D:/keep-this-selection.esp"
    finally:
        dialog.close()
        dialog.deleteLater()
        settle_layout(layout_app)


@pytest.mark.parametrize("mode", ["parse", "migrate"])
@pytest.mark.parametrize("theme", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_large_text_language_and_validation_fit_a_short_dialog(layout_app, mode, theme):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=theme, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    dialog = ParseConfigDialog(mode)
    apply_layout_font(dialog, service.snapshot(), 14)
    dialog.resize(580, 480)
    dialog.show()
    try:
        dialog._ok_btn.click()
        settle_layout(layout_app)
        assert dialog.size().width() == 580 and dialog.size().height() == 480
        assert dialog._ok_btn.font().pointSizeF() == 14
        assert dialog.rect().contains(dialog._ok_btn.mapTo(dialog, dialog._ok_btn.rect().bottomRight()))
        combo = dialog._strings_lang
        combo.setCurrentText("japanese")
        option = QStyleOptionComboBox()
        combo.initStyleOption(option)
        available = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, combo
        )
        assert available.width() >= combo.fontMetrics().horizontalAdvance(combo.currentText())
        assert dialog._feedback.height() >= dialog._feedback.heightForWidth(dialog._feedback.width())
    finally:
        dialog.close()
        dialog.deleteLater()
        settle_layout(layout_app)
        service.close()
