# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Public xTranslator SST reader backed by the complete sequential codec."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field, replace
import json
from pathlib import Path

from .sst_codec import SstDocument, SstRecord, sst_hash

sst_string_hash = sst_hash


@dataclass(frozen=True)
class SST_Subrecord:
    """Legacy compatibility type. Real SST records are flat, never nested."""

    form_id: int
    rec: str
    unk12: int
    f2: int
    str_idx: int
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SST_Entry:
    """Compatibility projection of one complete SST record.

    ``index`` is one-based for existing TranslationEntry callers. ``record``
    retains exact on-disk metadata and its zero-based index. Historical names
    ``group_index``, ``global_seq`` and ``f2`` mean index_max, string_id and
    edid_hash respectively; none denotes a nested group or a generated ID.
    """

    rec: str
    form_id: int
    text: str
    index: int = 1
    group_index: int = 0
    trail_hash: bytes = field(default_factory=bytes)
    extra: int = 0
    global_seq: int = 0
    f2: int = 0
    translated_text: str = ""
    subrecords: tuple[SST_Subrecord, ...] = ()
    _raw: bytes = field(default_factory=bytes, repr=False)
    _tail: bytes = field(default_factory=bytes, repr=False)
    record: SstRecord | None = field(default=None, repr=False)
    ordinal: int = -1

    @classmethod
    def from_record(cls, record: SstRecord, ordinal: int) -> SST_Entry:
        return cls(
            rec=record.record_type + record.field_type,
            form_id=record.form_id,
            text=record.original,
            index=record.index + 1,
            group_index=record.index_max,
            global_seq=record.string_id,
            f2=record.edid_hash,
            translated_text=record.translation,
            record=record,
            ordinal=ordinal,
        )

    def to_record(self) -> SstRecord:
        """Apply facade edits while retaining every other original wire field."""
        if len(self.rec) != 8:
            raise ValueError("SST rec must contain a four-byte record and four-byte field signature")
        if self.subrecords:
            raise ValueError("SST records cannot contain nested subrecords")
        values = {
            "form_id": self.form_id,
            "record_type": self.rec[:4],
            "field_type": self.rec[4:],
            "index": self.index - 1,
            "index_max": self.group_index,
            "string_id": self.global_seq,
            "edid_hash": self.f2,
            "original": self.text,
            "translation": self.translated_text,
        }
        if self.record is not None:
            return replace(self.record, **values)
        return SstRecord(list_index=0, collab_id=0, params=int(bool(self.translated_text)), **values)


class SST_Parser:
    """Strict SSU8/SSU9 reader; malformed input never returns partial success."""

    def __init__(
        self,
        entries: list[SST_Entry],
        raw_header: bytes = b"",
        magic: bytes = b"",
        trailing: bytes = b"",
        *,
        document: SstDocument | None = None,
    ) -> None:
        self.entries = entries
        self.document = document
        self._raw_header = raw_header
        self._magic = magic
        self._trailing = trailing

    def __len__(self) -> int:
        return len(self.entries)

    @classmethod
    def from_file(cls, sst_path: str | Path) -> SST_Parser:
        return cls.from_bytes(Path(sst_path).read_bytes(), source_name=str(sst_path))

    @classmethod
    def from_bytes(cls, data: bytes, *, source_name: str = "<memory>") -> SST_Parser:
        """Read the same captured bytes used by immutable source snapshots."""
        try:
            document = SstDocument.from_bytes(data)
        except ValueError as exc:
            raise ValueError(f"Cannot parse SST {source_name}: {exc}") from exc
        return cls.from_document(document)

    @classmethod
    def from_document(cls, document: SstDocument) -> SST_Parser:
        document.to_bytes()
        return cls(
            [SST_Entry.from_record(record, i) for i, record in enumerate(document.records)],
            raw_header=replace(document, records=()).to_bytes(),
            magic=document.magic,
            document=document,
        )

    @staticmethod
    def _rec_display(rec: str) -> str:
        return f"{rec[:4]}:{rec[4:]}" if len(rec) == 8 else rec

    @staticmethod
    def _entry_to_dict(entry: SST_Entry) -> dict:
        result = {
            "rec": SST_Parser._rec_display(entry.rec),
            "form_id": f"0x{entry.form_id:08X}",
            "text": entry.text,
            "translated_text": entry.translated_text,
            "index": entry.index,
            "global_seq": entry.global_seq,
            "f2": f"0x{entry.f2:08X}" if entry.f2 else "",
            "ordinal": entry.ordinal,
            "index_max": entry.group_index,
        }
        if entry.record is not None:
            result.update(
                list_index=entry.record.list_index,
                string_id=entry.record.string_id,
                edid_hash=f"0x{entry.record.edid_hash:08X}",
                collab_id=entry.record.collab_id,
                params=entry.record.params,
            )
        return result

    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        return json.dumps(
            {"entries": [self._entry_to_dict(entry) for entry in self.entries]},
            ensure_ascii=ensure_ascii,
            indent=indent,
        )

    def to_json_file(self, path: str | Path, ensure_ascii: bool = False, indent: int = 2) -> None:
        Path(path).write_text(self.to_json(ensure_ascii=ensure_ascii, indent=indent), encoding="utf-8")

    def to_csv_file(self, path: str | Path) -> None:
        fieldnames = ["rec", "form_id", "text", "translated_text", "index", "global_seq", "f2", "ordinal"]
        with Path(path).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self._entry_to_dict(entry) for entry in self.entries)
