"""Reconstruct original task inputs without treating current edits as task validity."""

from transbridge.application.io.identity import EntryKey, EntryRevision
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .task_scope import SourceTask


def frozen_recovery_sources(record, slots):
    tasks, seen = [], set()
    applied = {EntryKey.from_dict(row["entry_key"]): row for row in record.get("applied_entries", ())}
    for source in record["sources"]:
        inputs = {EntryKey.from_dict(row["entry_key"]): row for row in source.get("inputs", ())}
        slot = slots.get(source["key"])
        values, groups = {}, []
        for field in ("translate_keys", "polish_keys", "postprocess_keys"):
            keys = []
            for raw_key in source.get(field, ()):
                key = EntryKey.from_dict(raw_key)
                saved, current = applied.get(key), None if slot is None else slot.collection.get(key)
                if (
                    saved is not None
                    and current is not None
                    and (current.original, current.translation, current.stage, current.context or "")
                    == (saved["original"], saved["text"], saved["stage"], inputs[key]["context"])
                ):
                    continue
                keys.append(key)
            for key in keys:
                if key in seen:
                    raise ValueError("恢复任务包含重复条目身份。")
                seen.add(key)
                before = inputs.get(key)
                if before is None:
                    raise ValueError(f"恢复任务缺少词条输入快照：{key.local_key}。")
                if "entry" in before:
                    entry = TranslationEntry.from_dict(before["entry"])
                    if entry.identity != key or (
                        entry.original,
                        entry.translation,
                        entry.stage,
                        entry.context or "",
                        entry.revision.value,
                    ) != tuple(before[name] for name in ("original", "translation", "stage", "context", "revision")):
                        raise ValueError(f"恢复任务词条封套与固定输入不匹配：{key.local_key}。")
                    values[key] = entry
                    continue
                values[key] = TranslationEntry(
                    key.local_key,
                    key.local_key,
                    before["original"],
                    before["translation"],
                    before["stage"],
                    before["context"],
                    entry_key=key,
                    revision=EntryRevision(before["revision"]),
                )
            groups.append(tuple(values[key] for key in keys))
        collection = TranslationEntryCollection(values.values())
        tasks.append(
            SourceTask(
                source["key"],
                source["label"],
                slot.esp_path if slot is not None else source.get("esp_path"),
                collection,
                *groups,
            )
        )
    return tuple(tasks)
