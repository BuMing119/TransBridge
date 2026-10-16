"""Read-only rows for inspecting every result of an authoritative source import preview."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.terminology.identity import normalize_original

from .importing import TerminologyProfileImportConflictKind, TerminologyProfileImportPreview, TerminologySourceEntry


class ImportPreviewOutcome(StrEnum):
    ADOPTED = "adopted"
    KEPT = "kept"
    SOURCE_CONFLICT = "source_conflict"
    SCOPE_AMBIGUOUS = "scope_ambiguous"
    SOURCE_ONLY = "source_only"


@dataclass(frozen=True, slots=True)
class ImportPreviewRow:
    row_id: str
    original: str
    base_translation: str
    translation: str
    scope_kind: str | None
    plugin_id: str | None
    outcome: ImportPreviewOutcome
    source_translations: tuple[str, ...] = ()
    base_scopes: tuple[str, ...] = ()


def import_preview_rows(preview: TerminologyProfileImportPreview) -> tuple[ImportPreviewRow, ...]:
    """Project the existing decision, without changing mappings or resolving conflicts."""

    conflicts = {item.normalized_original: item for item in preview.conflicts}
    source_groups: dict[str, list[TerminologySourceEntry]] = defaultdict(list)
    for entry in preview.source.entries:
        source_groups[normalize_original(entry.original)].append(entry)
    mapped_originals = set()
    rows = []
    for mapping in preview.content.mappings:
        normalized = normalize_original(mapping.original)
        mapped_originals.add(normalized)
        conflict = conflicts.get(normalized)
        source = source_groups.get(normalized, ())
        if conflict is not None:
            outcome = (
                ImportPreviewOutcome.SOURCE_CONFLICT
                if conflict.kind is TerminologyProfileImportConflictKind.SOURCE_TRANSLATIONS
                else ImportPreviewOutcome.SCOPE_AMBIGUOUS
            )
        else:
            outcome = ImportPreviewOutcome.ADOPTED if source else ImportPreviewOutcome.KEPT
        rows.append(
            ImportPreviewRow(
                mapping.term_key,
                mapping.original,
                mapping.base_translation,
                mapping.translation,
                mapping.scope_kind,
                mapping.plugin_id,
                outcome,
                tuple(sorted({item.translation for item in source})),
                () if conflict is None else conflict.base_scopes,
            )
        )
    for normalized in sorted(source_groups.keys() - mapped_originals):
        source = source_groups[normalized]
        rows.append(
            ImportPreviewRow(
                f"source-only:{normalized}",
                min(item.original for item in source),
                "",
                "",
                None,
                None,
                ImportPreviewOutcome.SOURCE_ONLY,
                tuple(sorted({item.translation for item in source})),
            )
        )
    return tuple(rows)


__all__ = ["ImportPreviewOutcome", "ImportPreviewRow", "import_preview_rows"]
