from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_explicit_excel_import_preserves_configured_column_mapping(monkeypatch):
    monkeypatch.setattr(source_catalog.LLMConfig, "load_from_file", lambda: _config())
    choice = source_catalog.local_file_selection("D:/imports/community.xlsx")
    assert choice.request.source_id == "excel"
    assert choice.request.excel_original_column == "C"
    assert choice.request.excel_translation_column == "D"


def test_explicit_local_file_becomes_an_independent_single_source_request(monkeypatch) -> None:
    monkeypatch.setattr(source_catalog.LLMConfig, "load_from_file", lambda: _config())

    choice = source_catalog.local_file_selection("D:/imports/community.csv")

    assert choice.request.source_id == "csv"
    assert Path(choice.request.file_path) == Path("D:/imports/community.csv")
    assert choice.label == "本地 CSV · community.csv"
    assert "独立" in choice.detail


@pytest.mark.parametrize("extension,source", [("json", "json"), ("csv", "csv"), ("xls", "excel"), ("xlsx", "excel")])
def test_explicit_file_request_never_enumerates_plugins_or_history(monkeypatch, extension, source):
    monkeypatch.setattr(source_catalog.LLMConfig, "load_from_file", lambda: _config())
    choice = source_catalog.local_file_selection(f"D:/imports/chosen.{extension}")
    assert choice.request.source_id == source
    assert Path(choice.request.file_path) == Path(f"D:/imports/chosen.{extension}")
    assert choice.request.esp_path is None


def test_bound_paratranz_request_uses_current_binding_and_frozen_configuration(monkeypatch):
    live = SimpleNamespace(
        token="original", user_id=7, base_url="https://paratranz.cn/api", timeout=15, headers={"X": "1"}
    )
    context = SimpleNamespace(
        config=live,
        current_project={"id": 99},
        paratranz_binding={"project_id": 42, "project_name": "绑定项目", "endpoint": "https://paratranz.cn/api"},
    )
    monkeypatch.setattr(source_catalog, "ParatranzConfig", lambda **values: SimpleNamespace(**values))
    monkeypatch.setattr("transbridge.paratranz.terms_service.ParaTranzTermsService.from_config", lambda config: config)
    choice = source_catalog.bound_paratranz_selection(context)
    assert choice.request.paratranz_project_id == 42
    assert "绑定项目" in choice.label
    live.token = "later"
    live.headers["X"] = "2"
    frozen = choice.reader_factory()._paratranz_factory()
    assert frozen.token == "original" and frozen.extra_headers == {"X": "1"}
    context.paratranz_binding = None
    assert source_catalog.bound_paratranz_selection(context) is None
