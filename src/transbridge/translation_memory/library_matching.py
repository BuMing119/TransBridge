# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Record location and indexed matching for the workbench translation libraries."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import NamedTuple

from transbridge.application.io.dsd_projection import DSD_LOCATOR_METADATA
from transbridge.converter.dsd_metadata import dsd_conditions, dsd_quest_index, dsd_region_index
from transbridge.converter.translation_entry import TranslationEntry, _normalize_text

_LOCAL_KEY = re.compile(
    r"(?:(?P<editor>.*):)?(?P<form>[0-9a-f]{1,8})\|(?P<index>\d{1,10})~(?P<shape>[^~]+)(?:~(?P<variant>.+))?", re.I
)
_FORM_ID = re.compile(r"[0-9a-f]{1,8}")
_FIELD = re.compile(r"([A-Z0-9_]{4})(?:[: ]?([A-Z0-9_]{4}))?")


def _encoded(*fields: object) -> str:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


def _record_shape(descriptor: str) -> tuple[str, str]:
    match = _FIELD.fullmatch(descriptor.split("|", 1)[0].strip().upper())
    return (match[1], match[2] or "") if match else ("", "")


def _owned_form_id(form_id: str) -> tuple[str, int] | None:
    digits, _, owner = form_id.casefold().partition("|")
    digits = digits.removeprefix("0x")
    if not owner or "|" in owner or not _FORM_ID.fullmatch(digits):
        return None
    value = int(digits, 16)
    # The leading byte is a master index. Packed light IDs use the low 12 bits.
    return owner, value & (0xFFF if value >> 24 == 0xFE else 0xFFFFFF)


def _plugin_locator(
    form: tuple[str, int],
    record_type: str,
    field: str,
    index: int,
    conditions: tuple[str, ...] = (),
    variant: str = "",
) -> str:
    return _encoded("plugin", *form, record_type, field, index, conditions, variant)


def _local_locator(mod_id: str, key: str) -> str:
    return _encoded("local", mod_id.strip().casefold(), key)


def _plugin_name(mod_id: str) -> str:
    name = mod_id.strip().casefold()
    return name[:-4] if name.endswith((".esp", ".esm", ".esl")) else name


@dataclass(frozen=True, slots=True)
class EntryLocation:
    locator: str
    record_type: str
    field: str
    owner: str = ""
    native_form_id: str = ""
    editor_id: str = ""


def locate_entry(entry: TranslationEntry, mod_id: str) -> EntryLocation:
    key = _LOCAL_KEY.fullmatch(entry.key)
    metadata = dict(entry.metadata)
    persisted = metadata.get(DSD_LOCATOR_METADATA)
    saved_form, saved_field, saved_index, saved_editor = "", "", None, ""
    if isinstance(persisted, (tuple, list)) and len(persisted) == 4:
        form, field, index, editor = persisted
        saved_form = form if isinstance(form, str) else ""
        saved_field = field if isinstance(field, str) else ""
        saved_index = index if type(index) is int else None
        saved_editor = editor if isinstance(editor, str) else ""
    record_type, field = _record_shape(entry.dsd_type or saved_field or entry.context or "")
    if not field and key:
        key_type, key_field = _record_shape(key["shape"])
        record_type, field = key_type or record_type, key_field

    form = entry.form_id_with_plugin or saved_form or metadata.get("plugin.form_id", "")
    form = form if isinstance(form, str) else ""
    editor = entry.editor_id or saved_editor or metadata.get("plugin.editor_id", "")
    editor = editor if isinstance(editor, str) else ""
    editor = editor or ((key["editor"] or "") if key else "")
    owned_form_id = _owned_form_id(form)
    if owned_form_id is None or not field:
        return EntryLocation(
            _local_locator(mod_id, entry.key), record_type, field, native_form_id=form, editor_id=editor
        )

    explicit_index = None
    if (record_type, field) == ("QUST", "CNAM"):
        explicit_index = dsd_quest_index(entry)
    elif (record_type, field) == ("REGN", "RDMP"):
        explicit_index = dsd_region_index(entry)
    index = next(
        number
        for number in (explicit_index, int(key["index"]) if key else None, saved_index, entry.dsd_index)
        if number is not None
    )
    conditions = dsd_conditions(entry) or ()
    variant = (key["variant"] or "") if key else ""
    locator = _plugin_locator(owned_form_id, record_type, field, index, conditions, variant)
    return EntryLocation(locator, record_type, field, owned_form_id[0], form, editor)


