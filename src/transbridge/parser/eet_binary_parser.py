"""EET binary query facade backed by the strict current-format codec.

EET v3 Classique and BDD files are supported by this parser. Product I/O remains
unregistered pending application validation; use scripts.eet_lab for experiments.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from .eet_binary_codec import EetDocument
from .eet_parser import EET_Entry


class EET_BinaryParser:
    """Expose XML-compatible entries while retaining the complete binary document."""

    def __init__(
        self,
        entries: list[EET_Entry],
        game_data: int | str = 0,
        phrases: bytes = b"",
        *,
        document: EetDocument | None = None,
    ) -> None:
        self.entries = entries
        self.game_data = game_data
        self.phrases = phrases
        self.document = document
        self._by_key: dict[tuple[str, str, str, str], list[EET_Entry]] = {}
        self._by_grup: dict[str, list[EET_Entry]] = {}
        self._by_id: dict[str, list[EET_Entry]] = {}
        self._by_edid: dict[str, list[EET_Entry]] = {}
        for entry in entries:
            self._by_key.setdefault(entry.key, []).append(entry)
            self._by_grup.setdefault(entry.grup, []).append(entry)
            self._by_id.setdefault(entry.id, []).append(entry)
            if entry.edid:
                self._by_edid.setdefault(entry.edid, []).append(entry)

    @classmethod
    def from_file(cls, path: str | Path) -> EET_BinaryParser:
        return cls.from_bytes(Path(path).read_bytes())

    @classmethod
    def from_bytes(cls, content: bytes) -> EET_BinaryParser:
        return cls.from_document(EetDocument.from_bytes(content))

    @classmethod
    def _parse_binary(cls, data: bytes) -> EET_BinaryParser:
        return cls.from_bytes(data)

    @classmethod
    def from_document(cls, document: EetDocument) -> EET_BinaryParser:
        entries = [
            EET_Entry(
                grup=record.group,
                id=record.form_id,
                edid=record.editor_id,
                champ=record.field,
                original=record.original,
                traduit=record.translation,
                perso=record.personal,
                index=record.index,
                status=record.status,
                idstexte=record.text_id,
                commentaire=record.comment,
                icon=record.icon,
            )
            for record in document.records
        ]
        return cls(entries, document.game, document.phrase_payload() or b"", document=document)

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
