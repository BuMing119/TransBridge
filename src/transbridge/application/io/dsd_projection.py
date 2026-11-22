# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Recover plugin-qualified DSD fields omitted by hydrated entry snapshots."""

from dataclasses import replace
import hashlib

from transbridge.converter.dsd_metadata import DSD_NATIVE_FIELDS, DSD_QUEST_INDEX, DSD_RECORD_METADATA, DSD_REGION_INDEX
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .contracts import FormatId
from .json_source import parse_json_source
from .plugin_transfer_snapshot import target_entries_from_snapshot

DSD_LOCATOR_METADATA = "transbridge.io.dsd.locator"


def restore_extracted_dsd_locator(entry, *, source_format=None):
    metadata = dict(entry.metadata)
    locator = metadata.get(DSD_LOCATOR_METADATA)
    if locator is None:
        raise ValueError("该词条没有可验证的 DSD 插件和字段定位。")
    form, field, index, editor = locator
    if source_format is FormatId.PLUGIN_SSE:
        # Old plugin snapshots retained the native index in the entry ID even when they saved zero as 1.
        if (
            index == 1
            and field in entry.DSD_INDEX_TYPES
            and entry.id.endswith(f":{form.partition('|')[0]}|0~{field.replace(' ', ':')}")
        ):
            index = 0
        elif field == "QUST NNAM" and -32768 <= index < 0:
            index += 65536
    if source_format is FormatId.JSON_DSD and DSD_NATIVE_FIELDS not in metadata:
        # The old reader saved 1 for an omitted index and "" for omitted text fields.
        fields = []
        if editor:
            fields.append("editor_id")
        if entry.original:
            fields.append("original")
        if index != 1 or field in entry.DSD_INDEX_TYPES:
            fields.append("index")
        if field == "QUST CNAM" and "index" in fields:
            metadata[DSD_QUEST_INDEX] = index
        elif field == "REGN RDMP" and "index" in fields:
            metadata[DSD_REGION_INDEX] = index
        if field != "QUST CNAM" or "index" in fields or entry.original:
            metadata[DSD_NATIVE_FIELDS] = tuple(fields)
    return replace(
        entry,
        form_id_with_plugin=form,
        dsd_type=field,
        dsd_index=index,
        editor_id=editor,
        metadata=tuple(metadata.items()),
    )


def restore_json_dsd_locators(target, snapshot) -> TranslationEntryCollection:
    if snapshot.content is None:
        return TranslationEntryCollection(
            restore_extracted_dsd_locator(entry, source_format=snapshot.format_id) for entry in target
        )
    parsed = parse_json_source(snapshot.source.uri, {"format": "dsd"}, content=snapshot.content)
    native = {(entry.key, entry.original): entry for entry in parsed.collection}
    entries = []
    for entry in target:
        original = native.get((entry.key, entry.original))
        if original is None:
            raise ValueError("当前词条与 DSD 来源快照不一致，请重新打开工程。")
        entries.append(_with_native_locator(entry, original))
    return TranslationEntryCollection(entries)


def _with_native_locator(entry, original):
    metadata = {key: value for key, value in entry.metadata if key not in DSD_RECORD_METADATA}
    metadata.update((key, value) for key, value in original.metadata if key in DSD_RECORD_METADATA)
    return replace(
        entry,
        form_id_with_plugin=original.form_id_with_plugin,
        dsd_type=original.dsd_type,
        dsd_index=original.dsd_index,
        editor_id=original.editor_id,
        metadata=tuple(metadata.items()),
    )


def restore_plugin_dsd_locators(target, snapshot) -> TranslationEntryCollection:
    """Bind target entries to captured records before restoring DSD convenience fields.

    Accept an iterable of TranslationEntry values. Identity, original, translation,
    stage and metadata remain owned by that iterable; no filename-based plugin
    attribution or fallback to mutable disk inputs is permitted.
    """
    if snapshot is None or snapshot.format_id is not FormatId.PLUGIN_SSE:
        raise ValueError("DSD 插件定位需要完整的插件来源快照。")
    if snapshot.content is None and snapshot.source.uri.startswith("project-source:"):
        return TranslationEntryCollection(
            restore_extracted_dsd_locator(entry, source_format=snapshot.format_id) for entry in target
        )
    if (
        snapshot.content is None
        or len(snapshot.content) != snapshot.size_bytes
        or hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256
    ):
        raise ValueError("插件来源快照内容与摘要不一致，请重新打开工程。")
    native = {entry.identity: entry for entry in target_entries_from_snapshot(snapshot)}
    entries = []
    for entry in target:
        original = native.get(entry.identity)
        if original is None or original.original != entry.original:
            raise ValueError("当前词条与插件来源快照不一致，请重新打开工程。")
        entries.append(_with_native_locator(entry, original))
    return TranslationEntryCollection(entries)
