"""Wire-level experiments against xTranslator's sequential SST layout.

The synthetic fixtures use independent struct calls, not codec internals. Real
files are optional local interoperability evidence and never modified here.
"""

from dataclasses import replace
from pathlib import Path
import struct

import pytest

from transbridge.parser.xt.sst_codec import SstDocument, SstRecord, sst_hash


def _string(value: str) -> bytes:
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    return struct.pack("<i", len(encoded)) + encoded


def _header(magic=b"SSU9", *, flag=0, masters=(), labels=()):
    data = magic + bytes([flag])
    if magic == b"SSU9":
        data += struct.pack("<i", len(masters)) + b"".join(_string(name) for name in masters)
    data += struct.pack("<i", len(labels))
    return data + b"".join(struct.pack("<i", label_id) + _string(name) for label_id, name in labels)


def _record(**changes):
    values = dict(
        list_index=0,
        string_id=-2147483648,
        form_id=0xFE012ABC,
        record_type="NPC_",
        field_type="FULL",
        index=0,
        index_max=65535,
        edid_hash=0x89ABCDEF,
        collab_id=0,
        params=1,
        original="Hello",
        translation="你好",
    )
    values.update(changes)
    return SstRecord(**values)


def _wire_record(record):
    return (
        struct.pack(
            "<BiI4s4sHHIBB",
            record.list_index,
            record.string_id,
            record.form_id,
            record.record_type.encode("latin-1"),
            record.field_type.encode("latin-1"),
            record.index,
            record.index_max,
            record.edid_hash,
            record.collab_id,
            record.params,
        )
        + _string(record.original)
        + _string(record.translation)
    )


def _document(record=None, **changes):
    values = dict(magic=b"SSU9", flag=0, masters=(), labels=(), records=(_record() if record is None else record,))
    values.update(changes)
    return SstDocument(**values)


@pytest.mark.parametrize("magic,header_size", [(b"SSU8", 9), (b"SSU9", 13)])
def test_minimal_header_and_record_are_exact_little_endian_wire(magic, header_size):
    expected = _record()
    header = _header(magic)
    assert len(header) == header_size
    content = header + _wire_record(expected)
    parsed = SstDocument.from_bytes(content)
    assert parsed == _document(expected, magic=magic)
    assert parsed.to_bytes() == content


def test_master_count_labels_and_flag_have_explicit_widths():
    masters = ("Skyrim.esm", "工具😀.esm")
    labels = ((7, "审校"), (255, "Review 😀"))
    content = _header(flag=0xA5, masters=masters, labels=labels) + _wire_record(_record(collab_id=255))
    assert content[4:9] == b"\xa5\x02\0\0\0"
    assert content[9:13] == b"\x14\0\0\0"
    parsed = SstDocument.from_bytes(content)
    assert parsed.flag == 0xA5
    assert parsed.masters == masters
    assert parsed.labels == labels
    assert parsed.records == (_record(collab_id=255),)
    assert parsed.to_bytes() == content


@pytest.mark.parametrize("magic", [b"SSU8", b"SSU9"])
def test_all_three_lists_and_old_false_positive_status_survive(magic):
    records = tuple(_record(list_index=i, params=0x40, collab_id=0, index=i) for i in range(3))
    content = _header(magic) + b"".join(_wire_record(record) for record in records)
    parsed = SstDocument.from_bytes(content)
    assert parsed.records == records
    assert parsed.to_bytes() == content


@pytest.mark.parametrize(
    "original,translation",
    [("", ""), ("", "译文"), ("Source", ""), ("😀𝄞", "𠀀中文"), ("A" * 32768, "中" * 32769), ("\ud800", "\udfff")],
    ids=[
        "empty-both",
        "empty-source",
        "empty-translation",
        "supplementary-unicode",
        "uint32-lengths",
        "raw-surrogates",
    ],
)
def test_strings_preserve_empty_unicode_and_lengths_above_uint16(original, translation):
    record = _record(original=original, translation=translation, record_type="Z9_?", field_type="AB12")
    content = _header() + _wire_record(record)
    parsed = SstDocument.from_bytes(content)
    assert parsed.records == (record,)
    assert parsed.to_bytes() == content


