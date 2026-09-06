from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from transbridge.ui.tools.terminology_profiles import source_catalog


def _config(**overrides):
    values = {
        "local_json_path": "D:/terms/base.json",
        "local_csv_path": "",
        "local_excel_path": "D:/terms/legacy.xlsx",
        "excel_original_col": "C",
        "excel_translation_col": "D",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_catalog_lists_each_project_plugin_and_configured_local_source_once(monkeypatch) -> None:
    monkeypatch.setattr(source_catalog, "bound_paratranz_project", lambda _context: None)
    monkeypatch.setattr(source_catalog.LLMConfig, "load_from_file", lambda: _config())
    context = SimpleNamespace(
        project_sources=(
            {
                "source_id": "plugin",
                "name": "Dragonborn",
                "format_id": "plugin.sse",
                "location": "D:/mods/Dragonborn.esm",
                "enabled": True,
            },
            {
                "source_id": "duplicate",
                "format_id": "plugin.sse",
                "location": "D:/mods/Dragonborn.esm",
                "enabled": True,
            },
            {"source_id": "translation", "format_id": "xml.eet", "location": "D:/translation.xml"},
        )
    )

    choices = source_catalog.configured_source_selections(context)

    assert [choice.request.source_id for choice in choices] == ["dynamic", "json", "excel"]
    assert choices[0].request.esp_path == "D:/mods/Dragonborn.esm"
    assert Path(choices[1].request.file_path) == Path("D:/terms/base.json")
    assert choices[2].request.excel_original_column == "C"
    assert choices[2].request.excel_translation_column == "D"


def test_explicit_local_file_becomes_an_independent_single_source_request(monkeypatch) -> None:
    monkeypatch.setattr(source_catalog.LLMConfig, "load_from_file", lambda: _config())

    choice = source_catalog.local_file_selection("D:/imports/community.csv")

    assert choice.request.source_id == "csv"
    assert Path(choice.request.file_path) == Path("D:/imports/community.csv")
    assert choice.label == "本地 CSV · community.csv"
    assert "独立" in choice.detail
