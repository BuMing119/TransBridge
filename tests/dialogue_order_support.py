"""Tiny real plugin with reverse physical topic order and a multi-response INFO."""

import struct

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference


def dialogue_bytes():
    def children(topic, content):
        return struct.pack("<4sIIiHHHH", b"GRUP", len(content) + 24, topic, 7, 0, 0, 0, 0) + content

    def response(number, text):
        return field("TRDT", bytes(12) + bytes([number]) + bytes(11)) + field("NAM1", text.encode() + b"\0")

    return plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0") + field("FULL", b"Fixture Quest\0")),
        "BOOK": record("BOOK", 0x80, field("FULL", b"Book\0")),
        "DIAL": (
            record("DIAL", 0x20, reference("QNAM", 1) + field("FULL", b"Later topic\0"))
            + children(0x20, record("INFO", 0x21, response(1, "Later reply")))
            + record("DIAL", 0x10, reference("QNAM", 1) + field("FULL", b"First topic\0"))
            + children(
                0x10,
                record(
                    "INFO", 0x11, response(2, "First reply") + response(1, "Second reply") + reference("TCLT", 0x20)
                ),
            )
        ),
    })
