"""Recover plugin-qualified DSD fields omitted by hydrated entry snapshots."""

from dataclasses import replace
import hashlib

from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .contracts import FormatId
from .plugin_transfer_snapshot import target_entries_from_snapshot


def restore_plugin_dsd_locators(target, snapshot) -> TranslationEntryCollection:
    """Bind target entries to captured records before restoring DSD convenience fields.

    Accept an iterable of TranslationEntry values. Identity, original, translation,
    stage and metadata remain owned by that iterable; no filename-based plugin
    attribution or fallback to mutable disk inputs is permitted.
    """
    if snapshot is None or snapshot.format_id is not FormatId.PLUGIN_SSE:
        raise ValueError("DSD 插件定位需要完整的插件来源快照。")
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
        entries.append(
            replace(
                entry,
                form_id_with_plugin=original.form_id_with_plugin,
                dsd_type=original.dsd_type,
                dsd_index=original.dsd_index,
                editor_id=original.editor_id,
            )
        )
    return TranslationEntryCollection(entries)
