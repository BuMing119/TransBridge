from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.ai_translator.term_source_reader import (
    ConfiguredTermSourceReader,
    TermSourceReadRequest,
    TermSourceUnavailableError,
)


def _request(source_id: str, **kwargs) -> TermSourceReadRequest:
    return TermSourceReadRequest(source_id, source_id, **kwargs)


@pytest.mark.parametrize("source_id,suffix", (("json", ".json"), ("csv", ".csv")))
def test_reader_loads_one_local_source_strictly(tmp_path: Path, source_id: str, suffix: str) -> None:
    path = tmp_path / f"terms{suffix}"
    if source_id == "json":
        path.write_text(json.dumps([{"term": "Whiterun", "translation": "白漫城"}]), encoding="utf-8")
    else:
        path.write_text("term,translation\nWhiterun,白漫城\n", encoding="utf-8-sig")

    snapshot = ConfiguredTermSourceReader().read(_request(source_id, file_path=str(path)))

    assert [(item.original, item.translation) for item in snapshot.entries] == [("Whiterun", "白漫城")]
    assert snapshot.source_id == source_id


def test_reader_loads_excel_with_configured_columns(tmp_path: Path) -> None:
    path = tmp_path / "terms.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["ignored", "Whiterun", "白漫城"])
    sheet.append(["ignored", "Solitude", "独孤城"])
    workbook.save(path)
    workbook.close()

    snapshot = ConfiguredTermSourceReader().read(
        _request("excel", file_path=str(path), excel_original_column="B", excel_translation_column="C")
    )

    assert [(item.original, item.translation) for item in snapshot.entries] == [("Solitude", "独孤城")]


def test_reader_resolves_dynamic_database_without_creating_or_using_merged_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "ai_translator" / "Content" / "Content_terms.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"term": "Dragon", "translation": "巨龙", "source": "manual"}]), encoding="utf-8")
    monkeypatch.setattr("transbridge.ai_translator.term_source_reader.get_data_dir", lambda: str(tmp_path))

    snapshot = ConfiguredTermSourceReader().read(_request("dynamic", esp_path="C:/mods/Content.esp"))

    assert [(item.original, item.translation) for item in snapshot.entries] == [("Dragon", "巨龙")]


class _ParaTranzService:
    def __init__(self, *, stable: bool = True) -> None:
        self.stable = stable
        self.closed = False

    def snapshot_terms(self, project_id: int):
        assert project_id == 42
        entry = TermEntry("Whiterun", "白漫城", "paratranz")
        return SimpleNamespace(stable=self.stable, items=(SimpleNamespace(entry=entry),))

    def close(self) -> None:
        self.closed = True


def test_reader_uses_typed_paratranz_snapshot_and_closes_service() -> None:
    service = _ParaTranzService()
    snapshot = ConfiguredTermSourceReader(lambda: service).read(_request("paratranz", paratranz_project_id=42))

    assert snapshot.entries[0].translation == "白漫城"
    assert service.closed


def test_reader_rejects_missing_files_unbound_or_unstable_sources(tmp_path: Path) -> None:
    with pytest.raises(TermSourceUnavailableError, match="不存在"):
        ConfiguredTermSourceReader().read(_request("json", file_path=str(tmp_path / "missing.json")))
    with pytest.raises(TermSourceUnavailableError, match="尚未绑定"):
        ConfiguredTermSourceReader().read(_request("paratranz"))
    with pytest.raises(TermSourceUnavailableError, match="发生变化"):
        ConfiguredTermSourceReader(lambda: _ParaTranzService(stable=False)).read(
            _request("paratranz", paratranz_project_id=42)
        )
