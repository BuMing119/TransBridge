"""Lossless SST creation and updates with explicit record identity."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile

from .sst_codec import SstDocument, SstRecord
from .sst_parser import SST_Entry, SST_Parser


class SST_Serializer:
    """Serialize a complete document without losing header or record metadata."""

    def __init__(self, document: SstDocument) -> None:
        document.to_bytes()
        self.document = document

    @classmethod
    def from_document(cls, document: SstDocument) -> SST_Serializer:
        return cls(document)

    @classmethod
    def from_parser(cls, sst: SST_Parser) -> SST_Serializer:
        if sst.document is None or sst._trailing:
            raise ValueError("SST serialization requires a complete parsed document")
        if len(sst.entries) != len(sst.document.records) or any(
            entry.ordinal != i for i, entry in enumerate(sst.entries)
        ):
            raise ValueError("SST template entries were added, removed, or reordered; use create_new explicitly")
        return cls(replace(sst.document, records=tuple(entry.to_record() for entry in sst.entries)))

    @classmethod
    def create_new(
        cls,
        plugin_name: str,
        entries: list[SST_Entry | SstRecord],
        *,
        magic: bytes = b"SSU9",
        masters: tuple[str, ...] | None = None,
        labels: tuple[tuple[int, str], ...] = (),
        flag: int = 0,
    ) -> SST_Serializer:
        """Create a complete file; use SstRecord entries for explicit metadata.

        A supplied master table is used verbatim. Otherwise SSU9 uses the plugin
        name as its sole master; SSU8 has no master table. No hash is guessed.
        """
        if masters is None:
            masters = (plugin_name,) if magic == b"SSU9" and plugin_name else ()
        records = tuple(entry.to_record() if isinstance(entry, SST_Entry) else entry for entry in entries)
        return cls(SstDocument(magic, flag, tuple(masters), tuple(labels), records))

    def to_bytes(self) -> bytes:
        return self.document.to_bytes()

    @staticmethod
    def _save_document(document: SstDocument, path: str | Path, overwrite: bool) -> Path:
        data = document.to_bytes()
        dest = Path(path)
        if dest.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent, delete=False
            ) as out:
                temporary = Path(out.name)
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            if overwrite:
                os.replace(temporary, dest)
            elif os.name == "nt":
                # Windows rename is exclusive and also works on volumes without hard links.
                os.rename(temporary, dest)
            else:
                # Exclusive publication: a racing writer can never be overwritten.
                os.link(temporary, dest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return dest

    def save(self, path: str | Path, overwrite: bool = False) -> Path:
        return self._save_document(self.document, path, overwrite)

    def update_record(self, ordinal: int, *, text: str | None = None, translated_text: str | None = None) -> bool:
        """Update one physical record in memory. Empty strings are real edits."""
        if type(ordinal) is not int or not 0 <= ordinal < len(self.document.records):
            raise ValueError(f"SST record ordinal is out of range: {ordinal!r}")
        records = list(self.document.records)
        original = records[ordinal]
        records[ordinal] = replace(
            original,
            original=original.original if text is None else text,
            translation=original.translation if translated_text is None else translated_text,
        )
        candidate = replace(self.document, records=tuple(records))
        candidate.to_bytes()
        self.document = candidate
        return records[ordinal] != original

    def _match(self, update: dict) -> int | None:
        if unknown := set(update) - {"ordinal", "form_id", "rec", "index", "text", "translated_text"}:
            raise ValueError(f"Unknown SST update fields: {sorted(unknown)}")
        if "ordinal" not in update and "form_id" not in update:
            raise ValueError("SST update requires ordinal or form_id")
        for key, lower, upper in (("form_id", 0, 0xFFFFFFFF), ("index", 1, 65536)):
            if key in update and (type(update[key]) is not int or not lower <= update[key] <= upper):
                raise ValueError(f"Invalid SST {key} selector: {update[key]!r}")
        if "rec" in update and (not isinstance(update["rec"], str) or len(update["rec"]) != 8):
            raise ValueError("SST rec selector must contain exactly eight characters")
        if "ordinal" in update:
            ordinal = update["ordinal"]
            if type(ordinal) is not int or not 0 <= ordinal < len(self.document.records):
                raise ValueError(f"SST record ordinal is out of range: {ordinal!r}")
            candidates = [ordinal]
        else:
            if ("rec" in update or "index" in update) and not {"rec", "index"} <= update.keys():
                raise ValueError("SST composite identity requires rec, form_id, and one-based index")
            candidates = range(len(self.document.records))
        matches = []
        for i in candidates:
            record = self.document.records[i]
            expected = {
                "form_id": record.form_id,
                "rec": record.record_type + record.field_type,
                "index": record.index + 1,
            }
            if all(key not in update or update[key] == value for key, value in expected.items()):
                matches.append(i)
        if len(matches) > 1:
            raise ValueError("Ambiguous SST identity; specify physical ordinal or unique rec/form_id/index")
        return matches[0] if matches else None

    def update_and_save(
        self,
        form_id: int,
        translated_text: str,
        path: str | Path,
        overwrite: bool = False,
        *,
        ordinal: int | None = None,
        rec: str | None = None,
        index: int | None = None,
    ) -> bool:
        """Update one uniquely identified record; form_id ambiguity is an error."""
        update = {"form_id": form_id, "translated_text": translated_text}
        update.update({
            key: value for key, value in {"ordinal": ordinal, "rec": rec, "index": index}.items() if value is not None
        })
        if self._match(update) is None:
            return False
        self.update_entries([update], path, overwrite)
        return True

    def update_entries(self, updates: list[dict], path: str | Path, overwrite: bool = False) -> dict:
        """Validate all identities before saving or mutating the in-memory file."""
        records = list(self.document.records)
        matched = updated = 0
        not_found = []
        seen = set()
        for update in updates:
            ordinal = self._match(update)
            if ordinal is None:
                not_found.append(update.get("form_id", update.get("ordinal")))
                continue
            if ordinal in seen:
                raise ValueError(f"Duplicate SST update for physical ordinal {ordinal}")
            seen.add(ordinal)
            matched += 1
            original = records[ordinal]
            records[ordinal] = replace(
                original,
                original=update.get("text", original.original),
                translation=update.get("translated_text", original.translation),
            )
            updated += records[ordinal] != original
        candidate = replace(self.document, records=tuple(records))
        self._save_document(candidate, path, overwrite)
        self.document = candidate
        return {"matched": matched, "updated": updated, "not_found": not_found}
