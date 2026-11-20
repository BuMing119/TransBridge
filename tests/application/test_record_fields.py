from dataclasses import replace

from transbridge.application.dialogue.record_fields import (
    RecordIdentity,
    find_record_entries,
    record_editor_id,
    record_field_id,
    record_identity,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry


def entry(key, *, namespace="source:a", metadata=(), **values):
    return TranslationEntry(
        key,
        key,
        "Original",
        "",
        0,
        None,
        entry_key=EntryKey(SourceNamespace(namespace), key),
        metadata=metadata,
        **values,
    )


def test_live_and_hydrated_record_identity_excludes_editor_and_field():
    live = entry("Topic:00abcdef|1~INFO:NAM1", form_id_with_plugin="00ABCDEF|Master.esm", editor_id="Topic")
    hydrated = entry("AnotherEditor:00ABCDEF|2~INFO:RNAM")
    assert (
        record_identity(live)
        == record_identity(hydrated)
        == RecordIdentity(SourceNamespace("source:a"), "INFO", "00ABCDEF")
    )
    assert record_editor_id(live) == "Topic"
    assert record_editor_id(hydrated) == "AnotherEditor"
    assert record_field_id(hydrated) == "RNAM"
    assert record_editor_id(entry("None:00ABCDEF|1~INFO:NAM1")) == ""


def test_metadata_identity_and_fields_are_authoritative_for_opaque_keys():
    selected = entry(
        "opaque",
        metadata=(
            ("plugin.record_type", "BOOK"),
            ("plugin.form_id", "00000001|Master.esm"),
            ("plugin.editor_id", "BookEditor"),
            ("plugin.field_type", "DESC"),
        ),
    )
    assert record_identity(selected) == RecordIdentity(SourceNamespace("source:a"), "BOOK", "00000001")
    assert record_editor_id(selected) == "BookEditor"
    assert record_field_id(selected) == "DESC"
    assert record_identity(entry("not-a-plugin-key")) is None


def test_all_repeated_fields_preserve_source_order_without_cross_record_or_source_leaks():
    first = entry("None:00000001|1~BOOK:DESC", metadata=(("plugin.source_order", 4),))
    second = entry("None:00000001|2~BOOK:DESC", metadata=(("plugin.source_order", 5),))
    name = entry("BookEditor:00000001|1~BOOK:FULL", metadata=(("plugin.source_order", 3),))
    other_type = entry("None:00000001|1~NPC_:FULL")
    other_source = replace(first, entry_key=EntryKey(SourceNamespace("source:b"), first.key))
    rows = [second, other_type, other_source, name, first]
    assert find_record_entries(rows, first) == (name, first, second)
