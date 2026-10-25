"""Admit local translations before scheduling model batches, preserving commit boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from transbridge.application.translation.entry_alias import ai_entry_key

from .prepared_names import PreparedName, PreparedNameSelection, select_prepared_names

if TYPE_CHECKING:
    from threading import Event

    from transbridge.application.io.identity import EntryKey
    from transbridge.converter.translation_entry import TranslationEntry

    from .batch_planner import Batch
    from .term_database import TermDatabaseManager


def select_translation_reuse(
    entries: Sequence[TranslationEntry],
    prepared_names: Mapping[EntryKey, PreparedName],
    manager: TermDatabaseManager,
) -> PreparedNameSelection:
    """Prefer identity-bound prepared outcomes, then scope-aware exact terms."""
    prepared = select_prepared_names(entries, prepared_names)
    accepted = dict(prepared.accepted)
    context_resolver = getattr(manager, "lookup_context_for_entry", None)
    grouped = {}
    for entry in prepared.remaining:
        context = context_resolver(entry) if callable(context_resolver) else None
        grouped.setdefault(context, []).append(entry)
    for context, contextual_entries in grouped.items():
        originals = [entry.original for entry in contextual_entries]
        matches = (
            manager.exact_match(originals, context=context)
            if callable(context_resolver)
            else manager.exact_match(originals)
        )
        for entry in contextual_entries:
            if entry.original in matches:
                accepted[ai_entry_key(entry)] = matches[entry.original]
    remaining = tuple(entry for entry in prepared.remaining if ai_entry_key(entry) not in accepted)
    return PreparedNameSelection(accepted, remaining, prepared.failures)


@dataclass(frozen=True)
class TranslationReuseResult:
    settled: frozenset[EntryKey]
    failures: tuple[str, ...]


def admit_translation_reuse(
    batches: Sequence[Batch],
    *,
    prepared_names: Mapping[EntryKey, PreparedName],
    manager: TermDatabaseManager,
    completed_fingerprints: set[frozenset[str]],
    accept: Callable[[dict[str, str]], int | None],
    stop_event: Event,
    pause_event: Event | None,
    progress: Callable[[str, int, int, str], None] | None,
    log: Callable[[str], None],
) -> TranslationReuseResult:
    """Persist reuse first; keep original batch identities for checkpoint recovery.

    No model calls or draft writes occur here. Failed prepared outcomes and
    persistence failures are settled too, so they cannot fall back to the model.
    Later batches may still find newly generated exact terms through the same
    selector when their turn arrives.
    """
    pending = [
        batch
        for batch in batches
        if frozenset(ai_entry_key(entry) for entry in batch.entries) not in completed_fingerprints
    ]
    total = sum(len(batch.entries) for batch in pending)
    settled, failures = set(), []
    scanned = reused = 0
    if progress is not None and total:
        progress("reuse", 0, total, "先复用已有译文，再翻译剩余条目")
    for batch in pending:
        while not stop_event.is_set() and pause_event is not None and not pause_event.wait(0.05):
            pass
        if stop_event.is_set():
            break
        selected = select_translation_reuse(batch.entries, prepared_names, manager)
        if selected.accepted:
            accepted = accept(selected.accepted)
            if accepted is not None:
                reused += len(selected.accepted)
        failures.extend(selected.failures)
        remaining = {entry.identity for entry in selected.remaining}
        settled.update(entry.identity for entry in batch.entries if entry.identity not in remaining)
        if not remaining:
            completed_fingerprints.add(frozenset(ai_entry_key(entry) for entry in batch.entries))
        scanned += len(batch.entries)
        if progress is not None:
            progress("reuse", scanned, total, f"已检查 {scanned}/{total} 条，已复用 {reused} 条，无需模型翻译")
    if reused:
        log(f"已有译文提前复用：{reused} 条，无需进入模型翻译批次")
    for failure in failures:
        log(f"  ⚠ {failure}")
    return TranslationReuseResult(frozenset(settled), tuple(failures))
