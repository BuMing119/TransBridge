"""Transport keys round-trip exact local identities without remote mappings."""

from dataclasses import replace
import json

import pytest

from tests.contracts.io.test_paratranz_adapter import _parse, _write
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.paratranz_keys import ParaTranzKeyError, decode_key, encode_key
from transbridge.application.io.paratranz_mapping import map_paratranz_records, paratranz_record_from_entry
from transbridge.converter.translation_entry import TranslationEntry

NS = SourceNamespace("source:transport-test")


def qualified(original="One", key="日志:00001234|1~QUST:CNAM"):
    return TranslationEntry(
        key, key, original, "译文 " + original, 3, "QUST:CNAM", entry_key=EntryKey(NS, key, original)
    )


@pytest.mark.parametrize("original", ["", "Hello", " Hello ", "一段原文🙂", "a\nb", "a\r\nb", "e\u0301", "é"])
def test_exact_original_round_trip_without_mutating_local_key(original):
    entry = qualified(original)
    wire = encode_key(entry.identity, original)
    assert wire != entry.key
    assert decode_key(wire, original, NS) == entry.identity
    assert entry.key == "日志:00001234|1~QUST:CNAM"
    assert encode_key(replace(entry, translation="changed", stage=5).identity, original) == wire


def test_hashes_distinguish_whitespace_newlines_and_unicode_without_normalization():
    texts = ("Hello", " Hello", "Hello ", "a\nb", "a\r\nb", "é", "e\u0301")
    assert len({encode_key(qualified(text).identity, text) for text in texts}) == len(texts)


def test_ordinary_keys_unchanged_and_reserved_original_keys_are_escaped():
    for key in ("same", "__transbridge_key__:literal", encode_key(qualified().identity, "One")):
        identity = EntryKey(NS, key)
        wire = encode_key(identity, "Source")
        assert decode_key(wire, "Source", NS) == identity
        assert (wire == key) is (key == "same")


def test_changed_original_and_unsupported_version_are_not_silently_stripped():
    wire = encode_key(qualified().identity, "One")
    with pytest.raises(ParaTranzKeyError, match="哈希"):
        decode_key(wire, "Changed", NS)
    for bad in (wire.replace(":v1:", ":v2:"), "__transbridge_key__:v1:o:!bad!:hash"):
        with pytest.raises(ParaTranzKeyError):
            decode_key(bad, "One", NS)
    with pytest.raises(ParaTranzKeyError, match="身份"):
        encode_key(qualified().identity, "Changed")


def test_downloaded_json_restores_both_qualified_identities_and_reexports_same_keys(tmp_path):
    entries = (qualified("One"), qualified("Two"))
    records = [dict(paratranz_record_from_entry(entry), id=index + 20) for index, entry in enumerate(entries)]
    source = tmp_path / "downloaded.json"
    source.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    parsed = _parse(source, namespace=NS)
    assert parsed.outcome is OperationOutcome.COMPLETED, parsed.diagnostics
    assert tuple(item.entry_key for item in parsed.entries) == tuple(entry.identity for entry in entries)
    assert all(item.to_translation_entry().requires_original_match for item in parsed.entries)
    target = tmp_path / "again.json"
    written = _write(target, tuple(item.to_translation_entry() for item in parsed.entries))
    assert written.outcome is OperationOutcome.COMPLETED, written.diagnostics
    restored = json.loads(target.read_text(encoding="utf-8"))
    assert [row["key"] for row in restored] == [row["key"] for row in records]
    assert [row["id"] for row in restored] == [20, 21]


def test_direct_json_export_of_same_key_originals_succeeds(tmp_path):
    entries = (qualified("One"), qualified("Two"))
    target = tmp_path / "out.json"
    result = _write(target, entries)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    parsed = _parse(target, namespace=NS)
    assert tuple(item.entry_key for item in parsed.entries) == tuple(item.identity for item in entries)


def test_invalid_transport_row_has_diagnostic_and_other_rows_still_import():
    bad = paratranz_record_from_entry(qualified())
    bad["original"] = "Changed remotely"
    batch = map_paratranz_records([bad, {"key": "legacy", "original": "O"}], NS)
    assert [item.key for item in batch.entries] == ["legacy"]
    assert batch.failed_indices == (0,)
    assert batch.diagnostics[0].code == "PARATRANZ_TRANSPORT_KEY_INVALID"
