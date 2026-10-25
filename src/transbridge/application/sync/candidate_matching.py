"""Bounded source matching and download content consensus, without invented IDs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
import re

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import Stage

from .download_review import DownloadCandidate
from .models import EntrySummary, LocalEntrySnapshot, RemoteEntrySnapshot, canonical_hash

_JOURNAL_KEY = re.compile(r"(?P<record>[^|]+:[0-9A-Fa-f]{8})\|[0-9]+~QUST:CNAM\Z")
_CONTENT_REVISION = "equivalent-content:"
_TRANSLATION_REVISION = "translation-content:"


def journal_record(key: EntryKey) -> tuple[object, str] | None:
    """An ordinal is not stable; the exact quest record and field are the boundary."""

    match = _JOURNAL_KEY.fullmatch(key.local_key)
    return None if match is None else (key.namespace, match["record"])


class JournalOriginalIndex:
    def __init__(self, entries: tuple[RemoteEntrySnapshot, ...]) -> None:
        self._entries = defaultdict(list)
        for entry in entries:
            record = journal_record(entry.entry_key)
            if record is not None and not entry.deleted:
                self._entries[record, entry.original].append(entry)

    def alternatives(
        self, current: LocalEntrySnapshot, same_key: list[RemoteEntrySnapshot]
    ) -> tuple[RemoteEntrySnapshot, ...]:
        if any(entry.original == current.original for entry in same_key):
            return ()
        record = journal_record(current.entry_key)
        if record is None or not current.original:
            return ()
        return tuple(self._entries.get((record, current.original), ()))


class OriginalContentIndex:
    """Exact source text within one source namespace; no key or cloud-ID ownership."""

    def __init__(
        self, entries: tuple[RemoteEntrySnapshot, ...], *, order_key: Callable[[RemoteEntrySnapshot], str]
    ) -> None:
        groups = defaultdict(list)
        for entry in entries:
            if entry.original and not entry.deleted:
                groups[entry.entry_key.namespace, entry.original].append(entry)
        self._groups = {
            key: _OriginalContentGroup(tuple(sorted(set(entries), key=order_key))) for key, entries in groups.items()
        }
        empty_groups = defaultdict(list)
        for entry in entries:
            if not entry.original and not entry.deleted:
                empty_groups[entry.entry_key.namespace, entry.entry_key.local_key].append(entry)
        self._empty_groups = {
            key: _OriginalContentGroup(tuple(sorted(set(entries), key=order_key)))
            for key, entries in empty_groups.items()
        }
        self._invalid_keys = {
            (entry.entry_key.namespace, entry.entry_key.local_key)
            for entry in entries
            if not consistent_original_identity(entry)
        }
        self._fallback = {}
        self._order_key = order_key

    def _group(self, current):
        if not current.original:
            return self._empty_groups.get((current.entry_key.namespace, current.entry_key.local_key))
        return self._groups.get((current.entry_key.namespace, current.original))

    def candidates(
        self, current: LocalEntrySnapshot, same_key: list[RemoteEntrySnapshot]
    ) -> tuple[RemoteEntrySnapshot, ...]:
        group = self._group(current)
        if group is not None:
            return group.entries
        key = current.entry_key.namespace, current.entry_key.local_key
        if key not in self._fallback:
            self._fallback[key] = tuple(sorted(set(same_key), key=self._order_key))
        return self._fallback[key]

    def match(self, current: LocalEntrySnapshot) -> tuple[RemoteEntrySnapshot | None, str, bool]:
        if not consistent_original_identity(current):
            return None, "original_mismatch", False
        group = self._group(current)
        if group is None:
            key = current.entry_key.namespace, current.entry_key.local_key
            return None, "original_mismatch" if key in self._invalid_keys else "no_matching_original", False
        return group.match(current)


class _OriginalContentGroup:
    """Precompute repeated-original evidence once, including stage agreement."""

    __slots__ = ("_context_translations", "_key_translations", "_missing_reason", "_translations", "entries")

    def __init__(self, entries: tuple[RemoteEntrySnapshot, ...]) -> None:
        self.entries = entries
        valid = tuple(entry for entry in entries if consistent_original_identity(entry))
        translations = defaultdict(list)
        self._key_translations = self._context_translations = None
        for entry in valid:
            if entry.translation:
                translations[entry.translation].append(entry)
        self._translations = {}
        for translation, members in translations.items():
            stages = {entry.stage for entry in members}
            self._translations[translation] = (tuple(members), next(iter(stages)) if len(stages) == 1 else None)
        # A unanimous text needs no per-key evidence. For real ambiguity these
        # maps make key/context/local-translation decisions constant-time too.
        if len(translations) > 1:
            self._key_translations, self._context_translations = defaultdict(set), defaultdict(set)
            for entry in valid:
                if entry.translation:
                    self._key_translations[entry.entry_key.local_key].add(entry.translation)
                    self._context_translations[entry.entry_key.local_key, entry.context].add(entry.translation)
        self._missing_reason = "remote_untranslated" if valid else "original_mismatch"

    def match(self, current):
        if not self._translations:
            return None, self._missing_reason, False
        if len(self._translations) == 1:
            translation = next(iter(self._translations))
            members, _ = self._translations[translation]
            reason = "download_translation_consensus" if len(members) > 1 else "download_original_match"
            if len(members) == 1 and members[0].entry_key.local_key == current.entry_key.local_key:
                reason = "download_key_match"
        else:
            evidence = (
                self._context_translations.get((current.entry_key.local_key, current.context), ()),
                self._key_translations.get(current.entry_key.local_key, ()),
            )
            for matching in evidence:
                if len(matching) == 1:
                    translation, reason = next(iter(matching)), "download_key_match"
                    break
            else:
                if current.translation not in self._translations:
                    return None, "multiple_remote_translations", False
                translation, reason = current.translation, "download_current_translation_match"
        members, agreed_stage = self._translations[translation]
        stage = stage_from_agreement(
            agreed_stage, current_matches=current.translation == translation, current_stage=current.stage
        )
        return translation_source_with_stage(members[0], current.entry_key, stage), reason, True


def translation_content_source(
    entries: tuple[RemoteEntrySnapshot, ...], current: LocalEntrySnapshot
) -> RemoteEntrySnapshot:
    stage = conservative_translation_stage(
        {entry.stage for entry in entries},
        current_matches=current.translation == entries[0].translation,
        current_stage=current.stage,
    )
    return translation_source_with_stage(entries[0], current.entry_key, stage)


def conservative_translation_stage(stages: set[int], *, current_matches: bool, current_stage: int) -> int:
    # Stages are workflow states, not an ordinal confidence scale. Conflicting
    # cloud states cannot manufacture reviewed/locked status for copied text.
    return stage_from_agreement(
        next(iter(stages)) if len(stages) == 1 else None,
        current_matches=current_matches,
        current_stage=current_stage,
    )


def stage_from_agreement(agreed_stage: int | None, *, current_matches: bool, current_stage: int) -> int:
    return agreed_stage if agreed_stage is not None else current_stage if current_matches else Stage.TRANSLATED.value


def translation_source_with_stage(entry: RemoteEntrySnapshot, target: EntryKey, stage: int) -> RemoteEntrySnapshot:
    source = replace(entry, entry_key=target, context="", stage=stage, external_ref=None, context_order=None)
    content = EntrySummary.from_remote(source).to_dict()
    content.pop("revision")
    return replace(source, remote_revision=_TRANSLATION_REVISION + canonical_hash(content))


def consistent_original_identity(entry: LocalEntrySnapshot | RemoteEntrySnapshot) -> bool:
    """Qualified identities cannot claim a different original than their body."""
    return entry.entry_key.original is None or entry.entry_key.original == entry.original


def content_source_summary(entry: RemoteEntrySnapshot) -> EntrySummary:
    return without_remote_identity(EntrySummary.from_remote(entry))


def without_remote_identity(summary: EntrySummary) -> EntrySummary:
    """Reuse precomputed content hashes while dropping revision and source layout."""

    # Cloud order belongs to source layout, not the translation being copied.
    summary = replace(summary, context_order=None)
    content = summary.to_dict()
    content.pop("revision")
    return replace(summary, revision=_CONTENT_REVISION + canonical_hash(content))


def equivalent_download_source(
    entries: tuple[RemoteEntrySnapshot, ...], target: EntryKey
) -> RemoteEntrySnapshot | None:
    """Use shared content only; equal translations do not prove a remote identity."""

    if len(entries) < 2 or any(entry.deleted for entry in entries):
        return None
    summary = content_source_summary(entries[0])
    if any(content_source_summary(entry) != summary for entry in entries[1:]):
        return None
    return replace(
        entries[0], entry_key=target, remote_revision=summary.revision, external_ref=None, context_order=None
    )


def group_download_candidates(
    candidates: tuple[DownloadCandidate, ...], current: LocalEntrySnapshot
) -> tuple[DownloadCandidate, ...]:
    groups = defaultdict(list)
    for candidate in candidates:
        if not candidate.selectable or candidate.entry.deleted:
            groups["identity", candidate.token].append(candidate)
        else:
            groups["content", content_source_summary(candidate.entry)].append(candidate)
    result = []
    for members in groups.values():
        if len(members) == 1:
            result.extend(members)
            continue
        entries = tuple(member.entry for member in members)
        source = equivalent_download_source(entries, current.entry_key)
        token = canonical_hash({"content_sources": sorted(member.token for member in members)})
        result.append(DownloadCandidate(token, source, equivalent_entries=entries))
    return tuple(result)


def group_translation_candidates(
    candidates: tuple[DownloadCandidate, ...], current: LocalEntrySnapshot
) -> tuple[DownloadCandidate, ...]:
    """Show one text choice even when its cloud copies have different metadata."""
    groups = defaultdict(list)
    for candidate in candidates:
        if candidate.reason == "remote_untranslated":
            continue
        if candidate.selectable and candidate.entry.translation and not candidate.entry.deleted:
            groups["content", candidate.entry.original, candidate.entry.translation].append(candidate)
        else:
            groups["identity", candidate.token].append(candidate)
    result = []
    for members in groups.values():
        if not members[0].selectable or not members[0].entry.translation or members[0].entry.deleted:
            result.extend(members)
            continue
        entries = tuple(member.entry for member in members)
        source = translation_content_source(entries, current)
        token = canonical_hash({"translation_sources": sorted(member.token for member in members)})
        result.append(DownloadCandidate(token, source, equivalent_entries=entries))
    return tuple(result)


def is_content_source(summary: EntrySummary | None) -> bool:
    return summary is not None and summary.revision.startswith((_CONTENT_REVISION, _TRANSLATION_REVISION))


def is_translation_source(summary: EntrySummary | None) -> bool:
    return summary is not None and summary.revision.startswith(_TRANSLATION_REVISION)
