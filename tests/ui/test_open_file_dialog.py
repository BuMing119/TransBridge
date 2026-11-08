"""Open-file options and stable layout across optional translation states."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton
import pytest

from transbridge.ui.operations.export_format_combo import ExportFormatComboBox
from transbridge.ui.workbench.open_file_dialog import OpenFileDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    dialog = OpenFileDialog()
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()
    qapp.processEvents()


def test_open_dialog_distinguishes_all_formats_without_old_mode_switch_or_preview(view):
    assert view.windowTitle() == "打开翻译文件"
    choices = {view.format_combo.itemData(i) for i in range(view.format_combo.count())}
    assert choices == {
        None,
        "plugin.sse",
        "binary.eet",
        "xml.eet",
        "xml.xt",
        "sst.ssu8",
        "sst.ssu9",
        "json.dsd",
        "json.paratranz",
    }
    assert isinstance(view.format_combo, ExportFormatComboBox)
    assert isinstance(view.translation_format_combo, ExportFormatComboBox)
    texts = [item.text() for item in view.findChildren(QLabel) + view.findChildren(QPushButton)]
    assert not any("预览" in text or "检查" in text or "解析来源" in text for text in texts)
    assert view.translation_fields.isVisible()
    assert not view.translation_fields.isEnabled()
    assert view.translation_fields.graphicsEffect().opacity() < 1
    assert view.open_button.text() == "打开文件"


@pytest.mark.parametrize("font_size", [9, 12, 16])
def test_optional_import_and_format_changes_keep_window_and_footer_geometry(qapp, font_size):
    previous_font = qapp.font()
    qapp.setFont(QFont(previous_font.family(), font_size))
    dialog = OpenFileDialog()
    try:
        dialog.show()
        dialog.adjustSize()
        qapp.processEvents()
        expected = (dialog.size(), dialog.buttons.geometry(), dialog.minimumSizeHint())
        for importing in (True, False, True):
            dialog.with_translations.setChecked(importing)
            for source_format in ("plugin.sse", "binary.eet", "json.paratranz"):
                dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(source_format))
                for donor_format in ("strings-directory", "json.dsd", "xml.xt"):
                    dialog.translation_format_combo.setCurrentIndex(
                        dialog.translation_format_combo.findData(donor_format)
                    )
                    dialog.adjustSize()
                    qapp.processEvents()
                    assert (dialog.size(), dialog.buttons.geometry(), dialog.minimumSizeHint()) == expected
                    assert dialog.translation_fields.isVisible()
                    assert dialog.translation_fields.isEnabled() == importing
                    assert dialog.translation_fields.graphicsEffect().opacity() == (1.0 if importing else 0.45)
                    assert dialog.strings_language.isEnabled() == (importing and donor_format == "strings-directory")
    finally:
        dialog.close()
        qapp.setFont(previous_font)
        qapp.processEvents()


def test_unchecked_translation_is_not_in_open_options_and_preserves_values_when_reenabled(view):
    view.source_input.setText("D:/Mods/Main.esp")
    view.translation_input.setText("D:/Translations/existing.json")
    view.translation_format_combo.setCurrentIndex(view.translation_format_combo.findData("json.dsd"))
    view.with_translations.setChecked(True)
    view.replace_existing.setChecked(True)
    assert view.get_config().translation_path == "D:/Translations/existing.json"
    assert view.get_config().replace_existing
    view.with_translations.setChecked(False)
    options = view.get_config()
    assert options.source_paths == ("D:/Mods/Main.esp",)
    assert options.translation_path is None
    assert options.translation_format_hint is None
    assert not options.replace_existing
    view.with_translations.setChecked(True)
    assert view.get_config().translation_format_hint == "json.dsd"
    assert view.get_config().replace_existing


def test_required_input_errors_do_not_move_footer_and_prevent_accept(view, qapp):
    accepted = []
    view.accepted.connect(lambda: accepted.append(True))
    expected = (view.size(), view.buttons.geometry())
    view.open_button.click()
    qapp.processEvents()
    assert not accepted
    assert "要编辑" in view.feedback.text()
    assert (view.size(), view.buttons.geometry()) == expected
    view.source_input.setText("D:/Mods/Main.esp")
    view.with_translations.setChecked(True)
    view.open_button.click()
    qapp.processEvents()
    assert not accepted
    assert "已有译文来源" in view.feedback.text()
    assert (view.size(), view.buttons.geometry()) == expected
    view.with_translations.setChecked(False)
    view.open_button.click()
    assert accepted == [True]


def test_multiple_selected_sources_survive_until_user_edits_path(view, monkeypatch):
    paths = ["D:/Mods/First.esp", "D:/Translations/Second.eet", "D:/Mods/First.esp"]
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (paths, "支持的文件"))
    view.source_browse.click()
    assert view.get_config().source_paths == tuple(paths[:2])
    view.source_input.selectAll()
    QTest.keyClicks(view.source_input, "D:/Mods/Replacement.esp")
    assert view.get_config().source_paths == ("D:/Mods/Replacement.esp",)


def test_programmatic_single_path_replaces_previous_multi_selection(view, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (["A.esp", "B.eet"], ""))
    view.source_browse.click()
    view.source_input.setText("C.sst")
    assert view.get_config().source_paths == ("C.sst",)


@pytest.mark.parametrize("donor", [False, True])
def test_cancelled_browse_preserves_paths_and_explicit_format(view, monkeypatch, donor):
    view.source_input.setText("source.esp")
    view.with_translations.setChecked(True)
    view.translation_input.setText("translation.json")
    combo = view.translation_format_combo if donor else view.format_combo
    combo.setCurrentIndex(combo.findData("json.dsd"))
    previous = view.get_config()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: ([], "ParaTranz JSON (*.json)"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", "ParaTranz JSON (*.json)"))
    (view.translation_browse if donor else view.source_browse).click()
    assert view.get_config() == previous


@pytest.mark.parametrize("donor", [False, True])
def test_explicit_json_filter_sets_only_its_schema(view, monkeypatch, donor):
    view.with_translations.setChecked(True)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (["source.json"], "ParaTranz JSON (*.json)"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("translation.json", "DSD JSON (*.json)"))
    (view.translation_browse if donor else view.source_browse).click()
    options = view.get_config()
    assert (options.translation_format_hint if donor else options.format_hint) == (
        "json.dsd" if donor else "json.paratranz"
    )


def test_strings_uses_directory_picker_and_requires_language(view, monkeypatch):
    view.source_input.setText("source.esp")
    view.with_translations.setChecked(True)
    view.translation_format_combo.setCurrentIndex(view.translation_format_combo.findData("strings-directory"))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "D:/Mods/Strings")
    view.translation_browse.click()
    view.strings_language.setEditText("custom-language")
    assert view.get_config().translation_path == "D:/Mods/Strings"
    assert view.get_config().strings_lang == "custom-language"
    accepted = []
    view.accepted.connect(lambda: accepted.append(True))
    view.strings_language.setEditText("")
    view.open_button.click()
    assert not accepted and "Strings 语言" in view.feedback.text()


def test_long_paths_stay_within_dialog_and_have_full_tooltips(view, qapp):
    original_width = view.width()
    path = "D:/" + "很长的目录名" * 80 + "/source.eet"
    view.source_input.setText(path)
    view.translation_input.setText(path)
    view.with_translations.setChecked(True)
    view.adjustSize()
    qapp.processEvents()
    assert view.width() == original_width
    assert view.source_input.toolTip() == path
    assert view.translation_input.toolTip() == path
    assert view.rect().contains(view.buttons.geometry())
    assert view.source_input.echoMode() == view.source_input.EchoMode.Normal
    assert view.source_input.layoutDirection() == Qt.LayoutDirection.LeftToRight
