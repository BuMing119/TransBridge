# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read and write DSD records and preserve variants with the same public locator."""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
import json
import logging
from typing import Any

from transbridge.converter.dsd_metadata import (
    DSD_EMPTY_REPLACEMENT,
    build_dsd_metadata,
    dsd_conditions,
    dsd_has_replacement,
    dsd_native_fields,
    dsd_quest_index,
    dsd_region_index,
)
from transbridge.converter.plugin_entry_conflicts import resolve_plugin_entry_conflicts
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED, TranslationEntry


def encode_dsd_entry(entry: TranslationEntry) -> dict[str, Any]:
    """Export a native DSD record or generate one from a translation entry."""
    if not dsd_has_replacement(entry):
        return {}

    record: dict[str, Any] = {"form_id": entry.form_id_with_plugin or "", "type": entry.dsd_type}
    fields = dsd_native_fields(entry)
    if fields is not None:
        if "editor_id" in fields:
            record["editor_id"] = entry.editor_id
        if "index" in fields:
            record["index"] = entry.dsd_index
        if "original" in fields:
            record["original"] = entry.original
    elif entry.dsd_type == "GMST DATA":
        record["editor_id"] = entry.editor_id
    elif entry.dsd_type == "QUST CNAM":
        index = dsd_quest_index(entry)
        if index is not None:
            record["index"] = index
        elif entry.original:
            record["original"] = entry.original
        else:
            raise ValueError("任务日志缺少可验证的 DSD 索引和原文；请从原插件重新提取词条。")
    elif entry.dsd_type == "REGN RDMP":
        index = dsd_region_index(entry)
        if index is not None:
            record["index"] = index
    elif entry.dsd_type in entry.DSD_INDEX_TYPES:
        record["index"] = entry.dsd_index

    record["string"] = entry.translation
    conditions = dsd_conditions(entry)
    if conditions is not None:
        record["conditions"] = list(conditions)
    return record


def decode_dsd_entry[EntryT: TranslationEntry](entry_type: type[EntryT], data: Mapping[str, Any]) -> EntryT:
    """Convert one native record to an entry, retaining its raw form ID and selectors."""
    form_id = _dsd_text("form_id", data["form_id"])
    type_str = _dsd_text("type", data["type"])
    string = _dsd_text("string", data.get("string", ""))
    editor_id = _dsd_text("editor_id", data.get("editor_id", ""))
    original = _dsd_text("original", data.get("original", ""))
    metadata = build_dsd_metadata(data)
    stage = STAGE_TRANSLATED if string or dict(metadata).get(DSD_EMPTY_REPLACEMENT) else STAGE_UNTRANSLATED

    form_id_hex = form_id.partition("|")[0]
    index = data.get("index", 1)
    context = type_str.replace(" ", ":")
    key = f"{editor_id}:{form_id_hex}|{index}~{context}"

    return entry_type(
        id=key,
        key=key,
        original=original,
        translation=string,
        stage=stage,
        context=context,
        form_id_with_plugin=form_id,
        dsd_type=type_str,
        dsd_index=index,
        editor_id=editor_id,
        metadata=metadata,
    )


def _dsd_text(field: str, value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"DSD {field} must be a string")
    return value


def parse_dsd_entries(records: Iterable[Mapping[str, Any]]) -> list[TranslationEntry]:
    """Preserve distinct native selectors before resolving original-text collisions."""
    entries = [TranslationEntry.from_dsd_dict(record) for record in records]
    _disambiguate_native_variants(entries)
    valid, diagnostics = resolve_plugin_entry_conflicts(entries)
    if diagnostics:
        logging.getLogger(__name__).warning(
            "SOURCE_LOCATOR_CONFLICT: DSD 中 %d 条定位与原文均相同的条目已跳过，保留 %d 条。",
            len(diagnostics),
            len(valid),
        )
    return valid


def _disambiguate_native_variants(entries: list[TranslationEntry]) -> None:
    positions_by_key: dict[str, list[int]] = defaultdict(list)
    for position, entry in enumerate(entries):
        positions_by_key[entry.key].append(position)

    for key, positions in positions_by_key.items():
        if len(positions) < 2:
            continue
        variants: dict[tuple[str, tuple[str, ...], tuple[str, ...] | None], list[int]] = defaultdict(list)
        for position in positions:
            entry = entries[position]
            owner = (entry.form_id_with_plugin or "").partition("|")[2].casefold()
            fields = tuple(sorted(dsd_native_fields(entry) or ()))
            variants[owner, fields, dsd_conditions(entry)].append(position)
        if len(variants) < 2:
            continue
        for selector, variant_positions in variants.items():
            local_key = f"{key}~{json.dumps(selector, ensure_ascii=False, separators=(',', ':'))}"
            for position in variant_positions:
                entry = entries[position]
                entries[position] = replace(
                    entry, key=local_key, entry_key=replace(entry.identity, local_key=local_key)
                )
