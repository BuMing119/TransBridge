"""Compatibility coverage for terminology sources and their caches."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.paratranz.config_manager import LLMConfig


def _config(**overrides) -> SimpleNamespace:
    values = {
        "retrieval_enabled": True,
        "enable_semantic_match": False,
        "embedding": SimpleNamespace(mode="disabled"),
        "term_priority": [],
        "local_json_path": "",
        "local_csv_path": "",
        "local_excel_path": "",
        "excel_original_col": "A",
        "excel_translation_col": "B",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _redirect_term_data(monkeypatch, tmp_path: Path) -> None:
    def get_ai_translator_dir(stem: str) -> str:
        directory = tmp_path / "ai_translator" / stem
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(get_ai_translator_dir))


def test_csv_source_loads_and_priority_order_controls_duplicate_merge(monkeypatch, tmp_path):
    _redirect_term_data(monkeypatch, tmp_path)
    csv_path = tmp_path / "terms.csv"
    csv_path.write_text(
        "term,translation,pos,note,caseSensitive,variants\n"
        'Shared,CSV 译文,noun,CSV 注释,true,"shared alias|shared name"\n'
        "CsvOnly,仅 CSV,,,,\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "terms.json"
    json_path.write_text(
        json.dumps(
            [
                {"term": "Shared", "translation": "JSON 译文"},
                {"term": "JsonOnly", "translation": "仅 JSON"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manager = TermDatabaseManager(
        _config(
            term_priority=["csv", "json"],
            local_csv_path=str(csv_path),
            local_json_path=str(json_path),
        ),
        "priority.esp",
    )

    merged = manager.load_all()

    assert merged == {"Shared": "CSV 译文", "JsonOnly": "仅 JSON", "CsvOnly": "仅 CSV"}
    shared = next(entry for entry in manager._merged_terms if entry.term == "Shared")
    assert shared.source == "csv"
    assert shared.pos == "noun"
    assert shared.note == "CSV 注释"
    assert shared.case_sensitive is True
    assert shared.variants == ["shared alias", "shared name"]


def test_paratranz_pagination_preserves_known_read_only_and_unknown_fields():
    class FakeParatranzClient:
        def __init__(self) -> None:
            self.calls = []

        def list_terms(self, project_id, *, page, page_size):
            self.calls.append((project_id, page, page_size))
            if page == 1:
                return {
                    "results": [
                        {"term": f"Filler {index}", "translation": f"填充 {index}"} for index in range(page_size)
                    ]
                }
            if page == 2:
                return {
                    "results": [
                        {
                            "id": 731,
                            "term": "Dragonborn",
                            "translation": "龙裔",
                            "pos": "noun",
                            "note": "Main quest title",
                            "variants": ["Dovahkiin"],
                            "caseSensitive": True,
                            "updatedAt": "2026-08-26T12:00:00Z",
                            "owner": {"id": 9, "name": "tester"},
                        }
                    ]
                }
            raise AssertionError(f"unexpected page: {page}")

    client = FakeParatranzClient()
    manager = object.__new__(TermDatabaseManager)
    manager._paratranz_client = client
    manager._project_id = 42

    entries = manager._load_paratranz()

    assert client.calls == [(42, 1, 100), (42, 2, 100)]
    assert len(entries) == 101
    dragonborn = entries[-1]
    assert dragonborn.term == "Dragonborn"
    assert dragonborn.translation == "龙裔"
    assert dragonborn.source == "paratranz"
    assert dragonborn.pos == "noun"
    assert dragonborn.note == "Main quest title"
    assert dragonborn.external_id == 731
    assert dragonborn.case_sensitive is True
    assert dragonborn.variants == ["Dovahkiin"]
    assert dragonborn.metadata == {
        "updatedAt": "2026-08-26T12:00:00Z",
        "owner": {"id": 9, "name": "tester"},
    }


def test_dynamic_database_reads_legacy_json_and_saves_all_canonical_fields(monkeypatch, tmp_path):
    _redirect_term_data(monkeypatch, tmp_path)
    database = DynamicTermDatabase("LegacyProject.esp")
    legacy_payload = [
        {
            "term": "Whiterun",
            "translation": "白漫城",
            "source": "manual",
            "context": "Hold capital",
            "created_at": "2024-01-02T03:04:05",
            "case_sensitive": True,
            "variants": ["White Run"],
        }
    ]
    Path(database._path).write_text(json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    database.load()

    entry = database.as_list()[0]
    assert entry.term == "Whiterun"
    assert entry.translation == "白漫城"
    assert entry.source == "manual"
    assert entry.context == "Hold capital"
    assert entry.created_at == "2024-01-02T03:04:05"
    assert entry.case_sensitive is True
    assert entry.variants == ["White Run"]

    database.save()

    saved = json.loads(Path(database._path).read_text(encoding="utf-8"))
    assert saved == [
        {
            "term": "Whiterun",
            "translation": "白漫城",
            "source": "manual",
            "context": "Hold capital",
            "created_at": "2024-01-02T03:04:05",
            "case_sensitive": True,
            "variants": ["White Run"],
            "pos": "",
            "note": "",
            "external_id": None,
            "metadata": {},
        }
    ]


def test_corrupt_local_source_falls_back_to_existing_source_cache(monkeypatch, tmp_path):
    _redirect_term_data(monkeypatch, tmp_path)
    json_path = tmp_path / "cached-terms.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "term": "Solitude",
                    "translation": "独孤城",
                    "note": "cached metadata",
                    "vendorFlag": "retained",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = _config(term_priority=["json"], local_json_path=str(json_path))
    first_manager = TermDatabaseManager(config, "cache-fallback.esp")
    assert first_manager.load_all() == {"Solitude": "独孤城"}

    json_path.write_text("{not valid json", encoding="utf-8")
    second_manager = TermDatabaseManager(config, "cache-fallback.esp")

    assert second_manager.load_all() == {"Solitude": "独孤城"}
    cached_entry = second_manager._merged_terms[0]
    assert cached_entry.note == "cached metadata"
    assert cached_entry.metadata == {"vendorFlag": "retained"}
    assert len(second_manager.get_load_log()) == 1
    source, count, detail = second_manager.get_load_log()[0]
    assert (source, count) == ("json", 1)
    assert detail is not None and detail.startswith("from cache (source failed:")
