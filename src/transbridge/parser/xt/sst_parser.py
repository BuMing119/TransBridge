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
    "QNAM", "CNAM", "EDID", "MODL", "MODT", "DNAM", "ITXT", "NNAM",
    "RNAM", "SHRT",
)


@dataclass(frozen=True)
class SST_Subrecord:
    """SSU9 nested subrecord in tail (extra data)."""

    form_id: int
    rec: str  # raw 8-char EDID (e.g. DIALFULL), \0 stripped
    unk12: int
    f2: int
    str_idx: int
    texts: tuple[str, ...] = ()  # decoded UTF-16LE text blocks


@dataclass(frozen=True)
class SST_Entry:
    """SST binary file single record (SSU8 or SSU9).

    SSU9 记录结构 (26B 固定头 + 可变尾):
      [form_id 4B LE][edid 8B ASCII][unk12 4B LE][f2 4B LE]
      [str_idx 2B LE][str_len 2B LE][pad 2B 0x0000]
      [eng_text N*2B UTF-16LE]
      [chn_len 4B LE][chn_text M*2B UTF-16LE]
      [extra/subrecords ...]

    SSU9 Header 结构:
      [SSU9 4B][type 2B 0x0600][? 2B 0x0000][name_len 2B big][? 2B 0x0000]
      [name_utf16le name_len B]
      [master_list: 对每个 master: [len 2B big][0x0000 2B][name len B UTF-16LE]]
      [0x0000 0x0000 终止符][? 8B metadata]

    SSU8 记录结构:
      [type 2B][edid 8B][field_a 4B][form_id 4B][str_len 4B LE][str N B UTF-16LE]
      [trail_len 4B LE][trail M B UTF-16LE][? 1B][global_seq 4B LE][extra 2B LE]
    """

    rec: str  # record type, 8-char concatenated (e.g. INFONAM1 → INFO:NAM1), \0 stripped
    form_id: int  # in-game string ID (FormID)
    text: str  # primary string (UTF-16LE decoded)
    index: int = 0  # per-EDID index (SSU8: (field_a & 0xFF) + 1, SSU9: unk12 lo16+1), 1-based
    group_index: int = 0  # parent group index (SSU9: unk12 hi16), DIAL topic# for INFO, quest stage# for QUST, 0 for others
    trail_hash: bytes = field(default_factory=bytes)  # SSU8 translated text raw bytes (UTF-16LE)
    extra: int = 0  # extra ID (SSU8 only)
    global_seq: int = 0  # global sequence number from sID (SSU8 only)
    f2: int = 0  # xTranslator internal record ID — form_id 完美一对一映射，非标准哈希算法，疑似 xTranslator 内部数据库主键
    translated_text: str = ""  # translated string (SSU8 decoded from trail, SSU9 from tail)
    subrecords: tuple[SST_Subrecord, ...] = ()  # SSU9 extra subrecords

    # 序列化用原始二进制 — 保留解析器未完全解码的字段 (unk12/str_idx/pad/extra) 供 SST_Serializer 原样重建
    _raw: bytes = field(default_factory=bytes, repr=False)  # SSU9: 26B head + eng_text
    _tail: bytes = field(default_factory=bytes, repr=False)  # SSU9: chn_len + chn_text + extra


