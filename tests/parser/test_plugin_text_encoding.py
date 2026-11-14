"""Real binary fixtures for explicit plugin and companion string codecs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import struct
import zlib

import pytest

from tests.plugin_fixtures import _record
from transbridge.config.text_encoding import LEGACY_READ_POLICY, STRICT_READ_POLICY
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin.text_subrecords import PluginTextEncodingError
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup, SkyrimStringsFile, SkyrimStringsWriter
from transbridge.writer.plugin_writer import PluginWriter


def _field(kind, data):
    return struct.pack("<4sH", kind, len(data)) + data


def _compressed_book(fields, *, formid=0x800):
    data = struct.pack("<I", len(fields)) + zlib.compress(fields)
    return struct.pack("<4sIIIHHHH", b"BOOK", len(data), 0x40000, formid, 0, 0, 44, 0) + data


def _group(records):
    return struct.pack("<4sI4siHHI", b"GRUP", 24 + len(records), b"BOOK", 0, 0, 0, 0) + records


def _header(fields=b"", *, localized=False):
    header = bytearray(_record(b"TES4", 0, fields))
    if localized:
        struct.pack_into("<I", header, 8, 0x80)
    return bytes(header)


def _parse(path, encoding="utf-8"):
    parser = PluginParser()
    entries = parser.parse_plugin(path, input_encoding=encoding, raise_on_error=True)
    return parser, entries


def test_cp1252_does_not_prefer_utf8_and_is_isolated_between_workers(tmp_path):
    path = tmp_path / "Source.esp"
    data = "é".encode() + b"\x00"
    path.write_bytes(_header() + _group(_record(b"BOOK", 0x800, _field(b"FULL", data))))

    encodings = ["utf-8", "cp1252"] * 8
    with ThreadPoolExecutor(max_workers=4) as executor:
        texts = list(executor.map(lambda encoding: _parse(path, encoding)[1][0].original, encodings))
    assert texts == ["é", "Ã©"] * 8


def test_explicit_strict_plugin_read_error_includes_physical_field(tmp_path):
    path = tmp_path / "Source.esp"
    data = _field(b"FULL", b"\xe9\x00")
    path.write_bytes(_header() + _group(_record(b"BOOK", 0x800, data)))

    with pytest.raises(PluginTextEncodingError, match=r"Source\.esp BOOK FULL \(00000800.*utf-8") as error:
        PluginParser().parse_plugin(path, text_read_policy=STRICT_READ_POLICY)
    assert isinstance(error.value.__cause__, UnicodeDecodeError)
    assert _parse(path, "cp1252")[1][0].original == "é"


@pytest.mark.parametrize(
    ("language", "encoded", "expected", "fallback"),
    [
        ("english", b"Anna\xefg", "Annaïg", "cp1252"),
        ("polish", "Żółć".encode("cp1250"), "Żółć", "cp1250"),
        ("russian", "Город".encode("cp1251"), "Город", "cp1251"),
    ],
)
def test_default_plugin_policy_falls_back_once_per_field(tmp_path, language, encoded, expected, fallback):
    path = tmp_path / "Source.esp"
    fields = _field(b"FULL", encoded + b"\x00") + _field(b"DESC", "Café\x00".encode())
    path.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))
    parser = PluginParser()

    entries = parser.parse_plugin(path, source_language=language)

    assert [entry.original for entry in entries] == [expected, "Café"]
    assert parser.fallback_count == 1
    assert [field.string.encoding for field in parser.get_plugin().inline_text_fields()] == [fallback, "utf-8"]


def test_plugin_languages_without_fallback_fail_and_legacy_originals_are_reproduced(tmp_path):
    path = tmp_path / "Source.esp"
    source = _header() + _group(_record(b"BOOK", 0x800, _field(b"FULL", b"Anna\xefg\x00")))
    path.write_bytes(source)
    with pytest.raises(PluginTextEncodingError, match="BOOK FULL.*utf-8"):
        PluginParser().parse_plugin(path, source_language="chinese")

    parser = PluginParser()
    (entry,) = parser.parse_plugin(path, text_read_policy=LEGACY_READ_POLICY, source_language="english")
    assert entry.original == "Annaďg"
    assert parser.get_plugin().dump() == source
    (field,) = parser.get_plugin().inline_text_fields()
    assert field.string.encoding == "cp1250"


def test_fallback_fields_transcode_when_primary_and_output_are_both_utf8(tmp_path):
    source = tmp_path / "Source.esp"
    target = tmp_path / "Target.esp"
    fields = _field(b"FULL", b"Anna\xefg\x00") + _field(b"DESC", "Café\x00".encode())
    source.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))

    parser, _entries = _parse(source)
    PluginWriter(parser.get_plugin()).write(target)

    reparsed = PluginParser().parse_plugin(target, text_read_policy=STRICT_READ_POLICY)
    assert [entry.original for entry in reparsed] == ["Annaïg", "Café"]
    assert "Annaïg".encode() in target.read_bytes()


def test_inline_write_transcodes_untranslated_fields_and_preserves_opaque_payloads(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Target.esp"
    author = _field(b"CNAM", b"\xe9\x00")
    master = _field(b"MAST", b"M\xe9.esp\x00")
    header = _header(author + master)
    edid = _field(b"EDID", b"Caf\xe9\x00extra")
    vmad = _field(b"VMAD", b"\x05\x00\x02\x00\xc3\xa9\x87")
    wrapped = _field(b"XXXX", struct.pack("<I", 3)) + b"ANAM\x00\x00\xb8\x00\x00"
    fields = edid + vmad + wrapped + _field(b"FULL", b"Caf\xe9\x00") + _field(b"DESC", b"Ouvr\xe9\x00\x00")
    source.write_bytes(header + _group(_compressed_book(fields)))

    parser, entries = _parse(source, "cp1252")
    assert [entry.original for entry in entries] == ["Café", "Ouvré"]
    writer = PluginWriter(parser.get_plugin(), output_encoding="cp936")
    assert writer.apply_collection(TranslationEntryCollection([replace(entries[0], translation="手册")])) == 1
    writer.write(target)

    assert [entry.original for entry in _parse(target, "cp936")[1]] == ["手册", "Ouvré"]
    expected_fields = (
        edid
        + vmad
        + wrapped
        + _field(b"FULL", "手册".encode("cp936") + b"\x00")
        + _field(b"DESC", "Ouvré".encode("cp936") + b"\x00\x00")
    )
    written = target.read_bytes()
    assert written.startswith(header)
    assert zlib.decompress(written[len(header) + 24 + 24 + 4 :]) == expected_fields
    assert source.read_bytes() == header + _group(_compressed_book(fields))


def test_encoding_only_write_prepares_all_fields_before_mutation(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Target.esp"
    fields = _field(b"FULL", b"Caf\xc3\xa9\x00") + _field(b"DESC", "手册\x00".encode())
    source.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))
    parser, entries = _parse(source)
    writer = PluginWriter(parser.get_plugin(), output_encoding="cp1252")

    with pytest.raises(PluginTextEncodingError, match="BOOK DESC.*cp1252") as error:
        writer.write(target)
    assert isinstance(error.value.__cause__, UnicodeEncodeError)
    assert "U+624B" in str(error.value)
    assert parser.get_plugin().dump() == source.read_bytes()
    assert not target.exists()

    edits = TranslationEntryCollection([replace(entries[-1], translation="Manual")])
    assert writer.apply_collection(edits) == 1
    writer.write(target)
    assert [entry.original for entry in _parse(target, "cp1252")[1]] == ["Café", "Manual"]
    assert b"Caf\xe9\x00" in target.read_bytes()


def test_encoding_only_write_transcodes_without_translation_entries(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Target.esp"
    fields = _field(b"FULL", b"Caf\xe9\x00") + _field(b"DESC", b"Portable\x00")
    source.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))
    parser, _ = _parse(source, "cp1252")
    writer = PluginWriter(parser.get_plugin())

    assert writer.apply_collection(TranslationEntryCollection()) == 0
    assert writer._inline_count == 1
    writer.write(target)
    assert [entry.original for entry in _parse(target)[1]] == ["Café", "Portable"]
    assert b"Caf\xc3\xa9\x00" in target.read_bytes()


def test_inline_field_overflow_is_reported_before_writing(tmp_path):
    source = tmp_path / "Source.esp"
    fields = _field(b"FULL", b"Original\x00")
    source.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))
    parser, (entry,) = _parse(source)
    writer = PluginWriter(parser.get_plugin())

    with pytest.raises(PluginTextEncodingError, match="BOOK FULL.*65535"):
        writer.apply_collection(TranslationEntryCollection([replace(entry, translation="é" * 32768)]))


def test_legacy_writers_reject_non_round_tripping_code_points(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Target.esp"
    text = "\u301c"
    fields = _field(b"FULL", text.encode() + b"\x00")
    source.write_bytes(_header() + _group(_record(b"BOOK", 0x800, fields)))
    parser, _ = _parse(source)

    with pytest.raises(PluginTextEncodingError, match="BOOK FULL.*cp932.*U\\+301C.*U\\+FF5E"):
        PluginWriter(parser.get_plugin(), output_encoding="cp932").write(target)
    assert not target.exists()
    with pytest.raises(ValueError, match="String ID 42.*cp932.*U\\+301C.*U\\+FF5E"):
        SkyrimStringsWriter.to_bytes({42: text}, False, output_encoding="cp932")


@pytest.mark.parametrize("suffix", [".strings", ".dlstrings", ".ilstrings"])
def test_strings_variants_decode_and_encode_the_exact_selected_bytes(suffix):
    length_prefixed = suffix != ".strings"
    original = SkyrimStringsWriter.to_bytes({42: "Café"}, length_prefixed, output_encoding="cp1252")
    assert original.endswith(b"Caf\xe9\x00")
    assert SkyrimStringsFile.from_bytes(original, suffix).get(42) == "Café"
    with pytest.raises(ValueError, match=r"String ID 42.*utf-8"):
        SkyrimStringsFile.from_bytes(original, suffix, text_read_policy=STRICT_READ_POLICY)
    assert SkyrimStringsFile.from_bytes(original, suffix, input_encoding="cp1252").get(42) == "Café"

    written = SkyrimStringsWriter.to_bytes({42: "手册"}, length_prefixed, output_encoding="cp936")
    assert written.endswith("手册".encode("cp936") + b"\x00")
    assert SkyrimStringsFile.from_bytes(written, suffix, input_encoding="cp936").get(42) == "手册"
    with pytest.raises(ValueError, match=r"String ID 42.*cp1252"):
        SkyrimStringsWriter.to_bytes({42: "手册"}, length_prefixed, output_encoding="cp1252")


def test_only_length_prefixed_strings_preserve_embedded_nul():
    text = "Café\x00Continuation\x00"
    written = SkyrimStringsWriter.to_bytes({42: text}, True, output_encoding="cp1252")
    assert SkyrimStringsFile.from_bytes(written, ".dlstrings", input_encoding="cp1252").get(42) == text
    with pytest.raises(ValueError, match=r"String ID 42.*U\+0000.*offset 4"):
        SkyrimStringsWriter.to_bytes({42: text}, False, output_encoding="cp1252")


def test_legacy_localized_writer_keeps_unreferenced_strings_and_reencodes_every_table(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Target.esp"
    fields = _field(b"FULL", struct.pack("<I", 1))
    source.write_bytes(_header(localized=True) + _group(_record(b"BOOK", 0x800, fields)))
    strings = tmp_path / "Strings"
    strings.mkdir()
    contents = {1: "Café", 2: "Crème"}
    (strings / "Source_English.strings").write_bytes(SkyrimStringsWriter.to_bytes(contents, False))
    (strings / "Source_English.dlstrings").write_bytes(SkyrimStringsWriter.to_bytes({3: "Épée"}, True))
    parser, (entry,) = _parse(source)
    lookup = parser.get_strings_lookup()
    writer = PluginWriter(parser.get_plugin(), strings_lookup=lookup, output_encoding="cp1252")

    assert writer.apply_collection(TranslationEntryCollection([replace(entry, translation="Pâté")])) == 1
    result = writer.write(target)
    assert not result["esp_saved"]
    written = PluginStringsLookup.from_strings_dir(strings, target.stem, input_encoding="cp1252")
    assert [written.get(string_id) for string_id in (1, 2, 3)] == ["Pâté", "Crème", "Épée"]
    assert (strings / "Target_English.strings").read_bytes().endswith(b"P\xe2t\xe9\x00Cr\xe8me\x00")
