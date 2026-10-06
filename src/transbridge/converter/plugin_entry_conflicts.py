"""Disambiguate plugin locators using their exact source text."""

from collections import defaultdict
from dataclasses import replace

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.io.identity import EntryKey
from transbridge.converter.translation_entry import TranslationEntry


def resolve_plugin_entry_conflicts(
    entries: list[TranslationEntry],
) -> tuple[list[TranslationEntry], tuple[Diagnostic, ...]]:
    """Preserve distinct originals, excluding every indistinguishable occurrence."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, entry in enumerate(entries):
        groups[entry.key].append(index)
    result = list(entries)
    rejected: set[int] = set()
    diagnostics: list[Diagnostic] = []
    for key, indices in groups.items():
        if len(indices) < 2:
            continue
        originals: dict[str, list[int]] = defaultdict(list)
        for index in indices:
            entry = entries[index]
            result[index] = replace(
                entry,
                requires_original_match=True,
                entry_key=EntryKey(entry.identity.namespace, entry.key, entry.original),
            )
            originals[entry.original].append(index)
        for duplicates in originals.values():
            if len(duplicates) < 2:
                continue
            rejected.update(duplicates)
            diagnostics.extend(
                Diagnostic(
                    "SOURCE_LOCATOR_CONFLICT",
                    "Plugin entries have the same locator and original text; these entries were skipped.",
                    severity=DiagnosticSeverity.WARNING,
                    details=(("record_index", index), ("locator", key), ("conflicting_indices", tuple(duplicates))),
                )
                for index in duplicates
            )
    return [entry for index, entry in enumerate(result) if index not in rejected], tuple(diagnostics)
