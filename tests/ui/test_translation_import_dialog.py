from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel, QPushButton
import pytest

from transbridge.ui.workbench.translation_import_dialog import TranslationImportDialog

_APP = QApplication.instance() or QApplication([])


def dialog():
    return TranslationImportDialog(
        SimpleNamespace(source_snapshot=SimpleNamespace(source=SimpleNamespace(display_name="3DNPC.eet")))
    )


def test_dialog_shows_target_and_distinct_formats_without_preview_or_check_buttons():
    view = dialog()
    assert "3DNPC.eet" in view.target_label.text()
    choices = {view.format_combo.itemText(i) for i in range(view.format_combo.count())}
    assert {
        "DSD JSON",
        "ParaTranz JSON",
        "ESP-ESM Translator EET",
        "ESP-ESM Translator XML",
        "xTranslator XML",
    } <= choices
    texts = [item.text() for item in view.findChildren(QLabel) + view.findChildren(QPushButton)]
    assert not any("预览" in text or "检查" in text or "匹配" in text for text in texts)
    assert not view.get_config().replace_existing
    view.close()


@pytest.mark.parametrize(
    "kind,hint",
    [
        ("json", "json.dsd"),
        ("json", "json.paratranz"),
        ("eet", "binary.eet"),
        ("eet", "xml.eet"),
        ("xt", "xml.xt"),
        ("sst", "sst.ssu9"),
        ("plugin", "plugin.sse"),
        ("strings-directory", None),
    ],
)
def test_drop_prefill_retains_format_and_policy(kind, hint):
    view = dialog()
    assert view.prefill_migration_source("D:/translation.dat", kind, hint)
    view.replace_existing.setChecked(True)
    config = view.get_config()
    assert config.import_source_path == "D:/translation.dat"
    assert config.import_format_hint == ("strings-directory" if kind == "strings-directory" else hint)
    assert config.replace_existing
    view.close()


def test_empty_input_stays_open_and_valid_input_accepts_without_manual_check():
    view = dialog()
    accepted = []
    view.accepted.connect(lambda: accepted.append(True))
    view.import_button.click()
    assert not accepted
    assert view.feedback.text()
    view.source_input.setText("D:/translation.eet")
    view.import_button.click()
    assert accepted == [True]
    assert view.get_config().import_format_hint is None
    view.close()


@pytest.mark.parametrize(
    "chosen_filter, expected_hint",
    [
        ("支持的译文文件 (*.xml *.eet *.sst *.json *.esp *.esm *.esl)", "json.dsd"),
        ("DSD JSON (*.json)", "json.dsd"),
        ("ParaTranz JSON (*.json)", "json.paratranz"),
    ],
)
def test_browse_only_explicit_json_filter_overrides_selected_format(monkeypatch, chosen_filter, expected_hint):
    view = dialog()
    view.format_combo.setCurrentIndex(view.format_combo.findData("json.dsd"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("D:/translation.json", chosen_filter))
    view.browse_button.click()
    assert view.get_config().import_format_hint == expected_hint
    assert view.get_config().import_source_path == "D:/translation.json"
    view.close()
