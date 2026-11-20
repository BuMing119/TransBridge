"""Compare the XT-compatible reader to Windows, while preserving saved policies."""

import ctypes
from ctypes import wintypes
import sys

import pytest

from tests.parser.test_plugin_text_encoding import _field, _group, _header
from tests.plugin_fixtures import _record
from transbridge.config.text_encoding import TextReadOptions
from transbridge.parser import windows_codepage
from transbridge.parser.localized_string_records import render
from transbridge.parser.plugin.text_subrecords import PluginTextEncodingError
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import SkyrimStringsFile
from transbridge.parser.text_reader import decode_game_text
from transbridge.writer.plugin_writer import PluginWriter


@pytest.mark.skipif(sys.platform != "win32", reason="Windows code-page compatibility oracle")
@pytest.mark.parametrize(
    ("language", "codepage"),
    [
        ("english", 1252),
        ("polish", 1250),
        ("russian", 1251),
        ("greek", 1253),
        ("turkish", 1254),
        ("arabic", 1256),
        ("japanese", 932),
    ],
)
def test_xt_language_fallback_matches_windows_for_all_byte_values(language, codepage):
    convert = ctypes.WinDLL("kernel32", use_last_error=True).MultiByteToWideChar
    convert.argtypes = [wintypes.UINT, wintypes.DWORD, ctypes.c_char_p, ctypes.c_int, wintypes.LPWSTR, ctypes.c_int]
    convert.restype = ctypes.c_int
    payload = bytes(range(256))
    buffer = ctypes.create_unicode_buffer(len(payload) * 2)
    count = convert(codepage, 0, payload, len(payload), buffer, len(buffer))
    assert count > 0, ctypes.get_last_error()

    text, encoding = decode_game_text(payload, TextReadOptions(source_language=language))

    assert text == buffer[:count]
    assert encoding == f"cp{codepage}"


@pytest.mark.parametrize("value", [0x81, 0x8D, 0x8F, 0x90, 0x9D])
def test_xt_cp1252_keeps_windows_control_codepoints_instead_of_changing_language(value):
    text, encoding = decode_game_text(bytes([value]), TextReadOptions())

    assert text == chr(value)
    assert encoding == "cp1252"


def test_new_source_defaults_are_versioned_without_replacing_saved_v1():
    assert TextReadOptions().text_read_policy == "xtranslator-v2"
    assert TextReadOptions(text_read_policy="xtranslator-v1").text_read_policy == "xtranslator-v1"


def test_valid_utf8_is_preferred_over_windows_codepage():
    assert decode_game_text("Café\ufffd".encode(), TextReadOptions()) == ("Café\ufffd", "utf-8")


def test_v1_and_strict_decoding_still_reject_undefined_cp1252_bytes():
    for options in (
        TextReadOptions(text_read_policy="xtranslator-v1"),
        TextReadOptions(text_read_policy="strict-v1"),
        TextReadOptions(input_encoding="cp1252"),
    ):
        with pytest.raises(UnicodeDecodeError):
            decode_game_text(b"\x81", options)


@pytest.mark.parametrize("language", ["english", "chinese"])
def test_xt_failure_downgrades_to_original_library_with_source_context(tmp_path, caplog, monkeypatch, language):
    monkeypatch.setattr(windows_codepage, "_convert", None)
    source = tmp_path / "Source.esp"
    original = _header() + _group(_record(b"BOOK", 0x800, _field(b"FULL", b"Old \x81\x00")))
    source.write_bytes(original)
    parser = PluginParser()

    entries = parser.parse_plugin(source, source_language=language, raise_on_error=True)

    assert [entry.original for entry in entries] == ["Old Ѓ"]
    assert "Source.esp BOOK FULL (00000800, subrecord 0)" in caplog.text
    assert "historical cp1251" in caplog.text
    assert parser.get_plugin().dump() == original
    assert source.read_bytes() == original


def test_native_cp1252_input_cannot_bypass_strict_same_codec_output(tmp_path):
    source, target = tmp_path / "Source.esp", tmp_path / "Output.esp"
    original = _header() + _group(_record(b"BOOK", 0x800, _field(b"FULL", b"\x81\x00")))
    source.write_bytes(original)
    parser = PluginParser()
    assert parser.parse_plugin(source, raise_on_error=True)[0].original == "\x81"

    with pytest.raises(PluginTextEncodingError, match=r"cp1252.*U\+0081"):
        PluginWriter(parser.get_plugin(), output_encoding="cp1252").write(target)

    assert not target.exists()
    assert parser.get_plugin().dump() == original


@pytest.mark.parametrize("suffix", [".strings", ".dlstrings", ".ilstrings"])
def test_external_tables_share_xt_windows_semantics_and_keep_strict_mode(suffix):
    import struct

    payload = b"\x81\0"
    chunk = payload if suffix == ".strings" else struct.pack("<I", len(payload)) + payload
    data = render([(42, chunk)])

    parsed = SkyrimStringsFile.from_bytes(data, suffix)

    assert parsed.get(42) == "\x81"
    assert parsed.fallback_count == 1
    for policy in ("xtranslator-v1", "strict-v1"):
        with pytest.raises(ValueError, match="String ID 42"):
            SkyrimStringsFile.from_bytes(data, suffix, text_read_policy=policy)
