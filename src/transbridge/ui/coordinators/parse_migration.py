"""Prepare optional parse-time migrations independently of Qt and publication."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.io.legacy_migration import prepare_legacy_migration
from transbridge.application.io.migration_import import prepare_migration_import
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseMigrationResult:
    collection: TranslationEntryCollection
    applied_count: int
    completed_sources: frozenset[str]
    failures: tuple[str, ...]


def prepare_parse_migrations(
    collection: TranslationEntryCollection,
    config,
    *,
    plugin_path: str | None = None,
    context: RequestContext | None = None,
) -> ParseMigrationResult:
    """Keep successful sources while rejecting each failed source atomically.

    Preserve the existing EET → XT → translated plugin → Strings precedence.
    EET parsing already supplied the primary XML, so only plugin parsing applies
    it as a migration. Explicitly selected sources must use strict readers.
    """
    completed = set()
    failures = []
    applied = 0
    for field, label, path in (
        ("eet_path", "EET XML", config.eet_path if plugin_path else None),
        ("xt_path", "XTranslator XML", config.xt_path),
        ("tp_path", "已翻译插件", config.tp_path),
        ("strings_dir", "Strings", config.strings_dir if plugin_path else None),
    ):
        if not path:
            continue
        try:
            if field == "tp_path":
                candidate, count = _translated_plugin(collection, path, context)
            else:
                draft = prepare_legacy_migration(
                    collection,
                    **{field: path},
                    plugin_stem=Path(plugin_path).stem if plugin_path else "",
                    strings_lang=config.strings_lang,
                    context=context,
                )
                candidate, count = draft.collection, draft.applied_count
            collection = candidate
            applied += count
            completed.add(field)
        except Exception as exc:  # noqa: BLE001 - retain the source failure and continue other selected migrations
            logger.exception("Parse migration failed: %s (%s)", label, path)
            failures.append(f"{label}（{path}）：{exc}")
    return ParseMigrationResult(collection, applied, frozenset(completed), tuple(failures))


def _translated_plugin(collection, path, context):
    draft = prepare_migration_import(path, collection, format_hint="plugin.sse", context=context)
    states = draft.state_mapping()
    count = 0
    entries = []
    for entry in collection:
        if entry.identity in states and not entry.translation:
            translation, stage = states[entry.identity]
            entry = replace(entry, translation=translation, stage=stage, revision=entry.revision.next())
            count += 1
        entries.append(entry)
    return TranslationEntryCollection(entries), count
