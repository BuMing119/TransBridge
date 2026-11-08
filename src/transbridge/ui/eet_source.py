"""Content-aware EET inputs used by the existing background GUI workers."""

from dataclasses import replace

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import FormatId
from transbridge.application.io.eet_source import parse_eet_source, probe_eet_format
from transbridge.application.io.legacy_migration import prepare_legacy_migration
from transbridge.application.io.migration_import import prepare_migration_import
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def parse_eet_collection(path, context=None):
    parsed = parse_eet_source(path, context)
    if parsed.outcome is not OperationOutcome.COMPLETED:
        details = "; ".join(f"{item.code}: {item.message}" for item in parsed.diagnostics)
        raise ValueError(details or "EET 来源未能完整解析")
    return TranslationEntryCollection(parsed.entries), parsed


def prepare_eet_migration(collection, path, context=None):
    """Retain the existing fill-empty policy for optional parse-time translations."""
    if probe_eet_format(path) is FormatId.XML_EET:
        draft = prepare_legacy_migration(collection, eet_path=path, context=context)
        return draft.collection, draft.applied_count
    draft = prepare_migration_import(path, collection, format_hint=FormatId.BINARY_EET, context=context)
    states = draft.state_mapping()
    entries = []
    count = 0
    for entry in collection:
        if entry.identity in states and not entry.translation and entry.stage == 0:
            translation, stage = states[entry.identity]
            entry = replace(entry, translation=translation, stage=stage, revision=entry.revision.next())
            count += 1
        entries.append(entry)
    return TranslationEntryCollection(entries), count


def apply_eet_migration(collection, path, context=None):
    """Compatibility boundary for the legacy Step1 worker's mutable collection."""
    prepared, count = prepare_eet_migration(collection, path, context)
    for entry in prepared:
        if collection.get(entry.identity) != entry:
            collection.add(entry, overwrite=True)
    return count
