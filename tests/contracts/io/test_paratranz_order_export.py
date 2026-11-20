from __future__ import annotations

import json

import pytest

from tests.contracts.io.test_paratranz_adapter import _parse, _write
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.paratranz_context_order import format_ordered_context, parse_ordered_context
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.converter.translation_entry_collection_export import export_to_categorized_json_files


def _entry(key, context="INFO:NAM1|00ABCDEF", order=None):
    metadata = () if order is None else (("plugin.source_order", order),)
    return TranslationEntry(key, key, key, "", 0, context, metadata=metadata)


def _payload(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_local_export_projects_sparse_zero_based_order_without_mutation(tmp_path):
    entries = (_entry("later", order=428), _entry("first", order=0))
    before = tuple(entry.to_dict() for entry in entries)
    target = tmp_path / "out.json"
    assert _write(target, entries).outcome is OperationOutcome.COMPLETED
    rows = _payload(target)
    assert [record["key"] for record in rows] == ["00000428|TB2|later", "00000000|TB2|first"]
    assert all(record["context"] == "INFO:NAM1|00ABCDEF" for record in rows)
    assert tuple(entry.to_dict() for entry in entries) == before


def test_categorized_fallback_is_global_and_preserves_complete_dialogue_order(tmp_path):
    entries = (
        _entry("later", order=8),
        _entry("book", "BOOK:FULL"),
        _entry("first", order=2),
    )
    export_to_categorized_json_files(TranslationEntryCollection(entries), tmp_path)
    dialogue = _payload(next(tmp_path.glob("任务/对话_*.json")))
    assert [record["key"] for record in dialogue] == ["00000000|TB2|first", "00000002|TB2|later"]
    assert _payload(tmp_path / "书籍_书名.json")[0]["key"] == "00000001|TB2|book"


@pytest.mark.parametrize("orders", [(2, None, 0), (1, 1, 0)])
def test_incomplete_or_duplicate_source_order_uses_unique_input_positions(tmp_path, orders):
    entries = tuple(_entry(str(index), order=order) for index, order in enumerate(orders))
    target = tmp_path / "out.json"
    assert _write(target, entries).outcome is OperationOutcome.COMPLETED
    assert [record["key"] for record in _payload(target)] == ["00000000|TB2|0", "00000001|TB2|1", "00000002|TB2|2"]


def test_categorized_subset_preserves_global_positions_for_free_text_without_dialogue_evidence(tmp_path):
    complete = (_entry("book", "BOOK:FULL"), _entry("note", "12345678|user note"), _entry("empty", None))
    export_to_categorized_json_files(
        TranslationEntryCollection(complete[1:]), tmp_path, classification_entries=complete
    )
    note, empty = _payload(tmp_path / "其他.json")
    assert (note["key"], note["context"]) == ("00000001|TB2|note", "12345678|user note")
    assert empty["key"] == "00000002|TB2|empty" and "context" not in empty


def test_order_does_not_hide_a_duplicate_identity(tmp_path):
    result = _write(tmp_path / "duplicate.json", (_entry("same", order=2), _entry("same", order=7)))
    assert result.outcome is OperationOutcome.FAILED
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"PARATRANZ_KEY_DUPLICATE"}


def test_generic_paratranz_dto_roundtrip_does_not_acquire_prefix(tmp_path):
    target = tmp_path / "out.json"
    source = tmp_path / "in.json"
    source.write_text('[{"key":"k","original":"x","translation":"","stage":0,"context":"BOOK:FULL"}]')
    assert _write(target, _parse(source).entries).outcome is OperationOutcome.COMPLETED
    assert _payload(target) == _payload(source)


def test_overflow_fails_before_overwriting_target(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("existing")
    result = _write(target, (_entry("k", order=100_000_000),))
    assert result.outcome is OperationOutcome.FAILED
    assert target.read_text() == "existing"
    assert "99999999" in result.diagnostics[0].message


def test_prefix_is_exact_and_never_stacks():
    assert format_ordered_context("00000007|BOOK:FULL", 8) == "00000008|BOOK:FULL"
    assert parse_ordered_context("00000008|BOOK:FULL") == ("BOOK:FULL", 8)
    assert format_ordered_context("BOOK:FULL", 99_999_999) == "99999999|BOOK:FULL"
    for value in (None, "", "00000008|User text", "00000008|BOOK:FULL|not-a-quest"):
        assert parse_ordered_context(value) == (value, None)
        assert format_ordered_context(value, 8) == value
