# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Structural string-table records with explicit, strict text codecs."""

from dataclasses import dataclass
import struct

from transbridge.config.text_encoding import STRICT_READ_POLICY, TextReadOptions

from .game_text import describe_game_text_error, encode_game_text
from .text_reader import decode_strings_text


@dataclass(frozen=True, slots=True)
class LocalizedStringRecord:
    directory_index: int
    string_id: int
    offset: int
    raw_payload: bytes
    raw_chunk: bytes
    text: str
    encoding: str

    def used_fallback(self, options: TextReadOptions) -> bool:
        return self.encoding not in {options.input_encoding, "utf-8-sig"}


def parse_records(
    data: bytes,
    length_prefixed: bool,
    input_encoding: str | None = "utf-8",
    *,
    read_options: TextReadOptions | None = None,
    legacy_linked: bool = False,
) -> tuple[LocalizedStringRecord, ...]:
    """None validates the container only, without guessing a text encoding."""
    if len(data) < 8:
        raise ValueError("localized strings header is truncated")
    count, data_size = struct.unpack_from("<II", data, 0)
    directory_end = 8 + count * 8
    if directory_end > len(data) or directory_end + data_size != len(data):
        raise ValueError("localized strings header size is inconsistent")
    if read_options is None and input_encoding is not None:
        read_options = TextReadOptions(input_encoding, text_read_policy=STRICT_READ_POLICY)
    records = []
    for index in range(count):
        string_id, offset = struct.unpack_from("<II", data, 8 + index * 8)
        position = directory_end + offset
        if position < directory_end or position >= len(data):
            raise ValueError("localized strings record offset is out of range")
        if length_prefixed:
            if position + 4 > len(data):
                raise ValueError("localized strings record length is truncated")
            length = struct.unpack_from("<I", data, position)[0]
            end = position + 4 + length
            if length < 1 or end > len(data) or data[end - 1] != 0:
                raise ValueError("localized strings length-prefixed record is invalid")
            raw_payload = data[position + 4 : end - 1]
            raw_chunk = data[position:end]
        else:
            try:
                end = data.index(b"\x00", position, directory_end + data_size)
            except ValueError as exc:
                raise ValueError("localized strings record has no terminator") from exc
            raw_payload = data[position:end]
            raw_chunk = data[position : end + 1]
        try:
            text, encoding = (
                ("", "")
                if read_options is None
                else decode_strings_text(
                    raw_payload, read_options, legacy_linked=legacy_linked, length_prefixed=length_prefixed
                )
            )
        except UnicodeDecodeError as exc:
            raise ValueError(f"String ID {string_id} cannot be decoded as {exc.encoding} at byte {exc.start}") from exc
        records.append(LocalizedStringRecord(index, string_id, offset, raw_payload, raw_chunk, text, encoding))
    return tuple(records)


def encode_chunk(text: str, encoding: str, length_prefixed: bool) -> bytes:
    try:
        payload = encode_game_text(text, encoding, allow_nul=length_prefixed)
    except ValueError as exc:
        reason = describe_game_text_error(exc)
        raise ValueError(f"Text cannot be encoded as {encoding}: {reason}") from exc
    terminated = payload + b"\x00"
    return struct.pack("<I", len(terminated)) + terminated if length_prefixed else terminated


def render(chunks: list[tuple[int, bytes]]) -> bytes:
    directory = []
    payload = []
    offset = 0
    for string_id, chunk in chunks:
        directory.append(struct.pack("<II", string_id, offset))
        payload.append(chunk)
        offset += len(chunk)
    return struct.pack("<II", len(chunks), offset) + b"".join(directory) + b"".join(payload)
