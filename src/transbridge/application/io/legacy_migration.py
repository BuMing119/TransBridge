"""Prepare XML/Strings migration on a detached collection using legacy matching."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import XT_XmlParser

from .contracts import FormatId, ParseRequest, SourceDescriptor
from .migration_import import MigrationImportError
from .migration_snapshot import detached_entries
from .strings_adapter import LocalizedStringsAdapter


@dataclass(frozen=True)
class LegacyMigrationDraft:
    collection: TranslationEntryCollection
    strings_lookup: PluginStringsLookup | None = None
    applied_count: int = 0


def prepare_legacy_migration(
    target: TranslationEntryCollection,
    *,
    eet_path: str | None = None,
    xt_path: str | None = None,
    strings_dir: str | None = None,
    plugin_stem: str = "",
    strings_lang: str = "english",
    context: RequestContext | None = None,
) -> LegacyMigrationDraft:
    """Preserve EET → XT → Strings matching/overwrite order without publishing.

    Every selected source must parse successfully before this draft can be used.
    Missing per-plugin Strings files remain a no-op for apply-to-all migrations.
    """
    candidate = TranslationEntryCollection(detached_entries(target))
    lookup = None
    applied_count = 0
    try:
        if eet_path:
            applied_count += candidate.update_from_eet_xml(Path(eet_path))
        if xt_path:
            applied_count += candidate.apply_xt_entries(XT_XmlParser.from_file(xt_path).entries)
        if strings_dir:
            lookup = _read_strings(Path(strings_dir), plugin_stem, strings_lang, context)
            if lookup:
                applied_count += candidate.update_from_strings_lookup(lookup)
    except MigrationImportError:
        raise
    except Exception as exc:
        raise MigrationImportError("MIGRATION_LEGACY_SOURCE_INVALID", f"迁移源无法完整解析：{exc}") from exc
    return LegacyMigrationDraft(candidate, lookup, applied_count)


def _read_strings(
    directory: Path, stem: str, language: str, context: RequestContext | None
) -> PluginStringsLookup | None:
    if not directory.is_dir():
        raise MigrationImportError("MIGRATION_STRINGS_DIRECTORY_INVALID", f"Strings 目录不可用：{directory}")
    merged = {}
    # Keep the existing discovery order and cross-file ID precedence.
    for suffix, format_id in (
        (".strings", FormatId.STRINGS),
        (".dlstrings", FormatId.DLSTRINGS),
        (".ilstrings", FormatId.ILSTRINGS),
    ):
        path = directory / f"{stem}_{language.capitalize()}{suffix}"
        if not path.exists():
            continue
        result = LocalizedStringsAdapter(format_id).parse(
            ParseRequest(SourceDescriptor(str(path), path.name), context or RequestContext("ui.migration-import"))
        )
        if result.outcome is not OperationOutcome.COMPLETED:
            diagnostic = result.diagnostics[0] if result.diagnostics else None
            detail = diagnostic.message if diagnostic is not None else "文件无法完整解析"
            raise MigrationImportError("MIGRATION_STRINGS_INVALID", f"{path.name}：{detail}")
        merged.update((entry.string_id, entry.original) for entry in result.entries)
    return PluginStringsLookup(merged) if merged else None
