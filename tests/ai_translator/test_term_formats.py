"""Pure format-layer tests for canonical terminology adapters."""

from __future__ import annotations

import base64
import csv
import json
import zlib

from openpyxl import Workbook

from transbridge.ai_translator.term_formats import (
    TermEntry,
    dump_terms_csv,
    dump_terms_json,
    load_terms_csv,
    load_terms_excel,
    load_terms_json,
    term_entry_from_mapping,
    term_entry_to_paratranz_dict,
)


def _complete_entry() -> TermEntry:
    return TermEntry(
        term="Whiterun",
        translation="白漫城",
        source="paratranz",
        context="Skyrim cities",
        created_at="2026-08-26T10:20:30Z",
        case_sensitive=True,
        variants=["White Run", "Snow City"],
        pos="proper noun",
        note="City name",
        external_id=731,
        metadata={
            "projectId": 42,
            "updatedAt": "2026-08-26T11:22:33Z",
            "nested": {"reviewed": True},
        },
    )


def test_paratranz_fields_and_unknown_metadata_are_normalized() -> None:
    entry = term_entry_from_mapping(
        {
            "term": "Whiterun",
            "translation": "白漫城",
            "pos": "proper noun",
            "note": "City name",
            "variants": ["White Run", "Snow City"],
            "caseSensitive": True,
            "id": 731,
            "createdAt": "2026-08-26T10:20:30Z",
            "projectId": 42,
            "updatedAt": "2026-08-26T11:22:33Z",
            "reviewState": {"approved": True},
        },
        source="paratranz",
    )

    assert entry == TermEntry(
        term="Whiterun",
        translation="白漫城",
        source="paratranz",
        created_at="2026-08-26T10:20:30Z",
        case_sensitive=True,
        variants=["White Run", "Snow City"],
        pos="proper noun",
        note="City name",
        external_id=731,
        metadata={
            "projectId": 42,
            "updatedAt": "2026-08-26T11:22:33Z",
            "reviewState": {"approved": True},
        },
    )


