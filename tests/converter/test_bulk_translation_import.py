from dataclasses import replace
import xml.etree.ElementTree as ET

import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import XT_Entry


def _entry(index, **changes):
    entry_id = f"Editor{index}:{index:08X}|1~NPC_:FULL"
    entry = TranslationEntry(
        entry_id,
        entry_id,
        f"source {index}",
        "",
        0,
        "NPC_:FULL|quest",
        string_id=index,
        revision=EntryRevision(7),
        external_refs=(ExternalEntryRef("test", "bulk", index),),
        provenance=(Provenance("prior-run", "translator", "xml"),),
        metadata=(("nested", {"values": [index]}),),
        form_id_with_plugin=f"{index:08X}|Example.esp",
        dsd_type="NPC_ FULL",
        dsd_index=1,
        editor_id=f"Editor{index}",
    )
    return replace(entry, **changes)


def _xt(number, dest="translated", **changes):
    return replace(XT_Entry(0, f"Editor{number}", "NPC_:FULL", f"source {number}", dest, 1), **changes)


def _write_eet(path, rows):
    root = ET.Element("DocumentElement")
    for index, text, changes in rows:
        fields = {
            "EDID": f"Editor{index}",
            "ID": f"{index:08X}",
            "INDEX": "1",
            "GRUP": "NPC_",
            "CHAMP": "FULL",
            "ORIGINAL": f"source {index}",
            "TRADUIT": text,
            "STATUS": "0",
        }
        fields.update(changes)
        node = ET.SubElement(root, "ESP")
        for name, value in fields.items():
            ET.SubElement(node, name).text = value
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _assert_changes(collection, originals, expected):
    assert collection.collection_revision.value == len(originals) + sum(
        entry.string_id in expected for entry in originals
    )
    for original in originals:
        actual = collection.get(original.identity)
        if original.string_id in expected:
            assert actual == replace(
                original, translation=expected[original.string_id], stage=1, revision=original.revision.next()
            )
        else:
            assert actual is original
        assert collection.get_by_external_ref(original.external_refs[0]) == (actual,)


def test_eet_exact_precedes_fallback_first_valid_duplicate_and_identical_counts(tmp_path):
    entries = [_entry(1, translation="old", stage=2), _entry(2), _entry(3, translation="same"), _entry(4)]
    path = _write_eet(
        tmp_path / "source.xml",
        [
            (99, "fallback", {"ORIGINAL": "source 1"}),
            (1, "wrong source", {"ORIGINAL": "other"}),
            (1, "", {}),
            (1, "exact first", {}),
            (1, "exact later", {}),
            (98, "fallback first", {"ORIGINAL": "source 2"}),
            (97, "fallback later", {"ORIGINAL": "source 2"}),
            (3, "same", {}),
            (4, "", {}),
        ],
    )
    collection = TranslationEntryCollection(entries)
    assert collection.update_from_eet_xml(path) == 3
    _assert_changes(collection, entries, {1: "exact first", 2: "fallback first", 3: "same"})


def test_xt_candidate_order_noop_suppression_fallback_and_first_duplicate():
    entries = [
        _entry(1),
        _entry(2),
        _entry(3, translation="keep", stage=2),
        _entry(4, translation="old", stage=2),
        _entry(5),
        _entry(6, original=" source\n6 "),
        _entry(7),
    ]
    source = [
        _xt(1, "bare", edid="00000001"),
        _xt(1, "editor first"),
        _xt(1, "editor later"),
        _xt(2, ""),
        _xt(2, "must not fallback", edid="Other"),
        _xt(3, "must not overwrite"),
        _xt(4, "fallback first", edid="Other"),
        _xt(4, "fallback later", edid="Other2"),
        _xt(5, "bracket", edid="[00000005]"),
        _xt(6, "whitespace", source="source\r\n6"),
        _xt(7, "index fallback", index=2),
    ]
    collection = TranslationEntryCollection(entries)
    assert collection.apply_xt_entries(source) == 5
    _assert_changes(
        collection,
        entries,
        {1: "editor first", 4: "fallback first", 5: "bracket", 6: "whitespace", 7: "index fallback"},
    )