def test_unknown_signature_bytes_and_signed_label_ids_are_preserved():
    record = _record(record_type="\xff\0_?", field_type="\x80XYZ")
    content = _header(b"SSU8", labels=((-2147483648, ""), (2147483647, "Label"))) + _wire_record(record)
    parsed = SstDocument.from_bytes(content)
    assert parsed.records == (record,)
    assert parsed.labels == ((-2147483648, ""), (2147483647, "Label"))
    assert parsed.to_bytes() == content


@pytest.mark.parametrize("magic", [b"SSU8", b"SSU9"])
def test_complete_last_record_is_not_appended_twice(magic):
    record = _record()
    content = _header(magic) + _wire_record(record)
    assert SstDocument.from_bytes(content).to_bytes() == content
    assert SstDocument.from_bytes(content).records == (record,)


@pytest.mark.parametrize("magic", [b"SSU8", b"SSU9"])
def test_empty_document_is_a_complete_file(magic):
    content = _header(magic)
    parsed = SstDocument.from_bytes(content)
    assert parsed.records == ()
    assert parsed.to_bytes() == content


def test_every_truncated_header_or_partial_record_is_rejected():
    header = _header(masters=("Base.esm",), labels=((9, "Reviewer"),))
    complete = header + _wire_record(_record())
    for cut in range(len(complete)):
        if cut == len(header):
            continue  # A complete header without records is a valid empty SST.
        with pytest.raises(ValueError):
            SstDocument.from_bytes(complete[:cut])


@pytest.mark.parametrize(
    "content",
    [
        b"SSU7" + b"\0" * 9,
        b"SSU9\0" + struct.pack("<i", -1),
        b"SSU9\0" + struct.pack("<ii", 1, -2),
        b"SSU9\0" + struct.pack("<ii", 1, 1) + b"A",
        b"SSU9\0" + struct.pack("<ii", 1, 0x7FFFFFFE),
        b"SSU8\0" + struct.pack("<i", -1),
        _header() + b"\0",
        _header() + b"\xff" * 35,
    ],
    ids=[
        "magic",
        "negative-count",
        "negative-size",
        "odd-utf16",
        "oversized-size",
        "negative-labels",
        "tail",
        "bad-record",
    ],
)
def test_invalid_headers_and_trailing_fragments_are_rejected(content):
    with pytest.raises(ValueError):
        SstDocument.from_bytes(content)


@pytest.mark.parametrize("length", [-2, 1, 0x7FFFFFFE])
def test_invalid_record_string_lengths_are_rejected(length):
    wire = bytearray(_wire_record(_record()))
    struct.pack_into("<i", wire, 27, length)
    with pytest.raises(ValueError):
        SstDocument.from_bytes(_header() + wire)


@pytest.mark.parametrize("list_index", [3, 255])
def test_invalid_list_index_is_rejected_on_read(list_index):
    wire = bytearray(_wire_record(_record()))
    wire[0] = list_index
    with pytest.raises(ValueError):
        SstDocument.from_bytes(_header() + wire)


@pytest.mark.parametrize(
    "field,value",
    [
        ("list_index", -1),
        ("list_index", 3),
        ("list_index", True),
        ("form_id", "123"),
        ("index", 1.5),
        ("string_id", -(2**31) - 1),
        ("string_id", 2**31),
        ("form_id", -1),
        ("form_id", 2**32),
        ("index", -1),
        ("index", 65536),
        ("index_max", -1),
        ("index_max", 65536),
        ("edid_hash", -1),
        ("edid_hash", 2**32),
        ("collab_id", -1),
        ("collab_id", 256),
        ("params", -1),
        ("params", 256),
        ("record_type", "NPC"),
        ("record_type", "NPC__"),
        ("record_type", "中文字符"),
        ("field_type", "TOOLONG"),
    ],
)
def test_writer_rejects_values_that_cannot_be_represented(field, value):
    with pytest.raises(ValueError):
        _document(_record(**{field: value})).to_bytes()


@pytest.mark.parametrize("changes", [{"magic": b"SSU0"}, {"flag": -1}, {"flag": 256}])
def test_writer_rejects_invalid_document_fields(changes):
    with pytest.raises(ValueError):
        _document(**changes).to_bytes()


