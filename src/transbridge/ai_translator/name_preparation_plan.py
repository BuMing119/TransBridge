"""Conservative project-name grouping without mutating source entries."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.converter.context_categories import name_category

if TYPE_CHECKING:
    from transbridge.converter.translation_entry import TranslationEntry


class NameSource(Protocol):
    key: str
    translate_entries: tuple[TranslationEntry, ...]


@dataclass(frozen=True, slots=True)
class NameAuthority:
    exact_translation: str | None = None
    blocked: bool = False
    required_terms: tuple[tuple[str, str], ...] = ()
    existing_translations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NameOccurrence:
    source_key: str
    entry: TranslationEntry
    position: int = 0


@dataclass(frozen=True, slots=True)
class NameCandidates:
    """Literal duplicates collected before opening any terminology database."""

    category: str
    occurrences: tuple[NameOccurrence, ...]


@dataclass(frozen=True, slots=True)
class NameGroup:
    category: str
    authority: NameAuthority
    occurrences: tuple[NameOccurrence, ...]

    @property
    def representative(self) -> NameOccurrence:
        return self.occurrences[0]


def eligible_names(
    task: NameSource, *, overwrite: bool, checkpoint: Callable[[], None] | None = None
) -> tuple[TranslationEntry, ...]:
    selected = []
    for entry in task.translate_entries:
        if checkpoint is not None:
            checkpoint()
        if (
            name_category(entry.context) is not None
            and entry.original.strip()
            and (overwrite or not entry.translation or entry.stage == 0)
            and DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
        ):
            selected.append(entry)
    return tuple(selected)


def plan_project_names(
    tasks: Iterable[NameSource],
    *,
    overwrite: bool,
    authority_for: Callable[[NameSource, TranslationEntry], NameAuthority],
    checkpoint: Callable[[], None] | None = None,
) -> tuple[NameGroup, ...]:
    """Collect literal duplicates, then resolve their reuse constraints."""
    tasks = tuple(tasks)
    by_source = {task.key: task for task in tasks}
    candidates = collect_project_names(tasks, overwrite=overwrite, checkpoint=checkpoint)
    return resolve_project_names(
        candidates,
        authority_for=lambda occurrence, _: authority_for(by_source[occurrence.source_key], occurrence.entry),
        checkpoint=checkpoint,
    )


def collect_project_names(
    tasks: Iterable[NameSource],
    *,
    overwrite: bool,
    checkpoint: Callable[[], None] | None = None,
    progress: Callable[[NameSource, int, int], None] | None = None,
) -> tuple[NameCandidates, ...]:
    """Group in one pass by literal spelling/category; do not consult terms."""
    groups: dict[tuple[str, str], list[NameOccurrence]] = {}
    position = 0
    for task in tasks:
        scanned = 0
        total = len(task.translate_entries) if hasattr(task.translate_entries, "__len__") else 0
        if progress is not None:
            progress(task, 0, total)

        def scan_checkpoint():
            nonlocal scanned
            if checkpoint is not None:
                checkpoint()
            scanned += 1
            if progress is not None and scanned % 256 == 0:
                progress(task, scanned, total)

        for entry in eligible_names(task, overwrite=overwrite, checkpoint=scan_checkpoint):
            category = name_category(entry.context)
            groups.setdefault((entry.original, category), []).append(NameOccurrence(task.key, entry, position))
            position += 1
        if progress is not None:
            progress(task, scanned, total or scanned)
    return tuple(NameCandidates(category, tuple(items)) for (_, category), items in groups.items())


def resolve_project_names(
    candidates: Iterable[NameCandidates],
    *,
    authority_for: Callable[[NameOccurrence, bool], NameAuthority],
    checkpoint: Callable[[], None] | None = None,
) -> tuple[NameGroup, ...]:
    """Split collected duplicates only where effective constraints differ."""
    groups: dict[tuple, list[NameOccurrence]] = {}
    for candidate in candidates:
        repeated = len(candidate.occurrences) > 1
        for occurrence in candidate.occurrences:
            if checkpoint is not None:
                checkpoint()
            entry = occurrence.entry
            authority = authority_for(occurrence, repeated)
            # Conflicting local translations already prove this spelling may
            # represent different names; keep each occurrence independent.
            discriminator = (
                (occurrence.source_key, entry.identity) if len(authority.existing_translations) > 1 else None
            )
            key = (entry.original, candidate.category, authority, discriminator)
            groups.setdefault(key, []).append(occurrence)
    resolved = [NameGroup(category, authority, tuple(items)) for (_, category, authority, _), items in groups.items()]
    return tuple(sorted(resolved, key=lambda group: group.representative.position))
