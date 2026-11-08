"""Lossless SSU8/SSU9 binary codec shared by product I/O and SST diagnostics.

Independently implemented from the binary layout read/written by xTranslator:
https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_SSTFunc.pas#L203-L435
https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_typedef.pas#L68-L97

Both formats contain a flag byte and collaboration-label table. SSU9 inserts
a master-name table before that label table. Records run to EOF; there is no
record count, terminator, grouping, padding, or nested record structure.
Integers are little endian; text lengths are signed int32 byte lengths.

UTF-16 uses surrogatepass to preserve Delphi's raw UTF-16 code units, including
isolated surrogates. Four-byte record/field signatures use Latin-1 as a lossless
byte mapping, without filtering unknown record types. Numeric bounds and list
indices are validated; status bits, including validated, are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

_INT32_MAX = 0x7FFFFFFF
_RECORD = struct.Struct("<BiI4s4sHHIBB")


@dataclass(frozen=True)
class SstRecord:
    """One complete SST record; index/index_max retain their on-disk values."""

    list_index: int
    string_id: int
    form_id: int
    record_type: str
    field_type: str
    index: int
    index_max: int
    edid_hash: int
    collab_id: int
    params: int
    original: str
    translation: str


@dataclass(frozen=True)
class SstDocument:
    """A complete SST file, including all header metadata and ordered records."""

    magic: bytes
    flag: int
    masters: tuple[str, ...]
    labels: tuple[tuple[int, str], ...]
    records: tuple[SstRecord, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> SstDocument:
        return from_bytes(data)

    def to_bytes(self) -> bytes:
        return to_bytes(self)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def take(self, size: int, field: str) -> bytes:
        remaining = len(self.data) - self.position
        if size < 0 or size > remaining:
            raise ValueError(f"Truncated SST {field} at offset {self.position}: need {size} bytes, have {remaining}")
        start = self.position
        self.position += size
        return self.data[start : self.position]

    def integer(self, field: str) -> int:
        return struct.unpack("<i", self.take(4, field))[0]

    def count(self, field: str, minimum_item_size: int) -> int:
        count = self.integer(field)
        remaining = len(self.data) - self.position
        if count < 0 or count > remaining // minimum_item_size:
            raise ValueError(f"Invalid SST {field} {count} at offset {self.position - 4}")
        return count

    def text(self, field: str) -> str:
        size = self.integer(f"{field} length")
        if size < 0 or size % 2:
            raise ValueError(f"Invalid UTF-16 byte length {size} for {field} at offset {self.position - 4}")
        return self.take(size, field).decode("utf-16-le", errors="surrogatepass")


def from_bytes(data: bytes) -> SstDocument:
    """Read every field sequentially, rejecting incomplete or malformed files.

    An empty document is valid. Any bytes after the header must constitute
    complete records; unknown trailing payload is never silently discarded.
    """
    reader = _Reader(data)
    magic = reader.take(4, "magic")
    if magic not in (b"SSU8", b"SSU9"):
        raise ValueError(f"Unsupported SST magic: {magic!r}")
    flag = reader.take(1, "flag")[0]
    masters = ()
    if magic == b"SSU9":
        masters = tuple(reader.text(f"master[{i}]") for i in range(reader.count("master count", 4)))
    labels = tuple(
        (reader.integer(f"label[{i}] id"), reader.text(f"label[{i}] text"))
        for i in range(reader.count("label count", 8))
    )
    records = []
    while reader.position < len(data):
        record_number = len(records)
        prefix = f"record[{record_number}]"
        start = reader.position
        values = _RECORD.unpack(reader.take(_RECORD.size, f"{prefix} header"))
        if values[0] > 2:
            raise ValueError(f"Invalid SST list_index {values[0]} at offset {start}; expected 0, 1, or 2")
        records.append(
            SstRecord(
                list_index=values[0],
                string_id=values[1],
                form_id=values[2],
                record_type=values[3].decode("latin-1"),
                field_type=values[4].decode("latin-1"),
                index=values[5],
                index_max=values[6],
                edid_hash=values[7],
                collab_id=values[8],
                params=values[9],
                original=reader.text(f"{prefix} original"),
                translation=reader.text(f"{prefix} translation"),
            )
        )
    return SstDocument(magic, flag, masters, labels, tuple(records))


def _integer(value: int, minimum: int, maximum: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"SST {field} must be an integer in [{minimum}, {maximum}], got {value!r}")
    return value


def _text(value: str, field: str) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"SST {field} must be text")
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    if len(encoded) > _INT32_MAX:
        raise ValueError(f"SST {field} exceeds the signed int32 byte-length limit")
    return struct.pack("<i", len(encoded)) + encoded


def _signature(value: str, field: str) -> bytes:
    if not isinstance(value, str) or len(value) != 4:
        raise ValueError(f"SST {field} must contain exactly four single-byte characters")
    try:
        return value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"SST {field} must contain exactly four single-byte characters") from exc


def to_bytes(document: SstDocument) -> bytes:
    """Serialize without reordering records, dropping fields, or changing flags."""
    if document.magic not in (b"SSU8", b"SSU9"):
        raise ValueError(f"Unsupported SST magic: {document.magic!r}")
    if document.magic == b"SSU8" and document.masters:
        raise ValueError("SSU8 cannot contain a master table")
    parts = [document.magic, bytes([_integer(document.flag, 0, 255, "flag")])]
    if document.magic == b"SSU9":
        parts.append(struct.pack("<i", _integer(len(document.masters), 0, _INT32_MAX, "master count")))
        parts.extend(_text(name, f"master[{i}]") for i, name in enumerate(document.masters))
    parts.append(struct.pack("<i", _integer(len(document.labels), 0, _INT32_MAX, "label count")))
    for i, (label_id, label) in enumerate(document.labels):
        parts.append(struct.pack("<i", _integer(label_id, -0x80000000, _INT32_MAX, f"label[{i}] id")))
        parts.append(_text(label, f"label[{i}] text"))
    for i, record in enumerate(document.records):
        prefix = f"record[{i}]"
        parts.append(
            _RECORD.pack(
                _integer(record.list_index, 0, 2, f"{prefix} list_index"),
                _integer(record.string_id, -0x80000000, _INT32_MAX, f"{prefix} string_id"),
                _integer(record.form_id, 0, 0xFFFFFFFF, f"{prefix} form_id"),
                _signature(record.record_type, f"{prefix} record_type"),
                _signature(record.field_type, f"{prefix} field_type"),
                _integer(record.index, 0, 0xFFFF, f"{prefix} index"),
                _integer(record.index_max, 0, 0xFFFF, f"{prefix} index_max"),
                _integer(record.edid_hash, 0, 0xFFFFFFFF, f"{prefix} edid_hash"),
                _integer(record.collab_id, 0, 255, f"{prefix} collab_id"),
                _integer(record.params, 0, 255, f"{prefix} params"),
            )
        )
        parts.append(_text(record.original, f"{prefix} original"))
        parts.append(_text(record.translation, f"{prefix} translation"))
    return b"".join(parts)


def sst_hash(value: str) -> int:
    """xTranslator FNV-1a over the low byte of each UTF-16 code unit.

    This matches byte(str[i]) in Delphi, rather than hashing UTF-8 bytes:
    https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_Const.pas#L2452-L2463
    """
    result = 0x811C9DC5
    for value_byte in value.encode("utf-16-le", errors="surrogatepass")[::2]:
        result = ((result ^ value_byte) * 0x01000193) & 0xFFFFFFFF
    return result
