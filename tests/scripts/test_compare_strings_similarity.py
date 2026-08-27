from __future__ import annotations

import csv
from pathlib import Path

from scripts.skyrim_strings_overlap_audit.compare_strings_similarity import (
    compare_sources,
    discover_files,
    literal_similarity,
    normalize_text,
    write_reports,
)
from scripts.skyrim_strings_overlap_audit.compare_strings_similarity_en import write_reports_english
from transbridge.parser.strings_file import SkyrimStringsWriter


def _write_strings(path: Path, entries: dict[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(SkyrimStringsWriter.to_bytes(entries, length_prefixed=path.suffix.lower() != ".strings"))


def test_normalization_and_literal_similarity_measure_wording_overlap() -> None:
    assert normalize_text(" 龙\r\n裔　") == "龙裔"
    assert literal_similarity("完全相同", "完全相同") == 1.0
    assert literal_similarity("雪漫城守卫", "雪漫城卫兵") > literal_similarity("雪漫城守卫", "黑暗兄弟会")


def test_discovery_filters_language_and_rejects_duplicate_logical_files(tmp_path: Path) -> None:
    _write_strings(tmp_path / "Strings" / "Skyrim_chinese.STRINGS", {1: "中文"})
    _write_strings(tmp_path / "Strings" / "Skyrim_english.STRINGS", {1: "English"})

    discovered = discover_files(tmp_path, "chinese")

    assert list(discovered) == ["skyrim.strings"]
    assert discovered["skyrim.strings"].logical_name == "Skyrim.strings"


def test_compare_sources_aligns_by_file_and_string_id_and_writes_reports(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    output = tmp_path / "report"
    _write_strings(
        left / "Strings" / "Skyrim_chinese.STRINGS",
        {1: "完全相同", 2: "雪漫城守卫", 3: "只在左边", 5: "   "},
    )
    _write_strings(
        right / "strings" / "skyrim_CHINESE.strings",
        {1: "完全相同", 2: "雪漫城卫兵", 4: "只在右边", 5: ""},
    )

    details, files, summary = compare_sources(
        left,
        right,
        language="chinese",
        high_threshold=0.85,
        medium_threshold=0.60,
        min_evidence_length=4,
    )
    metadata = {
        "generated_at": "2026-08-27T12:00:00+08:00",
        "left_input": str(left),
        "right_input": str(right),
        "language": "chinese",
        "metric": "test",
        "normalization": "test",
        "high_threshold": 0.85,
        "medium_threshold": 0.60,
        "min_evidence_length": 4,
    }
    paths = write_reports(output, details, files, summary, metadata)
    english_metadata = {
        **metadata,
        "left_input": "https://www.nexusmods.com/skyrimspecialedition/mods/134478",
        "right_input": "https://www.nexusmods.com/skyrimspecialedition/mods/139134",
    }
    english_paths = write_reports_english(tmp_path / "report-en", details, files, summary, english_metadata)

    assert summary["common_files"] == 1
    assert summary["common_ids"] == 3
    assert summary["only_left"] == 1
    assert summary["only_right"] == 1
    assert summary["raw_exact"] == 1
    assert summary["evidence_exact"] == 1
    assert summary["both_empty"] == 1
    assert {row["category"] for row in details} >= {"raw_exact", "only_left", "only_right", "both_empty"}
    assert all(path.exists() for path in paths)
    assert all(path.exists() for path in english_paths)
    english_summary = (tmp_path / "report-en" / "summary.md").read_text(encoding="utf-8")
    assert english_summary.startswith("# Skyrim STRINGS Literal Text Overlap Report")
    assert "<https://www.nexusmods.com/skyrimspecialedition/mods/134478>" in english_summary
    assert str(tmp_path) not in english_summary
    assert "Exact after normalization total: 1 (50.00%)" in english_summary
    with (output / "details.csv").open(encoding="utf-8-sig", newline="") as stream:
        written = list(csv.DictReader(stream))
    assert [int(row["string_id"]) for row in written] == [1, 2, 3, 4, 5]
