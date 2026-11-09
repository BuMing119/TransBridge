"""Prepare one translation-file import with an explicit existing-text policy.

The GUI selects a file, not a parser. Detection retains separate XML/EET/SST
contracts and the distinct DSD and ParaTranz JSON schemas. Publication remains
the caller's guarded project command; this module never mutates its target.
"""

from dataclasses import replace
from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from . import legacy_migration, migration_import
from .catalog import FormatCatalog
from .contracts import FormatId, ProbeRequest, ProbeStatus, SourceDescriptor, SourceSnapshot
from .dsd_projection import restore_plugin_dsd_locators
from .legacy_migration import LegacyMigrationDraft
from .migration_import import MigrationImportError
from .xml_translation_import import xml_translation_proposals


def prepare_translation_import(
    source_path: str,
    target: TranslationEntryCollection,
    *,
    format_hint: str | None = None,
    replace_existing: bool = False,
    plugin_stem: str = "",
    strings_lang: str = "chinese",
    context: RequestContext | None = None,
    target_snapshot: SourceSnapshot | None = None,
    require_original_match: bool = False,
) -> LegacyMigrationDraft:
    """Apply uniquely matched proposals, retaining protected and unmatched entries."""
    path = Path(source_path)
    legacy = None
    if format_hint == "strings-directory":
        legacy = legacy_migration.prepare_legacy_migration(
            target,
            strings_dir=str(path),
            plugin_stem=plugin_stem,
            strings_lang=strings_lang,
            context=context,
            overwrite_strings=replace_existing,
        )
    else:
        try:
            content = path.read_bytes()
            hint = FormatId(format_hint) if format_hint else None
        except (OSError, ValueError) as exc:
            raise MigrationImportError("IMPORT_SOURCE_INVALID", f"无法读取译文文件或所选格式无效：{exc}") from exc
        probe = FormatCatalog().resolve(ProbeRequest(SourceDescriptor(str(path), path.name), content, hint))
        if probe.status is ProbeStatus.AMBIGUOUS:
            raise MigrationImportError(
                "IMPORT_FORMAT_AMBIGUOUS",
                "无法确定文件格式，请在导入窗口选择具体格式（DSD JSON 或 ParaTranz JSON 等）。",
            )
        if probe.status is not ProbeStatus.EXACT:
            raise MigrationImportError("IMPORT_FORMAT_UNSUPPORTED", "无法识别译文文件，请检查文件内容和格式。")
        format_id = probe.candidates[0]
        if format_id in {FormatId.XML_EET, FormatId.XML_XT}:
            proposals = xml_translation_proposals(content, format_id, target)
        else:
            matching_target = target
            if (
                format_id is FormatId.JSON_DSD
                and target_snapshot is not None
                and target_snapshot.format_id is FormatId.PLUGIN_SSE
            ):
                matching_target = restore_plugin_dsd_locators(target, target_snapshot)
            draft = migration_import.prepare_migration_import(
                path,
                matching_target,
                format_hint=format_id,
                context=context,
                require_original_match=require_original_match,
            )
            proposals = draft.state_mapping()

    if legacy is not None:
        previous = {entry.identity: (entry.translation, entry.stage) for entry in target}
        proposals = {
            entry.identity: (entry.translation, entry.stage)
            for entry in legacy.collection
            if (entry.translation, entry.stage) != previous[entry.identity]
        }

    applied = 0
    entries = []
    for entry in target:
        state = proposals.get(entry.identity)
        eligible = entry.stage not in {-1, 9} and (replace_existing or (not entry.translation and entry.stage == 0))
        if state and state[0] and eligible and state != (entry.translation, entry.stage):
            entries.append(replace(entry, translation=state[0], stage=state[1], revision=entry.revision.next()))
            applied += 1
        else:
            entries.append(entry)
    return LegacyMigrationDraft(
        TranslationEntryCollection(entries), legacy.strings_lookup if legacy is not None else None, applied
    )
