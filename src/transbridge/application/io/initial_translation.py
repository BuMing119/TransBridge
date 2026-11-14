# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Fold optional translations into a parsed source before project publication."""

from dataclasses import replace
from pathlib import Path

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, DomainError, ErrorCategory
from transbridge.config.text_encoding import DEFAULT_TEXT_ENCODING, XTRANSLATOR_READ_POLICY
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .contracts import FormatId
from .migration_import import MigrationImportError
from .translation_import import prepare_translation_import

_NO_UPDATES = {
    "MIGRATION_NO_PROVABLE_MATCHES",
    "MIGRATION_SOURCE_EMPTY",
    "MIGRATION_SOURCE_NO_TRANSLATIONS",
    "MIGRATION_SST_NO_TRANSLATIONS",
}


def apply_initial_translation(
    parsed,
    source_path,
    options,
    context,
    *,
    input_encoding: str = DEFAULT_TEXT_ENCODING,
    source_language: str | None = None,
    text_read_policy: str = XTRANSLATOR_READ_POLICY,
):
    """Combine independently matched donors; never resolve conflicts by file order."""
    locations = options.get("locations") if isinstance(options, dict) else None
    if locations is None and isinstance(options, dict):
        locations = (options.get("location"),)
    if not isinstance(locations, (list, tuple)) or not locations or any(not str(p or "").strip() for p in locations):
        raise DomainError(ErrorCategory.INPUT, "INITIAL_TRANSLATION_REQUIRED", "请选择已有译文来源。")
    hint = options.get("format_hint")
    if hint == "strings-directory" and parsed.format_id is not FormatId.PLUGIN_SSE:
        raise DomainError(ErrorCategory.INPUT, "INITIAL_STRINGS_PLUGIN_REQUIRED", "Strings 目录只能应用到插件来源。")
    if hint == "strings-directory" and len(locations) != 1:
        raise DomainError(ErrorCategory.INPUT, "INITIAL_STRINGS_DIRECTORY_REQUIRED", "请选择一个 Strings 目录。")
    overwrite = bool(options.get("replace_existing", False))
    eligible = {
        entry.identity
        for entry in parsed.entries
        if entry.stage not in {-1, 9} and (overwrite or not entry.translation and entry.stage == 0)
    }
    # Match each donor against the same blank eligible entries. This also exposes
    # a donor agreeing with the current text, so a later differing donor cannot
    # silently win merely because the first one would be an apparent no-op.
    probe_target = TranslationEntryCollection([
        replace(entry, translation="", stage=0) if entry.identity in eligible else entry for entry in parsed.entries
    ])
    proposals = {}
    for location in locations:
        try:
            imported = prepare_translation_import(
                location,
                probe_target,
                format_hint=hint,
                replace_existing=True,
                plugin_stem=Path(source_path).stem if parsed.format_id is FormatId.PLUGIN_SSE else "",
                strings_lang=options.get("strings_lang", "chinese"),
                context=context,
                target_snapshot=parsed.source_snapshot,
                require_original_match=True,
                input_encoding=input_encoding,
                source_language=source_language,
                text_read_policy=text_read_policy,
            )
        except MigrationImportError as exc:
            if exc.code in _NO_UPDATES:
                continue
            raise DomainError(ErrorCategory.INPUT, exc.code, f"译文来源 {location}：{exc.message}", cause=exc) from exc
        for entry in imported.collection:
            if entry.identity not in eligible or not entry.translation:
                continue
            state = entry.translation, entry.stage
            previous = proposals.get(entry.identity)
            if previous is not None and previous[:2] != state:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "INITIAL_TRANSLATION_CONFLICT",
                    f"条目 {entry.key} 匹配到不同译文或状态：{previous[2]} 与 {location}。请移除冲突文件后重试。",
                )
            proposals[entry.identity] = (*state, location)
    entries, applied = [], 0
    for entry in parsed.entries:
        proposed = proposals.get(entry.identity)
        if proposed is not None and proposed[:2] != (entry.translation, entry.stage):
            entry = replace(entry, translation=proposed[0], stage=proposed[1], revision=entry.revision.next())
            applied += 1
        entries.append(entry)
    diagnostics = parsed.diagnostics
    if not applied:
        diagnostics = (
            *diagnostics,
            Diagnostic(
                "INITIAL_TRANSLATION_NO_UPDATES",
                f"{Path(source_path).name}：已有译文未产生更新，请检查匹配内容及保留/替换策略。",
                DiagnosticSeverity.WARNING,
            ),
        )
    return replace(parsed, entries=tuple(entries), diagnostics=diagnostics)
