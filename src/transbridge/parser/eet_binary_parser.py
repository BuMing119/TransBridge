"""EET binary format parser (*.eet files).

Binary format v2 structure:
  Header:   magic "EET_" (4B) | version uint32 LE | section_count uint32 LE
  Section1: "GAME" (4B) | uint16 LE (=0)
  Section2: "LINE" (4B) | record_count uint32 LE | [records...]
  Section3: "PHRA" (4B) | section_size uint32 LE | phrase_count uint32 LE | [data...]

Each LINE record:
  size uint32 LE (total, including this field)
  field_count uint32 LE (=4)
  GRUP 4B fixed ASCII
  ID: len uint32 LE + UTF-8
  EDID: len uint32 LE + UTF-8
  CHAMP: len uint32 LE + UTF-8
  ORIGINAL: len uint32 LE + UTF-8
  TRADUIT: len uint32 LE + UTF-8
  PERSO: len uint32 LE + UTF-8 (often empty)
  STATUS: uint32 LE
  INDEX: uint16 LE
  IDSTEXTE: uint16 LE
  COMMENTAIRE: len uint32 LE + UTF-8 (often empty)
  hash1: uint32 LE
  extra_len: uint32 LE | extra_str: UTF-8 (copy of ORIGINAL)
  hash2: uint32 LE (== hash1)
  [zero padding to size] | FFFFFFFF separator
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
from pathlib import Path
import struct

from transbridge.parser.eet_parser import EET_Entry

logger = logging.getLogger(__name__)

_MAGIC = b"EET_"
_REC_SEP = 0xFFFFFFFF


def _read_str(data: bytes, pos: int) -> tuple[str, int]:
    """Read a length-prefixed UTF-8 string. Returns (string, new_position)."""
    length = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if length == 0:
        return "", pos
    s = data[pos : pos + length].decode("utf-8", errors="replace")
    pos += length
    return s, pos


class EET_BinaryParser:
    """Parser for EET binary translation files (*.eet).

    Parses the three-section binary format produced by the EET translation tool.
    Compatible with EET_XmlParser query interface (find, get_by_grup, etc.).
    """

    def __init__(self, entries: list[EET_Entry], game_data: int = 0, phrases: bytes = b"") -> None:
        self.entries = entries
        self.game_data = game_data
        self.phrases = phrases  # raw PHRA section bytes

        # Build lookup indices
        self._by_key: dict[tuple[str, str, str, str], list[EET_Entry]] = {}
        self._by_grup: dict[str, list[EET_Entry]] = {}
        self._by_id: dict[str, list[EET_Entry]] = {}
        self._by_edid: dict[str, list[EET_Entry]] = {}

        for e in entries:
            self._by_key.setdefault(e.key, []).append(e)
            self._by_grup.setdefault(e.grup, []).append(e)
            self._by_id.setdefault(e.id, []).append(e)
            if e.edid:
                self._by_edid.setdefault(e.edid, []).append(e)

    # ── Factory ──

    @classmethod
    def from_file(cls, path: str | Path) -> EET_BinaryParser:
        """Parse an EET binary file. Auto-detects binary vs XML."""
        data = Path(path).read_bytes()
        if data[:4] == _MAGIC:
            return cls._parse_binary(data)
        raise ValueError(f"Not a valid EET binary file: {path}")

    # ── Binary parser ──

    @classmethod
    def _parse_binary(cls, data: bytes) -> EET_BinaryParser:
        version = struct.unpack_from("<I", data, 4)[0]
        if version != 2:
            raise ValueError(f"Unsupported EET binary version: {version}")

        section_count = struct.unpack_from("<I", data, 8)[0]
        pos = 12

        entries: list[EET_Entry] = []
        game_data = 0
        phrases = b""

        for _ in range(section_count):
            if pos + 4 > len(data):
                break
            sec_name = data[pos : pos + 4].decode("ascii")
            pos += 4

            if sec_name == "GAME":
                game_data = struct.unpack_from("<H", data, pos)[0]
                pos += 2

            elif sec_name == "LINE":
                # record_count is informational; we parse until a non-record pattern
                _record_count = struct.unpack_from("<I", data, pos)[0]
                pos += 4
                entries, pos = cls._parse_line_records(data, pos)

            elif sec_name == "PHRA":
                phra_size = struct.unpack_from("<I", data, pos)[0]
                pos += 4
                phrases = data[pos : pos + phra_size]
                pos += phra_size

            else:
                logger.warning("Unknown section name: %r at offset 0x%X", sec_name, pos - 4)
                break

        return cls(entries=entries, game_data=game_data, phrases=phrases)

    @classmethod
    def _parse_line_records(cls, data: bytes, pos: int) -> tuple[list[EET_Entry], int]:
        """Parse all records in the LINE section. Returns (entries, new_position)."""
        entries: list[EET_Entry] = []
        data_len = len(data)

        while pos < data_len - 12:
            rec_size = struct.unpack_from("<I", data, pos)[0]

            # Terminate on separator gap or invalid size
            if rec_size == _REC_SEP:
                pos += 4
                continue
            if rec_size < 24 or rec_size > 200_000:
                break

            # Sanity: field_count must be 4 and GRUP must be ASCII
            fc = struct.unpack_from("<I", data, pos + 4)[0]
            if fc != 4:
                break
            grup_bytes = data[pos + 8 : pos + 12]
            if not all(32 <= b < 127 for b in grup_bytes):
                break

            rec_start = pos
            try:
                entry = cls._parse_one_record(data, pos)
                if entry is not None:
                    entries.append(entry)
            except (struct.error, UnicodeDecodeError, IndexError) as exc:
                logger.warning("Failed to parse record at offset 0x%X: %s", pos, exc)

            pos = rec_start + rec_size

            # Consume separator
            if pos + 4 <= data_len and struct.unpack_from("<I", data, pos)[0] == _REC_SEP:
                pos += 4

        return entries, pos

    @classmethod
    def _parse_one_record(cls, data: bytes, pos: int) -> EET_Entry | None:
        """Parse a single record at the given offset. Returns EET_Entry or None."""
        _size = struct.unpack_from("<I", data, pos)[0]
        pos += 4

        _field_count = struct.unpack_from("<I", data, pos)[0]  # always 4
        pos += 4

        grup = data[pos : pos + 4].decode("ascii")
        pos += 4

        id_val, pos = _read_str(data, pos)
        edid_val, pos = _read_str(data, pos)
        champ_val, pos = _read_str(data, pos)
        original, pos = _read_str(data, pos)
        traduit, pos = _read_str(data, pos)

        perso, pos = _read_str(data, pos)

        status = struct.unpack_from("<I", data, pos)[0]
        pos += 4

        index = struct.unpack_from("<H", data, pos)[0]
        pos += 2

        idstexte = struct.unpack_from("<H", data, pos)[0]
        pos += 2

        commentaire, pos = _read_str(data, pos)

        # Hash section: hash1 + extra original copy + hash2 — skip (validates only)
        # _hash1 = struct.unpack_from("<I", data, pos)[0]
        # pos += 4
        # extra_str, pos = _read_str(data, pos)
        # _hash2 = struct.unpack_from("<I", data, pos)[0]
        # pos += 4
        # Remaining bytes in record are zero padding — consumed by size-based positioning

        return EET_Entry(
            grup=grup,
            id=id_val,
            edid=edid_val,
            champ=champ_val,
            original=original,
            traduit=traduit,
            perso=perso,
            index=index if index != 0 else None,
            status=status,
            idstexte=idstexte if idstexte != 0 else None,
            commentaire=commentaire,
            icon=None,
        )

    # ── Query interface (mirrors EET_XmlParser) ──

    def __iter__(self) -> Iterator[EET_Entry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def find(
        self,
        *,
        grup: str | None = None,
        id: str | None = None,
        edid: str | None = None,
        champ: str | None = None,
        original_contains: str | None = None,
        traduit_contains: str | None = None,
        status: int | None = None,
    ) -> list[EET_Entry]:
        """Filter entries by field values."""
        from collections.abc import Iterable

        res: Iterable[EET_Entry] = self.entries

        if grup is not None:
            res = (e for e in res if e.grup == grup)
        if id is not None:
            res = (e for e in res if e.id == id)
        if edid is not None:
            res = (e for e in res if e.edid == edid)
        if champ is not None:
            res = (e for e in res if e.champ == champ)
        if status is not None:
            res = (e for e in res if e.status == status)
        if original_contains is not None:
            res = (e for e in res if original_contains in e.original)
        if traduit_contains is not None:
            res = (e for e in res if traduit_contains in e.traduit)

        return list(res)

    def get_by_key(self, grup: str, id: str, edid: str, champ: str) -> list[EET_Entry]:
        return list(self._by_key.get((grup, id, edid, champ), []))

    def get_by_grup(self, grup: str) -> list[EET_Entry]:
        return list(self._by_grup.get(grup, []))

    def get_by_id(self, id: str) -> list[EET_Entry]:
        return list(self._by_id.get(id, []))

    def get_by_edid(self, edid: str) -> list[EET_Entry]:
        return list(self._by_edid.get(edid, []))

    def to_dicts(self) -> list[dict]:
        """Export entries as dicts (compatible with EET_XmlParser.to_dicts)."""
        out = []
        for e in self.entries:
            out.append(
                dict(
                    GRUP=e.grup,
                    ID=e.id,
                    EDID=e.edid,
                    CHAMP=e.champ,
                    ORIGINAL=e.original,
                    TRADUIT=e.traduit,
                    PERSO=e.perso,
                    INDEX=e.index,
                    STATUS=e.status,
                    IDSTEXTE=e.idstexte,
                    COMMENTAIRE=e.commentaire,
                    ICON=e.icon,
                )
            )
        return out
