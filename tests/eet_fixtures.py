"""Synthetic EET records for application workflows; never user translation data."""

from dataclasses import replace

from transbridge.parser.eet_binary_codec import EetDocument, EetRecord


def eet_record(**changes):
    record = EetRecord(
        "NPC_",
        "00000800",
        "Greeting",
        "FULL",
        "Hello",
        "你好",
        "personal",
        1,
        99,
        23,
        "comment",
        0x12345678,
        "Hello",
        0x87654321,
        icon=37,
    )
    return replace(record, **changes)


def write_eet(path, records=None, *, type_dt=1):
    document = EetDocument(3, "Skyrim SE", tuple(records if records is not None else (eet_record(),)), None, type_dt)
    path.write_bytes(document.to_bytes())
    return path
