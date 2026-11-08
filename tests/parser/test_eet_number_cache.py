"""Independent .NET reference cases and real EET numeric-cache fidelity."""

import json
from pathlib import Path
import struct

import pytest

from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.parser.eet_binary_wire import EetFormatError
from transbridge.parser.eet_number_cache import (
    EetNumberCache,
    EetNumberOptions,
    build_number_cache,
    parse_number_cache,
)

_REFERENCE = json.loads(
    (Path(__file__).parent / "fixtures" / "eet_number_cache_reference.json").read_text(encoding="utf-8-sig")
)
_INSTALL = Path("C:/MyProgram/MO2/tools/ESP-ESM Translator")


def _text(value):
    raw = value.encode("utf-8")
    return struct.pack("<I", len(raw)) + raw


@pytest.mark.parametrize("case", _REFERENCE["cases"], ids=lambda case: case["id"])
def test_independent_dotnet_regex_golden(case):
    mode = case["pattern_index"]
    options = EetNumberOptions(max_utf16_units=case["max_utf16"], roman=mode != 0, except_i_space=mode == 2)
    result = build_number_cache(case["text"], options=options)
    assert len(case["text"].encode("utf-16-le")) // 2 == case["utf16_length"]
    if not case["values"]:
        assert case["payload_hex"] == ""
        assert result is None
        return
    assert result == EetNumberCache(case["template"], tuple(case["values"]))
    # Build expected wire bytes directly, without using the codec writer.
    expected = _text(case["template"]) + struct.pack("<I", len(case["values"]))
    expected += b"".join(_text(value) for value in case["values"])
    assert expected.hex() == case["payload_hex"]
    assert result.to_payload() == expected
    assert parse_number_cache(expected) == result
    if case["total_matches"] > 100:
        assert len(result.values) == 100
        assert "%VAR99%" in result.template and "%VAR100%" not in result.template


@pytest.mark.parametrize("length", [199, 200, 201])
@pytest.mark.parametrize("prefix", ["a", "😀"])
def test_length_limit_counts_utf16_code_units(length, prefix):
    width = len(prefix.encode("utf-16-le")) // 2
    text = prefix * ((length - 1) // width) + "a" * ((length - 1) % width) + "7"
    assert len(text.encode("utf-16-le")) // 2 == length
    cache = build_number_cache(text)
    if length > 200:
        assert cache is None
    else:
        assert cache == EetNumberCache(text[:-1] + "%VAR00%", ("7",))


def test_length_limit_is_checked_before_trimming_and_can_be_configured():
    text = " " + "a" * 199 + "7"
    assert build_number_cache(text) is None
    assert build_number_cache(text, options=EetNumberOptions(max_utf16_units=201)).values == ("7",)
    assert build_number_cache("7", options=EetNumberOptions(max_utf16_units=0)) is None
    assert build_number_cache("", options=EetNumberOptions(max_utf16_units=0)) is None


@pytest.mark.parametrize(
    "group,field,excluded",
    [("SCPT", "SCTX", True), ("SCPT", "FULL", False), ("NPC_", "SCTX", False), ("scpt", "sctx", False)],
)
def test_script_filter_requires_exact_group_and_field(group, field, excluded):
    cache = build_number_cache("set value to 42", group=group, field=field)
    assert (cache is None) is excluded
    if not excluded:
        assert cache.values == ("42",)


def test_payload_preserves_unicode_repeated_values_and_literal_empty_cache():
    payload = _text("😀中 %VAR00% %VAR01%") + struct.pack("<I", 2) + _text("007") + _text("007")
    expected = EetNumberCache("😀中 %VAR00% %VAR01%", ("007", "007"))
    assert parse_number_cache(payload) == expected
    assert expected.to_payload() == payload
    assert parse_number_cache(b"") is None
    explicit_empty = _text("") + struct.pack("<I", 0)
    assert parse_number_cache(explicit_empty) == EetNumberCache("", ())
    assert parse_number_cache(explicit_empty).to_payload() == explicit_empty


def test_every_nonempty_truncation_of_payload_is_rejected():
    payload = _text("template %VAR00%") + struct.pack("<I", 1) + _text("42")
    for length in range(1, len(payload)):
        with pytest.raises(EetFormatError):
            parse_number_cache(payload[:length])


@pytest.mark.parametrize(
    "payload",
    [
        struct.pack("<I", 0xFFFFFFFF),
        struct.pack("<I", 1) + b"\xff" + struct.pack("<I", 0),
        _text("template") + struct.pack("<I", 999),
        _text("template") + struct.pack("<I", 1) + struct.pack("<I", 0xFFFFFFFF),
        _text("template") + struct.pack("<I", 1) + struct.pack("<I", 1) + b"\xff",
        _text("template") + struct.pack("<I", 0) + b"\0",
        bytearray(),
        "",
        None,
    ],
    ids=[
        "huge-template",
        "bad-template-utf8",
        "huge-count",
        "huge-value",
        "bad-value-utf8",
        "suffix",
        "mutable",
        "str",
        "none",
    ],
)
def test_invalid_payload_is_rejected(payload):
    with pytest.raises(EetFormatError):
        parse_number_cache(payload)


@pytest.mark.parametrize(
    "cache",
    [
        EetNumberCache(None, ()),
        EetNumberCache("\ud800", ()),
        EetNumberCache("template", (None,)),
        EetNumberCache("template", (42,)),
        EetNumberCache("template", ("\udfff",)),
    ],
    ids=["template-type", "template-surrogate", "value-none", "value-number", "value-surrogate"],
)
def test_invalid_cache_cannot_be_serialized(cache):
    with pytest.raises(EetFormatError):
        cache.to_payload()


@pytest.mark.parametrize("text", [None, b"12", 12, True, "\ud800"])
def test_invalid_text_is_rejected(text):
    with pytest.raises(EetFormatError):
        build_number_cache(text)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_utf16_units": True},
        {"max_utf16_units": -1},
        {"max_utf16_units": 0x80000000},
        {"max_utf16_units": 200.0},
        {"max_utf16_units": "200"},
        {"roman": 1},
        {"roman": None},
        {"except_i_space": 0},
        {"except_i_space": "false"},
    ],
)
def test_invalid_options_are_rejected(changes):
    with pytest.raises(ValueError):
        EetNumberOptions(**changes)


@pytest.mark.integration
@pytest.mark.parametrize(
    "path,expected_count",
    [
        (Path("tests/trans_exe/eet/BDD_WL.eet"), 4038),
        (_INSTALL / "BDD/BDD_Reconquista.eet", 3839),
        (_INSTALL / "BDD/BDD_Reconquista ANK.eet", 3867),
        (_INSTALL / "Bases Persos/TempSession/3DNPC_B675CB1D_BA3D60BA.eet", 910),
    ],
    ids=["wl", "reconquista", "reconquista-ank", "classic-session"],
)
def test_every_existing_real_cache_rebuilds_byte_exactly(path, expected_count):
    if not path.is_file():
        pytest.skip("Optional local EET corpus is not installed")
    document = EetDocument.from_bytes(path.read_bytes())
    count = 0
    for ordinal, record in enumerate(document.records):
        for side, text, payload in (
            ("original", record.original, record.original_variables),
            ("translation", record.translation, record.translation_variables),
        ):
            if not payload:
                continue
            cache = build_number_cache(text, group=record.group, field=record.field)
            if cache is None or cache.to_payload() != payload:
                # Report only location, never include users' translation data.
                pytest.fail(f"Stored cache differs at record {ordinal}, {side}")
            count += 1
    assert count == expected_count
