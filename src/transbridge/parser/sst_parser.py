# parser/sst_parser.py — XT SST binary file parser (SSU8 + SSU9)
import csv
import json
import logging
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── SSE record type suffixes ──
_VALID_EDID_SUFFIXES = (
    "FULL", "NAM1", "NAM2", "DATA", "DESC", "NAME", "GOLD", "SNAM",
    "QNAM", "CNAM", "EDID", "MODL", "MODT",
)


@dataclass(frozen=True)
class SST_Entry:
    """SST binary file single record (SSU8 or SSU9)."""

    edid: str  # 8-char EDID, \0 stripped
    form_id: int  # in-game string ID (FormID)
    text: str  # primary string (UTF-16LE decoded)
    index: int = 0  # global sequence number (SSU8 only)
    trail_hash: bytes = field(default_factory=bytes)  # trailing hash (SSU8 only)
    extra: int = 0  # extra ID (SSU8 only)
    f2: int = 0  # secondary field (SSU9)
    translated_text: str = ""  # translated string (SSU9 bilingual)


class SST_Parser:
    """XT SST binary file parser.

    Parses xTranslator-exported SST (Skyrim Strings Table) binary files.
    Supports both SSU8 and SSU9 formats.  Interface style matches XT_XmlParser.
    """

    def __init__(self, entries: list[SST_Entry]) -> None:
        self.entries = entries

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_file(cls, sst_path: str) -> "SST_Parser":
        """Factory: parse an SST file (SSU8 or SSU9) into an SST_Parser instance.

        Raises ValueError if magic is not recognised.
        """
        data = Path(sst_path).read_bytes()
        if len(data) < 4:
            raise ValueError(f"File too small: {sst_path}")

        magic = data[:4]
        if magic == b"SSU8":
            return cls._parse_ssu8(data, sst_path)
        if magic == b"SSU9":
            return cls._parse_ssu9(data, sst_path)

        raise ValueError(f"Not a valid SST file (magic={magic!r}): {sst_path}")

    # ── SSU8 parser ──

    @classmethod
    def _parse_ssu8(cls, data: bytes, path: str) -> "SST_Parser":
        pos = 16
        entries: list[SST_Entry] = []

        while pos < len(data):
            if pos + 24 > len(data):
                break

            _rec_type = struct.unpack_from("<H", data, pos)[0]
            pos += 2

            edid = data[pos : pos + 8].decode("ascii", errors="replace").rstrip("\0")
            pos += 8

            _field_a = struct.unpack_from("<I", data, pos)[0]
            pos += 4

            form_id = struct.unpack_from("<I", data, pos)[0]
            pos += 4

            pos += 2  # separator

            str_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4

            if pos + str_len > len(data):
                logger.warning("Truncated entry: string data exceeds file end at offset %d", pos)
                break

            raw_str = data[pos : pos + str_len]
            pos += str_len

            try:
                text = raw_str.decode("utf-16-le")
            except UnicodeDecodeError:
                logger.warning("UTF-16LE decode failed at offset %d, skipping entry", pos - str_len)
                continue

            if pos + 4 > len(data):
                logger.warning("Truncated entry: missing trailing length at offset %d", pos)
                break
            trail_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4

            if trail_len > 100 or pos + trail_len > len(data):
                logger.warning("Truncated entry: bad trailing length %d at offset %d", trail_len, pos - 4)
                break

            trail_hash = data[pos : pos + trail_len]
            pos += trail_len

            if pos + 7 > len(data):
                logger.warning("Truncated entry: missing tail fields at offset %d", pos)
                break
            pos += 1  # separator
            seq = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            extra = struct.unpack_from("<H", data, pos)[0]
            pos += 2

            entries.append(SST_Entry(
                edid=edid, form_id=form_id, text=text,
                index=seq, trail_hash=trail_hash, extra=extra,
            ))

        return cls(entries=entries)

    # ── SSU9 parser ──

    @classmethod
    def _parse_ssu9(cls, data: bytes, path: str) -> "SST_Parser":
        name_len = int.from_bytes(data[8:10], "big")
        start = 12 + name_len + 2 + 8  # header + plugin name + null + metadata

        # Phase 1: scan for record starts by pattern matching
        record_starts: list[tuple[int, str, int, int, int]] = []
        pos = start

        while pos < len(data) - 30:
            try:
                edid = data[pos + 4 : pos + 12].decode("ascii")
            except (UnicodeDecodeError, IndexError):
                pos += 1
                continue
            if not edid.endswith(_VALID_EDID_SUFFIXES) or not edid.isupper():
                pos += 1
                continue

            str_idx = struct.unpack_from("<H", data, pos + 20)[0]
            str_len = struct.unpack_from("<H", data, pos + 22)[0]
            if str_idx != 256 or str_len == 0 or str_len > 100000:
                pos += 1
                continue

            str_start = pos + 26
            if str_start + str_len > len(data):
                pos += 1
                continue
            try:
                data[str_start : str_start + min(str_len, 40)].decode("utf-16-le")
            except (UnicodeDecodeError, IndexError):
                pos += 1
                continue

            form_id = struct.unpack_from("<I", data, pos)[0]
            f2 = struct.unpack_from("<I", data, pos + 16)[0]
            record_starts.append((pos, edid, form_id, f2, str_len))
            pos += 26 + str_len

        # Phase 2: extract records
        entries: list[SST_Entry] = []
        for i, (off, edid, form_id, f2, eng_len) in enumerate(record_starts):
            eng_start = off + 26
            eng_text = data[eng_start : eng_start + eng_len].decode("utf-16-le")

            next_off = record_starts[i + 1][0] if i + 1 < len(record_starts) else len(data)
            after_eng = eng_start + eng_len
            tail = data[after_eng:next_off]

            # Extract translated string from tail (4-byte LE length prefix + UTF-16LE)
            chn_text = ""
            if len(tail) >= 4:
                chn_len = struct.unpack_from("<I", tail, 0)[0]
                if 0 < chn_len < len(tail) - 4:
                    try:
                        chn_text = tail[4 : 4 + chn_len].decode("utf-16-le")
                    except UnicodeDecodeError:
                        pass

            entries.append(SST_Entry(
                edid=edid, form_id=form_id, text=eng_text,
                f2=f2, translated_text=chn_text,
            ))

        return cls(entries=entries)

    # ── export ──

    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        data = {
            "entries": [
                {
                    **{k: v for k, v in asdict(e).items() if k not in ("trail_hash",)},
                    "trail_hash": e.trail_hash.hex() if e.trail_hash else "",
                    "translated_text": e.translated_text,
                }
                for e in self.entries
            ]
        }
        return json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)

    def to_json_file(self, path: str, ensure_ascii: bool = False, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(ensure_ascii=ensure_ascii, indent=indent))

    def to_csv_file(self, path: str) -> None:
        fieldnames = ["edid", "form_id", "text", "translated_text", "index", "f2", "extra"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for e in self.entries:
                writer.writerow({
                    "edid": e.edid,
                    "form_id": f"0x{e.form_id:08X}",
                    "text": e.text,
                    "translated_text": e.translated_text,
                    "index": e.index,
                    "f2": f"0x{e.f2:08X}" if e.f2 else "",
                    "extra": f"0x{e.extra:04X}" if e.extra else "",
                })
