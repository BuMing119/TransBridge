# parser/xt/sst_serializer.py — SST binary serializer (SSU9 write-back)
"""基于 SST_Parser 解析结果，模板重建 SSU9 格式 SST 二进制文件。

不增删记录，不修改 header，保留 extra/subrecords 原样。仅支持 SSU9。
"""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sst_parser import SST_Parser, SST_Entry


class SST_Serializer:
    """SSU9 二进制序列化器：from_parser → to_bytes → save。

    仅支持 SSU9 格式（SSU8 的 tail 格式未完全理解）。
    """

    def __init__(self, magic: bytes, header: bytes,
                 records: list[tuple["SST_Entry", bytes]],
                 trailing: bytes = b"") -> None:
        self._magic = magic
        self._header = header
        self._records = records  # [(entry, tail_bytes), ...]
        self._trailing = trailing  # 文件末尾未解析的残余字节

    # ── 工厂方法 ─────────────────────────────────────────────────

    @classmethod
    def from_parser(cls, sst: "SST_Parser") -> "SST_Serializer":
        """从 SST_Parser 实例创建序列化器。

        支持 SSU8 和 SSU9。SST_Parser 必须是通过 from_file() 解析的。
        """
        if not sst._magic:
            raise ValueError("SST_Parser 缺少原始二进制数据，请用 from_file() 解析")

        records: list[tuple["SST_Entry", bytes]] = []
        for entry in sst.entries:
            if not entry._raw:
                raise ValueError(
                    "SST_Entry 缺少原始二进制数据 (_raw)，请用 SST_Parser.from_file() 解析"
                )
            records.append((entry, entry._tail))

        return cls(sst._magic, sst._raw_header, records,
                   trailing=getattr(sst, '_trailing', b''))

    # ── 核心序列化 ─────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        """重建完整 SST 二进制（header + 逐条记录 + 尾部残余）。"""
        rebuild = self._rebuild_ssu8 if self._magic == b"SSU8" else self._rebuild_ssu9
        parts = [self._header]
        for entry, tail in self._records:
            parts.append(rebuild(entry, tail))
        if self._trailing:
            parts.append(self._trailing)
        return b"".join(parts)

    # ── SSU9 record rebuild ────────────────────────────────────

    @staticmethod
    def _rebuild_ssu9(entry: "SST_Entry", tail: bytes) -> bytes:
        """SSU9: 26B 头 + eng_text UTF-16LE + chn_len(4B) + chn_text + extra."""
        head = bytearray(entry._raw[:26])
        eng_bytes = entry.text.encode("utf-16-le")
        struct.pack_into("<H", head, 22, len(eng_bytes))

        result = bytes(head) + eng_bytes

        chn_bytes = entry.translated_text.encode("utf-16-le") if entry.translated_text else b""
        result += struct.pack("<I", len(chn_bytes))
        result += chn_bytes

        # 原样追加 extra/subrecords（跳过原 chn_len + chn_text）
        if len(tail) >= 4:
            orig_chn_len = struct.unpack_from("<I", tail, 0)[0]
            if orig_chn_len <= 100000:
                extra_start = 4 + orig_chn_len
                if extra_start <= len(tail):
                    result += tail[extra_start:]

        return bytes(result)

    # ── SSU8 record rebuild ────────────────────────────────────

    @staticmethod
    def _rebuild_ssu8(entry: "SST_Entry", tail: bytes) -> bytes:
        """SSU8: 24B 头 + eng_text UTF-16LE + trail_len(4B) + trail_data + tail_fields.

        _raw = 24B head + eng_text (str_len bytes).
        _tail = trail_len(4B) + trail_data + sep(1B) + global_seq(4B) + extra(2B).
        """
        # 1. 复制 24B 头，覆盖 str_len（SSU8 的 str_len 是 4B LE 在 offset 20）
        head = bytearray(entry._raw[:24])
        eng_bytes = entry.text.encode("utf-16-le")
        struct.pack_into("<I", head, 20, len(eng_bytes))  # SSU8: str_len 为 uint32 LE

        result = bytes(head) + eng_bytes

        # 2. trail_len + trail_data (Chinese UTF-16LE) + 原样 tail_fields
        chn_bytes = entry.translated_text.encode("utf-16-le") if entry.translated_text else b""
        result += struct.pack("<I", len(chn_bytes))
        result += chn_bytes

        # 3. 原样追加 sep(1B) + global_seq(4B) + extra(2B)，位于 tail 中 trail_data 之后
        if len(tail) >= 4:
            orig_trail_len = struct.unpack_from("<I", tail, 0)[0]
            tail_fields_start = 4 + orig_trail_len
            if tail_fields_start <= len(tail):
                result += tail[tail_fields_start:]

        return bytes(result)

    # ── 保存与更新 ─────────────────────────────────────────────

    def save(self, path: str | Path, overwrite: bool = False) -> Path:
        """写入文件。overwrite=False 时目标已存在则抛 FileExistsError。"""
        dest = Path(path)
        if dest.exists() and not overwrite:
            raise FileExistsError(f"文件已存在: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(self.to_bytes())
        tmp.replace(dest)
        return dest

    def update_and_save(
        self, form_id: int, translated_text: str,
        path: str | Path, overwrite: bool = False,
    ) -> bool:
        """修改指定 form_id 的第一条记录的 translated_text 后写回。

        返回 True（找到并修改）或 False（未找到）。
        """
        found = False
        new_records: list[tuple["SST_Entry", bytes]] = []
        for entry, tail in self._records:
            if not found and entry.form_id == form_id:
                entry = replace(entry, translated_text=translated_text)
                found = True
            new_records.append((entry, tail))

        if not found:
            return False

        self._records = new_records
        self.save(path, overwrite=overwrite)
        return True

    def update_entries(
        self, updates: list[dict],
        path: str | Path, overwrite: bool = False,
    ) -> dict:
        """批量修改后写回。

        updates: [{form_id: int, translated_text: str, text: str}, ...]
        返回 {matched: int, updated: int, not_found: list[int]}。
        """
        # 构建查找表
        lookup: dict[int, dict] = {}
        for u in updates:
            fid = u.get("form_id")
            if fid is not None:
                lookup[fid] = u

        matched = 0
        updated = 0
        new_records: list[tuple["SST_Entry", bytes]] = []
        for entry, tail in self._records:
            if entry.form_id in lookup:
                matched += 1
                upd = lookup[entry.form_id]
                new_trans = upd.get("translated_text", entry.translated_text)
                new_text = upd.get("text", entry.text)
                if new_trans != entry.translated_text or new_text != entry.text:
                    entry = replace(entry, translated_text=new_trans, text=new_text)
                    updated += 1
            new_records.append((entry, tail))

        not_found = [fid for fid in lookup if not any(
            e.form_id == fid for e, _ in new_records
        )]

        self._records = new_records
        self.save(path, overwrite=overwrite)
        return {"matched": matched, "updated": updated, "not_found": not_found}
