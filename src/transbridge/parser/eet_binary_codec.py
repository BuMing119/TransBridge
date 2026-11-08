"""Strict EET v3 wire codec, verified against samples and EET4 reflection-only IL.

Shared by the experimental binary EET application adapter and diagnostic CLI.
Hashes are retained; changed translations regenerate numeric caches. See docs/dev/eet-experiment.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .eet_binary_wire import EetFormatError as EetFormatError, _blob, _number, _Reader, _signed, _string
from .eet_number_cache import EetNumberCache, EetNumberOptions, build_number_cache, parse_number_cache


@dataclass(frozen=True)
class EetRecord:
    group: str
    form_id: str
    editor_id: str
    field: str
    original: str
    translation: str
    personal: str
    index: int
    status: int
    text_id: int
    comment: str
    original_hash: int
    normalized_original: str
    normalized_hash: int
    original_variables: bytes = b""
    translation_variables: bytes = b""
    icon: int | None = -1

    @property
    def original_number_cache(self) -> EetNumberCache | None:
        return parse_number_cache(self.original_variables)

    @property
    def translation_number_cache(self) -> EetNumberCache | None:
        return parse_number_cache(self.translation_variables)

    def to_bytes(self) -> bytes:
        payload = b"".join(
            _string(value)
            for value in (
                self.group,
                self.form_id,
                self.editor_id,
                self.field,
                self.original,
                self.translation,
                self.personal,
            )
        )
        payload += _signed(self.index, 4) + _signed(self.status, 2) + _signed(self.text_id, 4)
        payload += _string(self.comment) + _number(self.original_hash, 4)
        payload += _string(self.normalized_original) + _number(self.normalized_hash, 4)
        for variables in (self.original_variables, self.translation_variables):
            parse_number_cache(variables)
            payload += _blob(variables)
        if self.icon is not None:
            payload += _signed(self.icon, 4)
        return _number(len(payload), 4) + payload


@dataclass(frozen=True)
class EetDocument:
    version: int
    game: str
    records: tuple[EetRecord, ...]
    phrases: tuple[tuple[str, str], ...] | None = ()
    type_dt: int = 2

    @classmethod
    def from_bytes(cls, content: bytes) -> EetDocument:
        reader = _Reader(content)
        reader.expect(b"EET_")
        type_dt = reader.signed(4)
        if type_dt not in (1, 2):
            raise EetFormatError(f"Unsupported EET file type {type_dt}; only Classique and BDD are verified")
        version = reader.number(4)
        if version != 3:
            raise EetFormatError(f"Unsupported EET version {version}; only v3 is verified")
        reader.expect(b"GAME")
        game = reader.string(2)
        reader.expect(b"LINE")
        count = reader.number(4)
        if count > reader.remaining // 66:
            raise EetFormatError("LINE count exceeds remaining record data")
        records = []
        for ordinal in range(count):
            start = reader.position
            size = reader.number(4)
            if size < 62:
                raise EetFormatError(f"Record {ordinal} at {start}: invalid size {size}")
            record_reader = _Reader(reader.take(size), origin=start + 4)
            try:
                strings = tuple(record_reader.string() for _ in range(7))
                record = EetRecord(
                    *strings,
                    index=record_reader.signed(4),
                    status=record_reader.signed(2),
                    text_id=record_reader.signed(4),
                    comment=record_reader.string(),
                    original_hash=record_reader.number(4),
                    normalized_original=record_reader.string(),
                    normalized_hash=record_reader.number(4),
                    original_variables=record_reader.blob(),
                    translation_variables=record_reader.blob(),
                    icon=record_reader.signed(4) if record_reader.remaining else None,
                )
                parse_number_cache(record.original_variables)
                parse_number_cache(record.translation_variables)
                record_reader.finish()
            except EetFormatError as exc:
                raise EetFormatError(f"Record {ordinal} at {start}: {exc}") from exc
            records.append(record)
        phrases = None
        if reader.remaining:
            reader.expect(b"PHRA")
            phrases_reader = _Reader(reader.blob())
            phrase_count = phrases_reader.number(4)
            if phrase_count > phrases_reader.remaining // 4:
                raise EetFormatError("PHRA count exceeds remaining phrase data")
            phrases = tuple((phrases_reader.string(2), phrases_reader.string(2)) for _ in range(phrase_count))
            phrases_reader.finish()
        reader.finish()
        return cls(version, game, tuple(records), phrases, type_dt)

    def to_bytes(self) -> bytes:
        if isinstance(self.version, bool) or self.version != 3:
            raise EetFormatError("Only EET v3 serialization has been verified")
        if isinstance(self.type_dt, bool) or self.type_dt not in (1, 2):
            raise EetFormatError("Only Classique and BDD file types have been verified")
        prefix = b"EET_" + _signed(self.type_dt, 4) + _number(self.version, 4) + b"GAME" + _string(self.game, 2)
        lines = b"".join(record.to_bytes() for record in self.records)
        suffix = b""
        phrases = self.phrase_payload()
        if phrases is not None:
            suffix = b"PHRA" + _blob(phrases)
        return prefix + b"LINE" + _number(len(self.records), 4) + lines + suffix

    def phrase_payload(self) -> bytes | None:
        if self.phrases is None:
            return None
        return _number(len(self.phrases), 4) + b"".join(
            _string(left, 2) + _string(right, 2) for left, right in self.phrases
        )

    def with_translation(
        self, ordinal: int, translation: str, *, number_options: EetNumberOptions = EetNumberOptions()
    ) -> EetDocument:
        """Patch a translation and rebuild its cache; preserve original-side data."""
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or not 0 <= ordinal < len(self.records):
            raise ValueError("Record ordinal is out of range")
        _string(translation)
        record = self.records[ordinal]
        if record.translation == translation:
            return self
        cache = build_number_cache(translation, group=record.group, field=record.field, options=number_options)
        records = list(self.records)
        records[ordinal] = replace(
            record, translation=translation, translation_variables=cache.to_payload() if cache else b""
        )
        return replace(self, records=tuple(records))
