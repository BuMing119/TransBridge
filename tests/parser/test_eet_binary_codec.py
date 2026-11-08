"""Independent EET wire fixtures and full-file regressions against real samples."""

from dataclasses import replace
from pathlib import Path
import struct

import pytest

from transbridge.parser.eet_binary_codec import EetDocument, EetFormatError
from transbridge.parser.eet_binary_parser import EET_BinaryParser
from transbridge.parser.eet_number_cache import EetNumberOptions


def _text(text, width=4):
    raw = text.encode("utf-8")
    return struct.pack("<I" if width == 4 else "<H", len(raw)) + raw


def _cache(template="Value %VAR00%", value="42"):
    return _text(template) + struct.pack("<I", 1) + _text(value)


def _wire(*, icon=301, original_cache=b"", translation_cache=b"", phrases=True, game="Skyrim SE", type_dt=1):
    payload = b"".join(_text(value) for value in ("INFO", "01000800", "EditorID", "NAM1", "Hello", "你好", "tag"))
    payload += struct.pack("<ihi", 0x12345678, 99, 0x1234567)
    payload += _text("Comment注释") + struct.pack("<I", 0xFEDCBA98) + _text("Hello") + struct.pack("<I", 0x87654321)
    payload += struct.pack("<I", len(original_cache)) + original_cache
    payload += struct.pack("<I", len(translation_cache)) + translation_cache
    if icon is not None:
        payload += struct.pack("<i", icon)
    data = b"EET_" + struct.pack("<iI", type_dt, 3) + b"GAME" + _text(game, 2)
    data += b"LINE" + struct.pack("<I", 1) + struct.pack("<I", len(payload)) + payload
    if phrases:
        phrase_data = struct.pack("<I", 1) + _text("A", 2) + _text("中", 2)
        data += b"PHRA" + struct.pack("<I", len(phrase_data)) + phrase_data
    return data


def test_actual_header_types_signed_fields_icon_and_byte_lengths():
    data = _wire()
    doc = EetDocument.from_bytes(data)
    assert (doc.type_dt, doc.version, doc.game) == (1, 3, "Skyrim SE")
    record = doc.records[0]
    assert (record.index, record.status, record.text_id, record.icon) == (0x12345678, 99, 0x1234567, 301)
    assert record.comment == "Comment注释"
    assert (record.original_hash, record.normalized_hash) == (0xFEDCBA98, 0x87654321)
    assert doc.phrases == (("A", "中"),)
    assert doc.to_bytes() == data


@pytest.mark.parametrize("icon", [-1, 0, 1007, None])
@pytest.mark.parametrize("phrases", [True, False])
def test_optional_phrase_section_and_icon_are_preserved(icon, phrases):
    data = _wire(icon=icon, phrases=phrases)
    doc = EetDocument.from_bytes(data)
    assert doc.records[0].icon == icon
    assert (doc.phrases is None) == (not phrases)
    assert doc.to_bytes() == data


def test_truncation_does_not_return_partial_records():
    data = _wire()
    legal_end_without_optional_phrases = data.index(b"PHRA")
    for end in range(len(data)):
        if end == legal_end_without_optional_phrases:
            continue
        with pytest.raises(EetFormatError):
            EetDocument.from_bytes(data[:end])


@pytest.mark.parametrize("offset,value", [(4, 0), (4, 13), (8, 1), (8, 2), (8, 4)])
def test_unverified_file_type_and_version_rejected(offset, value):
    data = bytearray(_wire())
    struct.pack_into("<I", data, offset, value)
    with pytest.raises(EetFormatError):
        EetDocument.from_bytes(bytes(data))


@pytest.mark.parametrize("corruption", ["count_large", "count_small", "size_small", "size_large", "suffix", "utf8"])
def test_record_boundaries_and_utf8_are_strict(corruption):
    data = bytearray(_wire())
    line = data.index(b"LINE")
    if corruption == "count_large":
        struct.pack_into("<I", data, line + 4, 0xFFFFFFFF)
    elif corruption == "count_small":
        struct.pack_into("<I", data, line + 4, 0)
    elif corruption == "size_small":
        struct.pack_into("<I", data, line + 8, 4)
    elif corruption == "size_large":
        struct.pack_into("<I", data, line + 8, 0xFFFFFFFF)
    elif corruption == "suffix":
        data += b"\0"
    else:
        data[data.index(b"Hello")] = 0xFF
    with pytest.raises(EetFormatError):
        EetDocument.from_bytes(bytes(data))


@pytest.mark.parametrize("invalid", [b"x", _cache() + b"\0", _text("template") + struct.pack("<I", 999)])
def test_bad_nested_variable_cache_is_rejected(invalid):
    with pytest.raises(EetFormatError):
        EetDocument.from_bytes(_wire(original_cache=invalid))


