from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.config.language_profiles import LanguageProfile
from transbridge.ui.tools.fomod import fomod_panel as fomod_panel_module
from transbridge.ui.tools.fomod.fomod_panel import FomodPanel

_APP = QApplication.instance() or QApplication([])


def test_target_languages_come_from_shared_profiles(monkeypatch) -> None:
    monkeypatch.setattr(
        fomod_panel_module,
        "discover_language_profiles",
        lambda: (
            LanguageProfile("ja_JP", "日本語", "English", "Japanese"),
            LanguageProfile("zh_CN", "中文（简体）", "English", "Simplified Chinese"),
        ),
    )

    panel = FomodPanel()

    assert [panel._lang_combo.itemData(index) for index in range(panel._lang_combo.count())] == ["ja_JP", "zh_CN"]
    assert "日本語" in panel._lang_combo.itemText(0)
    panel.close()


def test_new_archive_prefills_safe_output_without_a_second_picker(tmp_path) -> None:
    source = tmp_path / "example.7z"
    panel = FomodPanel()

    panel.prefill_new_archive(str(source))

    assert panel._out_edit.text() == str(tmp_path / "example-translated.zip")
    panel._fmt_combo.setCurrentIndex(1)
    assert panel._out_edit.text() == str(tmp_path / "example-translated.7z")
    panel.close()


def test_manual_output_is_not_replaced_when_format_changes(tmp_path) -> None:
    panel = FomodPanel()
    panel.prefill_new_archive(str(tmp_path / "example.zip"))
    panel._out_edit.setText(str(tmp_path / "chosen.zip"))

    panel._fmt_combo.setCurrentIndex(1)

    assert panel._out_edit.text() == str(tmp_path / "chosen.zip")
    panel.close()
