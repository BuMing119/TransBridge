"""A localized numeric ID is scoped by its original strings-file variant."""

from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.bsa_fixtures import write_bsa
from tests.plugin_fixtures import _field, _record
from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import (
    PluginStringsLookup,
    PluginStringsWriter,
    SkyrimStringsFile,
    SkyrimStringsWriter,
)

VALUES = {".strings": "Item display name", ".dlstrings": "Long description", ".ilstrings": "Spoken dialogue"}


def _plugin(path: Path) -> Path:
    groups = []
    for form_id, (kind, subtype) in enumerate(
        ((b"NPC_", b"FULL"), (b"BOOK", b"DESC"), (b"INFO", b"NAM1")), start=0x800
    ):
        record = _record(kind, form_id, _field(b"EDID", f"Record{form_id}") + subtype + struct.pack("<HI", 4, 148))
        groups.append(struct.pack("<4sI4siHHI", b"GRUP", 24 + len(record), kind, 0, 0, 0, 0) + record)
    header = bytearray(_record(b"TES4", 0, b""))
    header[8:12] = struct.pack("<I", 0x80)
    path.write_bytes(header + b"".join(groups))
    return path


def _resources(plugin: Path, *, archive=False, variants=None):
    variants = VALUES if variants is None else variants
    contents = {
        f"strings/{plugin.stem.lower()}_english{suffix}": SkyrimStringsWriter.to_bytes(
            {148: text, 700 + index: f"unique {suffix}"}, suffix != ".strings"
        )
        for index, (suffix, text) in enumerate(variants.items())
    }
    if archive:
        write_bsa(plugin.with_suffix(".bsa"), contents)
    else:
        for member, content in contents.items():
            target = plugin.parent / member
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(content)


@pytest.mark.parametrize("archive", [False, True])
def test_library_subrecord_types_resolve_their_own_original_for_three_shared_numeric_ids(tmp_path, archive):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin, archive=archive)

    entries = PluginParser().parse_plugin(plugin, raise_on_error=True)

    assert {entry.dsd_type: entry.original for entry in entries} == {
        "NPC_ FULL": VALUES[".strings"],
        "BOOK DESC": VALUES[".dlstrings"],
        "INFO NAM1": VALUES[".ilstrings"],
    }
    assert [entry.string_id for entry in entries] == [148, 148, 148]


def test_typed_lookup_keeps_get_compatibility_and_unique_ids_ignore_variant_mismatch(tmp_path):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin)
    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get(148) == VALUES[".ilstrings"]
    assert lookup.get_for_type(148, "FULL") == VALUES[".strings"]
    assert lookup.get_for_type(148, "DESC") == VALUES[".dlstrings"]
    assert lookup.get_for_type(148, "NAM1") == VALUES[".ilstrings"]
    assert lookup.get_for_type(700, "DESC") == "unique .strings"
    assert lookup.get_for_type(701, "NAM1") == "unique .dlstrings"
    assert lookup.get_for_type(702, "FULL") == "unique .ilstrings"
    assert lookup.get_for_type(999, "FULL") is None


@pytest.mark.parametrize("subrecord_type", [None, ""])
def test_shared_id_without_a_type_does_not_guess_the_last_merged_original(tmp_path, subrecord_type):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin)

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get_for_type(148, subrecord_type) is None


def test_shared_id_does_not_fall_back_to_another_existing_variant_when_its_type_is_absent(tmp_path):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin, variants={suffix: text for suffix, text in VALUES.items() if suffix != ".ilstrings"})

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get_for_type(148, "NAM1") is None
    entries = PluginParser().parse_plugin(plugin, raise_on_error=True)
    assert {entry.dsd_type for entry in entries} == {"NPC_ FULL", "BOOK DESC"}


def test_typed_lookup_reuses_the_existing_writer_classification(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin)
    lookup = PluginStringsLookup.from_plugin(plugin)
    calls = []

    def route(subrecord_type):
        calls.append(subrecord_type)
        return ".ilstrings"

    monkeypatch.setattr(PluginStringsWriter, "_ext_for", staticmethod(route))

    assert lookup.get_for_type(148, "FULL") == VALUES[".ilstrings"]
    assert calls == ["FULL"]


def test_decoded_variant_tables_are_retained_without_reparsing_resource_bytes(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path / "Shared.esp")
    _resources(plugin)
    original = SkyrimStringsFile.from_bytes
    calls = []

    def parse(cls, data, ext, source=""):
        calls.append(ext)
        return original(data, ext, source=source)

    monkeypatch.setattr(SkyrimStringsFile, "from_bytes", classmethod(parse))
    lookup = PluginStringsLookup.from_plugin(plugin)
    for _ in range(20):
        assert lookup.get_for_type(148, "FULL") == VALUES[".strings"]
        assert lookup.get_for_type(148, "DESC") == VALUES[".dlstrings"]
        assert lookup.get_for_type(148, "NAM1") == VALUES[".ilstrings"]
    assert calls == list(VALUES)


@pytest.mark.parametrize("legacy", [True, False])
def test_dictionary_and_fake_lookup_injection_keep_existing_extraction_contract(tmp_path, legacy):
    plugin = SSEPluginWithContext.from_file(_plugin(tmp_path / "Shared.esp"))
    lookup = (
        PluginStringsLookup({148: "Injected original"})
        if legacy
        else SimpleNamespace(get=lambda _id: "Injected original")
    )

    entries = plugin.extract_strings_with_context(strings_lookup=lookup)

    assert [entry.string for entry in entries] == ["Injected original"] * 3


def test_context_extraction_passes_the_library_subrecord_type_to_an_injected_typed_lookup(tmp_path):
    plugin = SSEPluginWithContext.from_file(_plugin(tmp_path / "Shared.esp"))
    calls = []

    class TypedLookup:
        def get_for_type(self, string_id, subrecord_type):
            calls.append((string_id, subrecord_type))
            return f"Resolved {subrecord_type}"

        def get(self, _string_id):
            pytest.fail("Typed extraction fell back to a global ID lookup")

    entries = plugin.extract_strings_with_context(strings_lookup=TypedLookup())

    assert calls == [(148, "FULL"), (148, "DESC"), (148, "NAM1")]
    assert [entry.string for entry in entries] == ["Resolved FULL", "Resolved DESC", "Resolved NAM1"]