@pytest.mark.parametrize("field", ["original_variables", "translation_variables"])
def test_caches_roundtrip_and_translation_updates_preserve_original_side(field):
    arguments = {"original_cache" if field == "original_variables" else "translation_cache": _cache()}
    data = _wire(**arguments)
    doc = EetDocument.from_bytes(data)
    assert doc.to_bytes() == data
    assert doc.with_translation(0, doc.records[0].translation) is doc
    patched = doc.with_translation(0, "Value 75")
    assert patched.records[0] == replace(
        doc.records[0], translation="Value 75", translation_variables=_cache(value="75")
    )
    assert EetDocument.from_bytes(patched.to_bytes()) == patched
    assert patched.records[0].translation_number_cache.values == ("75",)


@pytest.mark.parametrize("translation", ["plain", "", "1" * 201])
def test_translation_without_eligible_numbers_clears_stale_cache(translation):
    doc = EetDocument.from_bytes(_wire(original_cache=_cache(), translation_cache=_cache()))
    patched = doc.with_translation(0, translation)
    assert patched.records[0] == replace(doc.records[0], translation=translation, translation_variables=b"")


def test_patch_creates_new_cache_and_accepts_explicit_regex_options():
    doc = EetDocument.from_bytes(_wire())
    patched = doc.with_translation(0, "I am III 12", number_options=EetNumberOptions(roman=False))
    assert patched.records[0].translation_variables == _cache("I am III %VAR00%", "12")
    assert patched.records[0].original_number_cache is None
    # A no-op preserves existing caches even if a different profile is supplied.
    assert patched.with_translation(0, "I am III 12", number_options=EetNumberOptions()) is patched


@pytest.mark.parametrize("translation", ["", "修改后😀", "Long" * 20000], ids=["empty", "unicode", "long"])
def test_patch_only_changes_one_translation_and_record_length(translation):
    doc = EetDocument.from_bytes(_wire())
    doc = replace(doc, records=(doc.records[0], doc.records[0]))
    patched = doc.with_translation(1, translation)
    assert patched.records[0] == doc.records[0]
    assert patched.records[1] == replace(doc.records[1], translation=translation)
    assert EetDocument.from_bytes(patched.to_bytes()) == patched
    assert doc.records[1].translation == "你好"


@pytest.mark.parametrize("ordinal", [-1, 1, True, 0.0])
def test_patch_requires_exact_physical_ordinal(ordinal):
    with pytest.raises(ValueError):
        EetDocument.from_bytes(_wire()).with_translation(ordinal, "changed")


def test_facade_exposes_correct_numbers_comment_and_icon_without_losing_document():
    parser = EET_BinaryParser.from_bytes(_wire())
    entry = parser.entries[0]
    assert (entry.index, entry.status, entry.idstexte, entry.icon) == (0x12345678, 99, 0x1234567, 301)
    assert entry.commentaire == "Comment注释"
    assert parser.game_data == "Skyrim SE" and parser.document.to_bytes() == _wire()
    assert parser.get_by_key("INFO", "01000800", "EditorID", "NAM1") == [entry]
    assert parser.find(status=99, traduit_contains="你") == [entry]
    assert parser.to_dicts()[0]["INDEX"] == 0x12345678


@pytest.mark.parametrize("field,value", [("status", 32768), ("index", 2**31), ("icon", 2**31), ("text_id", True)])
def test_serialization_checks_signed_ranges(field, value):
    doc = EetDocument.from_bytes(_wire())
    with pytest.raises(EetFormatError):
        replace(doc, records=(replace(doc.records[0], **{field: value}),)).to_bytes()


def test_empty_game_and_absent_or_empty_phrase_sections_are_distinct():
    absent = EetDocument(3, "", (), None, 2)
    empty = replace(absent, phrases=())
    assert absent.to_bytes() != empty.to_bytes()
    assert EetDocument.from_bytes(absent.to_bytes()) == absent
    assert EetDocument.from_bytes(empty.to_bytes()) == empty


@pytest.mark.slow
def test_real_database_complete_roundtrip_and_old_partial_parse_regression():
    path = Path("tests/trans_exe/eet/BDD_WL.eet")
    if not path.exists():
        pytest.skip("Local EET corpus is not distributed")
    data = path.read_bytes()
    doc = EetDocument.from_bytes(data)
    assert (len(doc.records), len(doc.phrases)) == (92157, 43)
    assert doc.to_bytes() == data
    assert (doc.records[0].index, doc.records[0].status, doc.records[0].text_id) == (1, 99, 6)
    assert sum(bool(record.original_variables) for record in doc.records) == 2166
    with pytest.raises(EetFormatError):
        EET_BinaryParser.from_bytes(data[:149])


@pytest.mark.integration
@pytest.mark.parametrize(
    "relative,count",
    [
        ("BDD/BDD_Reconquista.eet", 92556),
        ("BDD/BDD_Reconquista ANK.eet", 92950),
        ("Bases Persos/TempSession/3DNPC_B675CB1D_BA3D60BA.eet", 53847),
    ],
)
def test_installed_corpus_read_only(relative, count):
    path = Path("C:/MyProgram/MO2/tools/ESP-ESM Translator") / relative
    if not path.exists():
        pytest.skip("Optional local ESP-ESM Translator corpus is unavailable")
    data = path.read_bytes()
    doc = EetDocument.from_bytes(data)
    assert len(doc.records) == count
    assert doc.to_bytes() == data
