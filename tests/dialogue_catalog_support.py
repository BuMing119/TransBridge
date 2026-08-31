"""Small real plugin byte streams for record navigation regressions."""

from io import BytesIO
import struct

from transbridge.parser.plugin.plugin_with_context import SSEPluginWithContext


def field(kind: str, data: bytes) -> bytes:
    return struct.pack("<4sH", kind.encode("ascii"), len(data)) + data


def reference(kind: str, value: int) -> bytes:
    return field(kind, struct.pack("<I", value))


def record(kind: str, form_id: int, fields: bytes) -> bytes:
    return struct.pack("<4sIIIIHH", kind.encode("ascii"), len(fields), 0, form_id, 0, 44, 0) + fields


def plugin_bytes(groups: dict[str, bytes]) -> bytes:
    header = record("TES4", 0, field("HEDR", struct.pack("<fII", 1.7, 0, 0x800)))
    return header + b"".join(
        struct.pack("<4sI4sIHHHH", b"GRUP", len(records) + 24, kind.encode("ascii"), 0, 0, 0, 0, 0) + records
        for kind, records in groups.items()
    )


def dialogue_plugin_bytes(scene_name="TestScene") -> bytes:
    return plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"Quest\0")),
        "DIAL": (
            record("DIAL", 0x10, field("EDID", b"Topic10\0") + reference("QNAM", 1) + field("DATA", b"\0\0\0\0"))
            + record("DIAL", 0x11, reference("QNAM", 1) + field("DATA", b"\0\2\x0e\0"))
        ),
        "SCEN": (
            record(
                "SCEN",
                0x12,
                field("EDID", scene_name.encode("ascii") + b"\0")
                + reference("DNAM", 0xDEAD)  # Actor flags are not topic references.
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x10)
                + field("ANAM", b"")
                + field("ANAM", b"\1\0")
                + reference("PNAM", 0xBAD)
                + reference("DATA", 0xDEAD)
                + field("ANAM", b"")
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x11)
                + field("ANAM", b"")
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x10)
                + field("ANAM", b"")
                + reference("PNAM", 1),
            )
            + record("SCEN", 0x13, field("EDID", b"EmptyScene\0") + reference("PNAM", 1))
        ),
    })


def dialogue_plugin() -> SSEPluginWithContext:
    return SSEPluginWithContext.from_stream(BytesIO(dialogue_plugin_bytes()), "fixture.esp")