def test_canonical_json_round_trip_preserves_every_field(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "canonical-terms.json"
    expected = _complete_entry()

    dump_terms_json(path, [expected], target="canonical")

    assert load_terms_json(path, source=None) == [expected]


def test_paratranz_payload_excludes_read_only_and_canonical_only_fields() -> None:
    entry = _complete_entry()

    assert term_entry_to_paratranz_dict(entry) == {
        "term": "Whiterun",
        "translation": "白漫城",
        "variants": ["White Run", "Snow City"],
        "caseSensitive": True,
        "pos": "proper noun",
        "note": "City name",
    }


def test_json_array_is_loaded(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "array.json"
    path.write_text(
        json.dumps(
            [
                {
                    "original": "Whiterun",
                    "translation": "白漫城",
                    "variants": ["White Run"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_terms_json(path) == [
        TermEntry(term="Whiterun", translation="白漫城", source="json", variants=["White Run"])
    ]


def test_json_simple_mapping_is_loaded(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "mapping.json"
    path.write_text(
        json.dumps({"Whiterun": "白漫城", "Dragonborn": "龙裔"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_terms_json(path) == [
        TermEntry(term="Whiterun", translation="白漫城", source="json"),
        TermEntry(term="Dragonborn", translation="龙裔", source="json"),
    ]


def test_json_results_wrapper_is_loaded(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "results.json"
    path.write_text(
        json.dumps(
            {
                "page": 1,
                "results": [
                    {
                        "term": "Whiterun",
                        "translation": "白漫城",
                        "caseSensitive": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert load_terms_json(path, source="paratranz") == [
        TermEntry(term="Whiterun", translation="白漫城", source="paratranz", case_sensitive=True)
    ]


def test_csv_with_headers_normalizes_aliases_and_unknown_columns(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "headers.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "term",
                "translation",
                "pos",
                "note",
                "variants",
                "caseSensitive",
                "id",
                "projectId",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "term": "Whiterun",
            "translation": "白漫城",
            "pos": "proper noun",
            "note": "City name",
            "variants": json.dumps(["White Run", "Snow City"]),
            "caseSensitive": "true",
            "id": "731",
            "projectId": "42",
        })

    assert load_terms_csv(path) == [
        TermEntry(
            term="Whiterun",
            translation="白漫城",
            source="csv",
            case_sensitive=True,
            variants=["White Run", "Snow City"],
            pos="proper noun",
            note="City name",
            external_id=731,
            metadata={"projectId": "42"},
        )
    ]


def test_csv_without_headers_falls_back_to_two_columns(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "two-columns.csv"
    path.write_text("Whiterun,白漫城\nDragonborn,龙裔\n", encoding="utf-8")

    assert load_terms_csv(path) == [
        TermEntry(term="Whiterun", translation="白漫城", source="csv"),
        TermEntry(term="Dragonborn", translation="龙裔", source="csv"),
    ]


def test_csv_round_trip_preserves_every_field(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "canonical.csv"
    expected = _complete_entry()

    dump_terms_csv(path, [expected])

    assert load_terms_csv(path, source=None) == [expected]


def test_excel_with_complete_headers_normalizes_every_field(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "complete.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append([
        "term",
        "translation",
        "pos",
        "note",
        "variants",
        "caseSensitive",
        "context",
        "createdAt",
        "id",
        "source",
        "metadata",
        "projectId",
    ])
    sheet.append([
        "Whiterun",
        "白漫城",
        "proper noun",
        "City name",
        json.dumps(["White Run", "Snow City"]),
        True,
        "Skyrim cities",
        "2026-08-26T10:20:30Z",
        731,
        "paratranz",
        json.dumps({"updatedAt": "2026-08-26T11:22:33Z"}),
        42,
    ])
    workbook.save(path)
    workbook.close()

    assert load_terms_excel(path, source=None) == [
        TermEntry(
            term="Whiterun",
            translation="白漫城",
            source="paratranz",
            context="Skyrim cities",
            created_at="2026-08-26T10:20:30Z",
            case_sensitive=True,
            variants=["White Run", "Snow City"],
            pos="proper noun",
            note="City name",
            external_id=731,
            metadata={"updatedAt": "2026-08-26T11:22:33Z", "projectId": 42},
        )
    ]


def test_excel_without_recognized_headers_uses_legacy_two_column_fallback(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "legacy.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Source text", "Localized text"])
    sheet.append(["Whiterun", "白漫城"])
    sheet.append(["Dragonborn", "龙裔"])
    workbook.save(path)
    workbook.close()

    assert load_terms_excel(path) == [
        TermEntry(term="Whiterun", translation="白漫城", source="excel"),
        TermEntry(term="Dragonborn", translation="龙裔", source="excel"),
    ]


def test_legacy_xls_is_loaded_through_the_canonical_adapter(tm_tmp_dir) -> None:
    path = tm_tmp_dir / "legacy.xls"
    compressed_fixture = "eNrtWE1IFVEU/u68//f8eZoGGshDyErdRC3a6LOipEViFhgR1OgbaFJnHvNGwSCyzGUgtCraCG5qYbXphwpq1yIyahEEgdayVVDQQp3OPW9meJYLH5RYzPe4557vnHvvnHf/Z97M1yzM3G9cxC/oRAgrTgLREpuglPBIGuR3HKl6eZySE+CfQiJOAxmN4Enlq5gcQznei1BwL/yCJPCJ0mnk0WMaWmYDcYBjUIWMoYOkwC2yVKGBo6plOchyC8u7XPIpyy62XGPZQWUXxCnMZ3ta97mz+KTSzL4qyHYfcp0PbNmNeryUs/jStCiWjWC/pavDm9PRFK7ALGjcujVDs9ThBdTRAM7iu5MBvnkr9XkmsG+sXYDsP1bbY2vYrythYALOWZ7gU6jE3rD0RHBCs0YKS5gmEziR3SZbijJLNQrDqq2bRgjImwVyGaat0e47ptKsMOwCVRhUC9pxzSjotj6m0dTWc7Rz5y3zvDZoH8lR2f5zOjU4Sm2Ii2Nt5uv+FPvzmpUxzFGDSh/U7fGMoY5oCbd0pm/USMozgPeM9Ko9o5LXUgXJHKpZr+EVlaZTYen217dHB3qzZ9gywedE8TTZLjsADi7LGlS5ij0KvNOklfU2lle41W2sN7Kso46ivKW33lUOT3KZq+xtoRb2MN5ld5ToO0mf+nLsUdPU5+wu0ue6Fy/Uzb3PzqCZTrcc1Ze/SbSLdnHzhsTjrJcLd+f5yLLht10orqTd2B33yKzGMpKs1rAsMtk7wmeK21dFFiIW8lmYWNhnEWIRn0WJRX0WIxZzYxBrxCA4hrhbXnAMCZ/JGJI+kzGkfCZjqJBPV+K898jYn1FPCI7ASMl273AEnUotHvDwdZXcG5IIECBAgAABAgQIsFkg3Pt2qPiOwbfLKN/lit91limtBJ9J/lv0waSfTS+mh2BQbmG8rPmzFRHhtSXWWcf7XijRT0+3MIQBjmOo7PlL72Oi9P+su2L6zy2hcp+/Uk6cf/n5PwGPr/Cv"  # noqa: E501
    path.write_bytes(zlib.decompress(base64.b64decode(compressed_fixture)))

    assert load_terms_excel(path) == [
        TermEntry(
            term="Whiterun",
            translation="白漫城",
            source="excel",
            case_sensitive=True,
            variants=["White Run"],
            pos="proper noun",
            note="City name",
            external_id=731,
            metadata={"projectId": 42.0},
        )
    ]
