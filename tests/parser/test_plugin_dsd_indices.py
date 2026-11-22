# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read official DSD indices without replacing native plugin write locators."""

import struct

import pytest

from tests.plugin_fixtures import _field, _record
from transbridge.converter.dsd_metadata import DSD_QUEST_INDEX, DSD_REGION_INDEX
from transbridge.parser.plugin_parser import PluginParser


def _raw_field(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<H", len(payload)) + payload


def _plugin_bytes(kind: bytes, payload: bytes) -> bytes:
    record = _record(kind, 0x800, _field(b"EDID", "Fixture") + payload)
    group = struct.pack("<4sI4siHHI", b"GRUP", 24 + len(record), kind, 0, 0, 0, 0)
    return _record(b"TES4", 0, b"") + group + record


def _parse(tmp_path, kind, payload):
    parser = PluginParser()
    entries = parser.parse_plugin(
        tmp_path / "source.esp",
        captured_content=_plugin_bytes(kind, payload),
        discover_sibling_strings=False,
        raise_on_error=True,
    )
    return parser, entries


def test_quest_log_uses_stage_and_log_position_including_entries_without_cnam(tmp_path):
    log_entry = _raw_field(b"QSDT", b"\x00")
    parser, entries = _parse(
        tmp_path,
        b"QUST",
        _raw_field(b"INDX", struct.pack("<HBB", 0, 0, 0))
        + log_entry
        + _field(b"CNAM", "Stage zero")
        + log_entry
        + log_entry
        + _field(b"CNAM", "Third entry")
        + _raw_field(b"INDX", struct.pack("<HBB", 512, 0, 0))
        + log_entry
        + _field(b"CNAM", "Next stage"),
    )

    assert [entry.original for entry in entries] == ["Stage zero", "Third entry", "Next stage"]
    assert [entry.dsd_index for entry in entries] == [0, 2, 512]
    assert [dict(entry.metadata)[DSD_QUEST_INDEX] for entry in entries] == [0, 2, 512]

    native_indices = [field.index for field in parser.get_plugin().extract_strings_with_context()]
    assert [entry.key for entry in entries] == [f"Fixture:00000800|{index}~QUST:CNAM" for index in native_indices]
    assert entries[0].key == entries[1].key
    assert entries[0].requires_original_match and entries[1].requires_original_match


def test_quest_text_without_log_boundary_has_no_proven_dsd_index(tmp_path):
    parser, (entry,) = _parse(
        tmp_path, b"QUST", _raw_field(b"INDX", struct.pack("<HBB", 10, 0, 0)) + _field(b"CNAM", "Legacy log")
    )

    (native,) = parser.get_plugin().extract_strings_with_context()
    assert entry.dsd_index == native.index
    assert DSD_QUEST_INDEX not in dict(entry.metadata)


@pytest.mark.parametrize(
    ("kind", "subrecord", "payload"),
    [
        (b"MESG", "ITXT", _field(b"ITXT", "First") + _field(b"ITXT", "Second")),
        (b"INFO", "NAM1", _raw_field(b"TRDT", bytes(24)) + _field(b"NAM1", "First")),
        (b"QUST", "NNAM", _raw_field(b"QOBJ", b"\x00\x00") + _field(b"NNAM", "First")),
        (
            b"PERK",
            "EPF2",
            _raw_field(b"EPFT", b"\x04") + _field(b"EPF2", "First") + _raw_field(b"EPF3", bytes(4)),
        ),
        (b"PERK", "EPFD", _raw_field(b"EPFT", b"\x07") + _field(b"EPFD", "First")),
    ],
)
def test_zero_index_survives_plugin_entry_conversion(tmp_path, kind, subrecord, payload):
    _, entries = _parse(tmp_path, kind, payload)

    assert entries[0].dsd_index == 0
    assert entries[0].key.endswith(f"|0~{kind.decode()}:{subrecord}")


def test_objective_uses_unsigned_dsd_index_and_legacy_signed_write_index(tmp_path):
    _, (entry,) = _parse(tmp_path, b"QUST", _raw_field(b"QOBJ", b"\xff\xff") + _field(b"NNAM", "Objective"))

    assert entry.dsd_index == 65535
    assert entry.key.endswith("|-1~QUST:NNAM")


@pytest.mark.parametrize("position", [0, 1])
def test_region_map_uses_region_data_entry_position(tmp_path, position):
    _, (entry,) = _parse(
        tmp_path,
        b"REGN",
        _raw_field(b"RDAT", struct.pack("<IBB2x", 2, 0, 0)) * position
        + _raw_field(b"RDAT", struct.pack("<IBB2x", 4, 0, 0))
        + _field(b"RDMP", "Map name"),
    )

    assert entry.dsd_index == position
    assert dict(entry.metadata)[DSD_REGION_INDEX] == position
    assert entry.key.endswith("|1~REGN:RDMP")