@pytest.mark.parametrize("overwrite", [False, True])
def test_strings_empty_same_original_existing_and_missing(overwrite):
    entries = [
        _entry(1),
        _entry(2),
        _entry(3, translation="keep", stage=2),
        _entry(4, translation="same"),
        _entry(5),
        _entry(6, string_id=None),
    ]
    collection = TranslationEntryCollection(entries)
    lookup = PluginStringsLookup({1: "", 2: "source 2", 3: "new", 4: "same", 6: "unused"})
    expected = {1: "", 3: "new", 4: "same"} if overwrite else {1: ""}
    assert collection.update_from_strings_lookup(lookup, overwrite=overwrite) == len(expected)
    _assert_changes(collection, entries, expected)


def test_matching_failure_rolls_back_earlier_matches():
    entries = [_entry(1), _entry(2, id="Editor2:00000002|invalid~NPC_:FULL")]
    collection = TranslationEntryCollection(entries)
    with pytest.raises(ValueError):
        collection.apply_xt_entries([_xt(1), _xt(2)])
    _assert_changes(collection, entries, {})


def test_lookup_failure_rolls_back_earlier_matches():
    class FailingLookup(PluginStringsLookup):
        def get(self, string_id):
            if string_id == 2:
                raise RuntimeError("lookup failed")
            return super().get(string_id)

    entries = [_entry(1), _entry(2)]
    collection = TranslationEntryCollection(entries)
    with pytest.raises(RuntimeError, match="lookup failed"):
        collection.update_from_strings_lookup(FailingLookup({1: "new"}))
    _assert_changes(collection, entries, {})


def test_batch_checks_global_reference_conflicts_before_publication():
    entries = [_entry(1), _entry(2), _entry(3)]
    collection = TranslationEntryCollection(entries)
    updates = [
        replace(entries[0], translation="new"),
        replace(entries[1], translation="new", external_refs=entries[2].external_refs),
    ]
    with collection._lock, pytest.raises(ValueError, match="external reference conflict"):
        collection._replace_import_entries(updates)
    _assert_changes(collection, entries, {})


@pytest.mark.parametrize("kind", ["eet", "xt", "strings"])
def test_large_batch_single_index_build_without_legacy_scans(kind, tmp_path, monkeypatch):
    entries = [_entry(index) for index in range(2000)]
    # Namespaced entries retain identity even with colliding legacy local keys.
    entries += [
        replace(
            entries[0],
            entry_key=EntryKey(SourceNamespace("source:other"), entries[0].key),
            external_refs=(ExternalEntryRef("test", "other", 0),),
        )
    ]
    collection = TranslationEntryCollection(entries)
    original_build = collection._build_external_index
    rebuild_sizes = []

    def build(projected):
        rebuild_sizes.append(len(projected))
        return original_build(projected)

    def forbidden(*args, **kwargs):
        pytest.fail("bulk import must not perform per-entry collection scans or commits")

    monkeypatch.setattr(collection, "_build_external_index", build)
    monkeypatch.setattr(collection, "_legacy_matches", forbidden)
    monkeypatch.setattr(collection, "add", forbidden)
    if kind == "eet":
        path = _write_eet(tmp_path / "large.xml", [(index, "new", {}) for index in range(2000)])
        count = collection.update_from_eet_xml(path)
    elif kind == "xt":
        count = collection.apply_xt_entries(_xt(index, "new") for index in range(2000))
    else:
        count = collection.update_from_strings_lookup(PluginStringsLookup(dict.fromkeys(range(2000), "new")))
    assert count == len(entries)
    assert rebuild_sizes == [len(entries)]
    _assert_changes(collection, entries, dict.fromkeys(range(2000), "new"))


@pytest.mark.parametrize("kind", ["eet", "xt", "strings"])
def test_empty_import_does_not_publish(kind, tmp_path, monkeypatch):
    entries = [_entry(1)]
    collection = TranslationEntryCollection(entries)

    def forbidden(*args):
        pytest.fail("empty import must not rebuild the collection")

    monkeypatch.setattr(collection, "_build_external_index", forbidden)
    if kind == "eet":
        count = collection.update_from_eet_xml(_write_eet(tmp_path / "empty.xml", []))
    elif kind == "xt":
        count = collection.apply_xt_entries([])
    else:
        count = collection.update_from_strings_lookup(PluginStringsLookup({}))
    assert count == 0
    _assert_changes(collection, entries, {})