def test_ssu8_cannot_silently_drop_a_master_list():
    with pytest.raises(ValueError):
        _document(magic=b"SSU8", masters=("Base.esm",)).to_bytes()


@pytest.mark.parametrize("label_id", [-2147483649, 2147483648, True])
def test_label_ids_obey_signed_int32_bounds(label_id):
    with pytest.raises(ValueError):
        _document(labels=((label_id, "Label"),)).to_bytes()


def test_editing_translation_preserves_all_other_record_metadata():
    records = (_record(list_index=1), _record(list_index=2, string_id=7, params=0x41, collab_id=2))
    original = _header(masters=("Base.esm",), labels=((2, "Reviewer"),))
    original += b"".join(_wire_record(record) for record in records)
    parsed = SstDocument.from_bytes(original)
    changed = replace(parsed, records=(replace(records[0], translation="New 😀 translation"), records[1]))
    actual = SstDocument.from_bytes(changed.to_bytes())
    assert actual == changed
    assert actual.records[1] == records[1]
    assert replace(actual.records[0], translation=records[0].translation) == records[0]
    assert parsed.to_bytes() == original


def test_hash_uses_low_byte_of_each_utf16_code_unit_not_utf8():
    assert sst_hash("") == 0x811C9DC5
    assert sst_hash("hello") == 0x4F9F2CAB
    assert sst_hash("\u0100") == sst_hash("\0")
    assert sst_hash("😀") == sst_hash("=\0")  # D83D DE00 contributes 3D 00.


_CORPUS_COUNTS = {
    "_resourcepack_english_chinese.sst": 27,
    "ccvsvsse002-pets_english_chinese.sst": 171,
    "hlioremi_english_chinese.sst": 8604,
    "ssu8/ccbgssse002-exoticarrows_english_chinese.sst": 59,
    "ssu8/ccbgssse003-zombies_english_chinese.sst": 83,
    "ssu8/ccbgssse004-ruinsedge_english_chinese.sst": 26,
    "ssu8/ccbgssse006-stendarshammer_english_chinese.sst": 12,
    "ssu8/ccbgssse007-chrysamere_english_chinese.sst": 27,
    "ssu8/ccbgssse008-wraithguard_english_chinese.sst": 102,
    "ssu8/ccbgssse010-petdwarvenarmoredmudcrab_english_chinese.sst": 26,
    "ssu8/ccbgssse011-hrsarmrelvn_english_chinese.sst": 1,
    "ssu8/ccbgssse012-hrsarmrstl_english_chinese.sst": 1,
    "ssu8/ccbgssse013-dawnfang_english_chinese.sst": 126,
    "ssu8/ccbgssse014-spellpack01_english_chinese.sst": 134,
    "ssu8/ccbgssse016-umbra_english_chinese.sst": 41,
    "ssu8/ccbgssse018-shadowrend_english_chinese.sst": 51,
    "ssu8/ccbgssse019-staffofsheogorath_english_chinese.sst": 39,
    "ssu8/ccbgssse020-graycowl_english_chinese.sst": 106,
    "ssu8/ccbgssse021-lordsmail_english_chinese.sst": 54,
}
_CORPUS_ROOT = Path(__file__).resolve().parents[1] / "trans_exe" / "xt"


@pytest.mark.integration
@pytest.mark.parametrize("relative_path,count", _CORPUS_COUNTS.items())
def test_local_real_samples_have_exact_record_counts_and_bytes(relative_path, count):
    path = _CORPUS_ROOT / relative_path
    if not path.is_file():
        pytest.skip("Optional local xTranslator sample is unavailable")
    original = path.read_bytes()
    document = SstDocument.from_bytes(original)
    assert len(document.records) == count
    assert document.to_bytes() == original
    assert path.read_bytes() == original


@pytest.mark.integration
@pytest.mark.parametrize("filename", ["_test_rt.sst", "_test_roundtrip.sst"])
def test_local_historical_writer_outputs_are_structurally_invalid(filename):
    path = _CORPUS_ROOT / filename
    if not path.is_file():
        pytest.skip("Optional historical experimental output is unavailable")
    with pytest.raises(ValueError):
        SstDocument.from_bytes(path.read_bytes())
