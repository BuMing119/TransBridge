"""Pure legacy import matching; publication belongs to the collection."""

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import replace
import logging

from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry, _normalize_text
from transbridge.parser.eet_parser import EET_Entry
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import XT_Entry


def _type_field_base(context: str) -> str:
    return context.split("|")[0] if context else ""


def match_eet_updates(entries: Iterable[TranslationEntry], source: Iterable[EET_Entry]) -> Iterator[TranslationEntry]:
    """Exact id/original first, then original/type; first nonempty source wins."""
    all_eet = list(source)
    eet_by_id: dict[str, list[EET_Entry]] = defaultdict(list)
    for item in all_eet:
        entry_id = TranslationEntry._build_eet_id(item.edid, item.id, item.index, item.grup, item.champ)
        eet_by_id[entry_id].append(item)
    unmatched = []
    for entry in entries:
        if entry.requires_original_match:
            candidates = [item for item in eet_by_id.get(entry.id, ()) if item.original == entry.original]
            if len(candidates) == 1:
                if candidates[0].traduit:
                    yield replace(entry, translation=candidates[0].traduit, stage=STAGE_TRANSLATED)
            else:
                logging.getLogger(__name__).warning(
                    "SOURCE_ORIGINAL_MATCH_REQUIRED: EET 词条 %s 无法由原文唯一定位，已跳过。", entry.key
                )
            continue
        for item in eet_by_id.get(entry.id, ()):
            if item.original == entry.original and item.traduit:
                yield replace(entry, translation=item.traduit, stage=STAGE_TRANSLATED)
                break
        else:
            unmatched.append(entry)
    if unmatched:
        fallback: dict[tuple[str, str], EET_Entry] = {}
        for item in all_eet:
            if item.traduit:
                fallback.setdefault((item.original, f"{item.grup}:{item.champ}"), item)
        for entry in unmatched:
            item = fallback.get((entry.original, _type_field_base(entry.context)))
            if item is not None:
                yield replace(entry, translation=item.traduit, stage=STAGE_TRANSLATED)


def match_xt_updates(entries: Iterable[TranslationEntry], source: Iterable[XT_Entry]) -> Iterator[TranslationEntry]:
    """Preserve exact-match no-op suppression and the legacy text fallback."""
    all_xt = list(source)
    xt_by_edid: dict[str, list[XT_Entry]] = defaultdict(list)
    for item in all_xt:
        xt_by_edid[item.edid].append(item)
    unmatched = []
    for entry in entries:
        left, _, right_with_other = entry.id.partition(":")
        right = right_with_other.split("|")[0]
        if entry.requires_original_match:
            candidates = [
                updated
                for edid in {left, right, f"[{right}]"}
                for item in xt_by_edid.get(edid, ())
                if item.source == entry.original
                and (updated := TranslationEntry.try_update_from_xt(entry, item)) is not None
            ]
            if len(candidates) == 1:
                if candidates[0] is not entry:
                    yield candidates[0]
            else:
                logging.getLogger(__name__).warning(
                    "SOURCE_ORIGINAL_MATCH_REQUIRED: XT 词条 %s 无法由原文唯一定位，已跳过。", entry.key
                )
            continue
        matched = False
        for edid in (left, right, f"[{right}]"):
            for item in xt_by_edid.get(edid, ()):
                updated = TranslationEntry.try_update_from_xt(entry, item)
                if updated is None:
                    continue
                if updated is not entry:
                    yield updated
                matched = True
                break
            if matched:
                break
        if not matched:
            unmatched.append(entry)
    if unmatched:
        fallback: dict[tuple[str, str], XT_Entry] = {}
        for item in all_xt:
            if item.dest:
                fallback.setdefault((_normalize_text(item.source), item.rec), item)
        for entry in unmatched:
            item = fallback.get((_normalize_text(entry.original), _type_field_base(entry.context)))
            if item is not None:
                yield replace(entry, translation=item.dest, stage=STAGE_TRANSLATED)


def match_strings_updates(
    entries: Iterable[TranslationEntry], lookup: PluginStringsLookup, *, overwrite: bool
) -> Iterator[TranslationEntry]:
    """Match string ids, retaining the legacy empty-translation behavior."""
    for entry in entries:
        if entry.translation and not overwrite:
            continue
        if entry.string_id is None:
            continue
        text = lookup.get(entry.string_id)
        if text is None or text == entry.original:
            continue
        yield replace(entry, translation=text, stage=STAGE_TRANSLATED)