class SST_Parser:
    """XT SST binary file parser.

    Parses xTranslator-exported SST (Skyrim Strings Table) binary files.
    Supports both SSU8 and SSU9 formats.  Interface style matches XT_XmlParser.
    """

    def __init__(self, entries: list[SST_Entry],
                 raw_header: bytes = b"", magic: bytes = b"",
                 trailing: bytes = b"") -> None:
        self.entries = entries
        self._raw_header = raw_header
        self._magic = magic
        self._trailing = trailing  # 文件末尾未解析的残余字节（截断记录等），序列化时原样追加

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
        """SSU8 record: rec_type(2B) + edid(8B) + field_a(4B) + form_id(4B)
           + sep1(2B) + str_len(4B LE) + eng_text(N B UTF-16LE)
           + trail_len(4B LE) + trail_data(M B UTF-16LE, Chinese)
           + sep2(1B) + global_seq(4B LE) + extra(2B LE)

        SSU8 Header (16B, 跨16文件验证):
          [0-3] magic SSU8
          [4-5] 0x0000 — 常量 (16/16)
          [6-9] 0x00000000 — 常量 (16/16)
          [10-11] xTranslator 主版本号 (0x0001=标准, 0x0000/0x0002/0x00BB/0x00C1=其他版本)
          [12-15] 高16位=导出批次ID, 低16位始终0x0000

        记录字段:
          rec_type — 多数文件为0x0500(标准), 其他值按文件而异(wraithguard=0x0AB0等)
          sep1 — 0x0100=可翻译文本(绝大多数), 0x0000=音效/拟声词(如\"(squeal)\")
          sep2 — 0=标准(115), 1/2=特殊类型(ARMO/BOOK/CELL/MESG/QUST/INFO)
          global_seq — xTranslator 全局序号, 导出子集时有缺口
          extra — xTranslator 内部记录ID, 非form_id哈希(同form_id可有不同extra)"""
        pos = 16
        entries: list[SST_Entry] = []

        while pos < len(data):
            iter_start = pos  # for trailing capture on break
            if pos + 24 > len(data):
                break

            rec_start = pos  # for _raw capture

            _rec_type = struct.unpack_from("<H", data, pos)[0]
            pos += 2

            edid = data[pos : pos + 8].decode("ascii", errors="replace").rstrip("\0")
            pos += 8

            field_a = struct.unpack_from("<I", data, pos)[0]
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

            # _raw = head (24B) + eng_text (str_len B), for serializer template
            head_len = 24  # rec_type(2) + edid(8) + field_a(4) + form_id(4) + sep(2) + str_len(4)
            raw_bytes = data[rec_start : rec_start + head_len + str_len]

            if pos + 4 > len(data):
                logger.warning("Truncated entry: missing trailing length at offset %d", pos)
                break
            trail_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4

            if pos + trail_len > len(data) or trail_len > 50000:
                logger.warning("Truncated entry: bad trailing length %d at offset %d", trail_len, pos - 4)
                break

            trail_start = pos
            trail_data = data[pos : pos + trail_len]
            pos += trail_len

            # SSU8 trail data is the Chinese translation (UTF-16LE), not a hash
            ssu8_translated = ""
            try:
                ssu8_translated = trail_data.decode("utf-16-le")
            except UnicodeDecodeError:
                pass

            if pos + 7 > len(data):
                logger.warning("Truncated entry: missing tail fields at offset %d", pos)
                break
            pos += 1  # separator
            global_seq = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            extra = struct.unpack_from("<H", data, pos)[0]
            pos += 2

            # _tail = trail_len(4B) + trail_data + sep(1B) + global_seq(4B) + extra(2B)
            tail_bytes = data[trail_start - 4 : pos]  # include trail_len header

            # Per-EDID index from field_a (low byte, 0-based; +1 = 1-based)
            per_edid_index = (field_a & 0xFF) + 1
            # group_index from field_a upper bytes (analogous to SSU9 unk12 hi16)
            group_idx = field_a >> 8

            entries.append(SST_Entry(
                rec=edid, form_id=form_id, text=text,
                index=per_edid_index, group_index=group_idx,
                global_seq=global_seq, trail_hash=trail_data, extra=extra,
                translated_text=ssu8_translated,
                _raw=raw_bytes, _tail=tail_bytes,
            ))

        trailing = data[iter_start:] if iter_start < len(data) else b""
        return cls(entries=entries, raw_header=data[:16], magic=b"SSU8", trailing=trailing)

    # ── SSU9 parser ──

    @classmethod
    def _parse_ssu9(cls, data: bytes, path: str) -> "SST_Parser":
        # Header: [SSU9 4B][type 2B 0x0600][? 2B][name_len 2B big][? 2B]
        #          [name UTF-16LE name_len B]
        #          [master list: len 2B big + 0x0000 2B + name UTF-16LE ...][0x0000 0x0000 终止]
        #          [? 8B metadata]
        # start 仅用首个 name_len 估算，实际 header 边界见下文 record_starts[0][0]
        name_len = int.from_bytes(data[8:10], "big")
        start = 12 + name_len + 2 + 8

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
            # str_idx 字段（7种值，疑似 bitfield 标记）:
            #   0x0100=标准记录 0x0400=变体 0x0200=稀有变体 0x0000=简单响应
            #   0x4100/0x4400/0x4200 = bit14(0x4000)标记 + 上述值
            # bit14(0x4000) 有 ~39% 误匹配率（中文文本恰好落在 English 偏移），跳过
            if str_idx == 0x4000 or str_len == 0 or str_len > 100000:
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
            unk12 = struct.unpack_from("<I", data, pos + 12)[0]
            record_starts.append((pos, edid, form_id, f2, str_len, unk12))
            pos += 26 + str_len

        # Phase 2: extract records
        entries: list[SST_Entry] = []
        for i, (off, edid, form_id, f2, eng_len, unk12) in enumerate(record_starts):
            eng_start = off + 26
            eng_text = data[eng_start : eng_start + eng_len].decode("utf-16-le")

            next_off = record_starts[i + 1][0] if i + 1 < len(record_starts) else len(data)
            after_eng = eng_start + eng_len
            tail = data[after_eng:next_off]

            # Extract translated string from tail (4-byte LE length prefix + UTF-16LE)
            chn_text = ""
            if len(tail) >= 4:
                chn_len = struct.unpack_from("<I", tail, 0)[0]
                if 0 < chn_len <= len(tail) - 4:
                    try:
                        chn_text = tail[4 : 4 + chn_len].decode("utf-16-le")
                    except UnicodeDecodeError:
                        pass

            # Per-EDID index from unk12 (low 16 bits = REC id, 0-based; +1 = 1-based)
            per_edid_index = (unk12 & 0xFFFF) + 1

            # Parse extra subrecords from tail
            subrecords = cls._parse_ssu9_extra(tail, chn_len)

            entries.append(SST_Entry(
                rec=edid, form_id=form_id, text=eng_text,
                index=per_edid_index, group_index=unk12 >> 16,
                f2=f2, translated_text=chn_text,
                subrecords=subrecords,
                _raw=data[off : off + 26 + eng_len],
                _tail=tail,
            ))

        # 用实际第一条记录位置确定 header 边界（start 可能因多 master 名而不准）
        header_end = record_starts[0][0] if record_starts else start
        return cls(entries=entries, raw_header=data[:header_end], magic=b"SSU9")

    _EXTRA_MARKERS = (b"\x02\x00\x00\x00\x00", b"\x00\x00\x00\x00\x00", b"\x01\x00\x00\x00\x00")

    @classmethod
    def _parse_ssu9_extra(cls, tail: bytes, chn_len: int) -> tuple[SST_Subrecord, ...]:
        """Parse nested subrecords from SSU9 tail (extra data after Chinese text).

        Returns empty tuple if no extra data or tail too short.
        """
        if len(tail) < 4 + chn_len + 5:
            return ()

        extra = tail[4 + chn_len :]
        offset = 0
        subrecords: list[SST_Subrecord] = []

        while offset < len(extra):
            # Skip prefix / separator markers
            if offset + 5 <= len(extra) and extra[offset : offset + 5] in cls._EXTRA_MARKERS:
                offset += 5
                continue

            # Need at least 22 bytes for subrecord header
            if offset + 22 > len(extra):
                break

            try:
                ref_form_id = struct.unpack_from("<I", extra, offset)[0]
                ref_rec = extra[offset + 4 : offset + 12].decode("ascii").rstrip("\0")
            except (UnicodeDecodeError, struct.error):
                offset += 1
                continue

            if not ref_rec.endswith(_VALID_EDID_SUFFIXES) or not ref_rec.isupper():
                offset += 1
                continue

            sub_unk12 = struct.unpack_from("<I", extra, offset + 12)[0]
            sub_f2 = struct.unpack_from("<I", extra, offset + 16)[0]
            sub_str_idx = struct.unpack_from("<H", extra, offset + 20)[0]
            offset += 22

            # Read text blocks
            texts: list[str] = []
            while offset + 4 <= len(extra):
                if offset + 5 <= len(extra) and extra[offset : offset + 5] in cls._EXTRA_MARKERS:
                    break

                try:
                    block_len = struct.unpack_from("<I", extra, offset)[0]
                except struct.error:
                    break

                if block_len == 0 or block_len > len(extra) - offset - 4 or block_len > 1000:
                    break

                block_bytes = extra[offset + 4 : offset + 4 + block_len]
                offset += 4 + block_len

                if block_len % 2 == 0:
                    try:
                        texts.append(block_bytes.decode("utf-16-le"))
                    except UnicodeDecodeError:
                        pass

            subrecords.append(
                SST_Subrecord(
                    form_id=ref_form_id,
                    rec=ref_rec,
                    unk12=sub_unk12,
                    f2=sub_f2,
                    str_idx=sub_str_idx,
                    texts=tuple(texts),
                )
            )

        return tuple(subrecords)

    # ── REC format helper ──

    _REC_SUFFIXES = (
        "FULL", "NAM1", "NAM2", "DATA", "DESC", "NAME", "GOLD", "SNAM",
        "QNAM", "CNAM", "EDID", "MODL", "MODT", "DNAM", "ITXT", "NNAM",
        "RNAM", "SHRT",
    )

    @staticmethod
    def _rec_display(rec: str) -> str:
        """将拼接 REC 还原为冒号格式：QUSTNNAM → QUST:NNAM"""
        suffix = rec[-4:]
        if suffix in SST_Parser._REC_SUFFIXES:
            return f"{rec[:-4]}:{suffix}"
        return rec

    # ── export ──

    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        data = {
            "entries": [
                self._entry_to_dict(e)
                for e in self.entries
            ]
        }
        return json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)

    @staticmethod
    def _entry_to_dict(e: SST_Entry) -> dict:
        d = {
            "rec": SST_Parser._rec_display(e.rec),
            "form_id": f"0x{e.form_id:08X}",
            "text": e.text,
            "translated_text": e.translated_text,
            "index": e.index,
            "global_seq": e.global_seq,
            "f2": f"0x{e.f2:08X}" if e.f2 else "",
        }
        if e.subrecords:
            d["subrecords"] = [
                {
                    "form_id": f"0x{s.form_id:08X}",
                    "rec": SST_Parser._rec_display(s.rec),
                    "unk12": f"0x{s.unk12:08X}",
                    "f2": f"0x{s.f2:08X}",
                    "str_idx": s.str_idx,
                    "texts": list(s.texts),
                }
                for s in e.subrecords
            ]
        return d

    def to_json_file(self, path: str, ensure_ascii: bool = False, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(ensure_ascii=ensure_ascii, indent=indent))

    def to_csv_file(self, path: str) -> None:
        fieldnames = ["rec", "form_id", "text", "translated_text", "index", "global_seq", "f2"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for e in self.entries:
                writer.writerow({
                    "rec": self._rec_display(e.rec),
                    "form_id": f"0x{e.form_id:08X}",
                    "text": e.text,
                    "translated_text": e.translated_text,
                    "index": e.index,
                    "global_seq": e.global_seq,
                    "f2": f"0x{e.f2:08X}" if e.f2 else "",
                    "extra": f"0x{e.extra:04X}" if e.extra else "",
                })