class LibraryRecord(NamedTuple):
    locator: str
    qualifier: str
    original: str
    translation: str
    source_mod: str
    source_mod_key: str
    record_type: str
    field: str
    source_key: str
    native_form_id: str
    editor_id: str

    @classmethod
    def from_entry(
        cls,
        entry: TranslationEntry,
        mod_id: str,
        *,
        location: EntryLocation | None = None,
        translation: str | None = None,
    ) -> LibraryRecord:
        if location is None:
            location = locate_entry(entry, mod_id)
        return cls(
            location.locator,
            entry.identity.original or "",
            entry.original,
            entry.translation if translation is None else translation,
            mod_id,
            mod_id.casefold(),
            location.record_type,
            location.field,
            entry.key,
            location.native_form_id,
            location.editor_id,
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    translation: str
    source: str
    main: bool
    order: int


class _ExactIndex:
    def __init__(self) -> None:
        self._winner: dict[tuple[str, str], _Candidate] = {}
        self._by_source: dict[tuple[str, str, str], _Candidate] = {}

    def add(self, locator: str, original: str, candidate: _Candidate) -> None:
        self._winner.setdefault((locator, original), candidate)
        self._by_source.setdefault((locator, original, candidate.source), candidate)

    def candidates(self, locator: str, original: str, source: str, owner: str) -> tuple[_Candidate | None, ...]:
        return (
            self._winner.get((locator, original)),
            self._by_source.get((locator, original, source)),
            self._by_source.get((locator, original, owner)),
        )


class LibraryMatcher:
    """Keep the first candidate in each tier of a fixed library snapshot."""

    def __init__(self) -> None:
        self._exact = _ExactIndex()
        self._raw = _ExactIndex()
        self._text: dict[tuple[str, ...], str] = {}
        self._order = 0

    def add(self, record: LibraryRecord, *, main: bool = False) -> None:
        candidate = _Candidate(record.translation, _plugin_name(record.source_mod), main, self._order)
        self._add_exact(record.locator, record.original, candidate, qualified=bool(record.qualifier))
        if not record.qualifier:
            self._add_text(_normalize_text(record.original), record.record_type, record.field, record.translation)
        self._order += 1

    def lookup(self, entry: TranslationEntry, mod_id: str) -> tuple[str, str]:
        location = locate_entry(entry, mod_id)
        source, owner = _plugin_name(mod_id), _plugin_name(location.owner)
        raw = self._raw.candidates(location.locator, entry.original, source, owner)
        if entry.requires_original_match:
            return self._best_exact(source, owner, *raw)

        original = _normalize_text(entry.original)
        if not original:
            return "", ""
        exact = self._exact.candidates(location.locator, original, source, owner)
        local = (
            self._exact.candidates(_local_locator(mod_id, entry.key), original, source, owner) if location.owner else ()
        )
        matched = self._best_exact(source, owner, *raw, *exact, *local)
        if matched[0]:
            return matched
        translation = (
            self._text.get((original, location.record_type, location.field))
            or self._text.get((original, location.record_type))
            or self._text.get((original,))
        )
        return (translation, "text") if translation else ("", "")

    def _add_exact(self, locator: str, original: str, candidate: _Candidate, *, qualified: bool) -> None:
        self._raw.add(locator, original, candidate)
        if not qualified:
            self._exact.add(locator, _normalize_text(original), candidate)

    def _add_text(self, original: str, record_type: str, field: str, translation: str) -> None:
        if not original:
            return
        self._text.setdefault((original,), translation)
        if record_type:
            self._text.setdefault((original, record_type), translation)
            if field:
                self._text.setdefault((original, record_type, field), translation)

    @staticmethod
    def _best_exact(source: str, owner: str, *candidates: _Candidate | None) -> tuple[str, str]:
        winner = min(
            (item for item in candidates if item is not None),
            key=lambda item: (item.main, item.source != source, item.source != owner, item.order),
            default=None,
        )
        return (winner.translation, "key") if winner else ("", "")
