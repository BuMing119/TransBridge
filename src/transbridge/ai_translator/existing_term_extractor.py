"""Initialize dynamic terminology from translations already present in a collection."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
import re
import threading
from typing import TYPE_CHECKING, Literal
import unicodedata

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.token_batching import (
    ContentBatch,
    ContentTokenCounter,
    StableContentBatcher,
)
from transbridge.converter.context_categories import AUTO_TERM_CONTEXTS
from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_QUESTIONABLE
from transbridge.infra.token_counting import TiktokenContentTokenCounter

if TYPE_CHECKING:
    from transbridge.ai_translator.noun_extractor import NounExtractor
    from transbridge.ai_translator.term_database import TermDatabaseManager
    from transbridge.converter.translation_entry import TranslationEntry


EXISTING_NAME_SOURCE = "existing_name"
EXISTING_TEXT_SOURCE = "existing_text"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ExistingTermSeedResult:
    """Summary of one partial-project terminology initialization."""

    direct_added: int = 0
    text_added: int = 0
    conflicts: int = 0
    skipped_existing: int = 0
    text_extraction_attempted: bool = False
    text_batches_completed: int = 0
    text_batches_total: int = 0
    cancelled: bool = False
    error: str | None = None
    conflict_records: tuple[TermConflictEvidence, ...] = ()

    @property
    def added_count(self) -> int:
        return self.direct_added + self.text_added


@dataclass(frozen=True, slots=True)
class TermConflictEvidence:
    """One immutable entry-level explanation of a rejected terminology candidate."""

    entry_key: EntryKey
    term: str
    observed_translation: str
    canonical_translation: str
    kind: Literal["candidate_internal", "effective_library"]
    candidate_source: str = ""
    candidate_context: str = ""
    canonical_source: str = ""
    canonical_context: str = ""


@dataclass(frozen=True, slots=True)
class _TermCandidate:
    entry: TermEntry
    entry_keys: tuple[EntryKey, ...]


@dataclass(frozen=True, slots=True)
class _ExistingTextPair:
    entry_key: EntryKey
    original: str
    translation: str

    def as_payload(self) -> dict[str, str]:
        return {"original": self.original, "translation": self.translation}


def should_seed_existing_terms(
    entries: list[TranslationEntry],
    candidates: list[TranslationEntry],
) -> bool:
    """Return whether the current run is translating the unfinished part of an existing corpus."""

    has_existing_translation = any(_is_eligible_existing_entry(entry) for entry in entries)
    has_untranslated_candidate = any(not entry.translation.strip() for entry in candidates)
    return has_existing_translation and has_untranslated_candidate


class ExistingTermSeeder:
    """Extract, merge, and persist safe terminology from existing translations."""

    def __init__(
        self,
        term_manager: TermDatabaseManager,
        noun_extractor: NounExtractor,
        *,
        max_tokens_per_batch: int = 2000,
        model: str = "",
        token_counter: ContentTokenCounter | None = None,
        max_concurrent: int = 1,
        text_batch_size: int | None = None,
    ) -> None:
        if text_batch_size is not None and text_batch_size <= 0:
            raise ValueError("text_batch_size must be positive when provided")
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or max_concurrent <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        self._term_manager = term_manager
        self._noun_extractor = noun_extractor
        self._batcher = StableContentBatcher(
            token_counter or TiktokenContentTokenCounter(model),
            max_tokens_per_batch,
            max_items=text_batch_size,
        )
        self._max_concurrent = max_concurrent

    def seed(
        self,
        entries: list[TranslationEntry],
        *,
        progress_callback: Callable[[int, int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
    ) -> ExistingTermSeedResult:
        eligible = [entry for entry in entries if _is_eligible_existing_entry(entry)]
        if not eligible:
            _notify(progress_callback, 1, 1, "没有可用于术语初始化的已有译文")
            return ExistingTermSeedResult()

        dynamic_db = self._term_manager.get_dynamic_db()
        direct_terms = self._extract_direct_terms(eligible)

        text_extraction_attempted = not any(entry.source == EXISTING_TEXT_SOURCE for entry in dynamic_db.as_list())
        if text_extraction_attempted:
            text_pairs = self._text_pairs(eligible)
            text_plan = self._batcher.plan(
                text_pairs,
                key=lambda pair: pair.entry_key,
                content=lambda pair: (pair.original, pair.translation),
            )
            text_batches_total = len(text_plan.batches)
            display_total = max(1, text_batches_total)
            _notify(
                progress_callback,
                0,
                display_total,
                f"准备从 {len(text_pairs)} 条已有译文抽取术语（共 {text_batches_total} 批）",
            )
            if log_callback:
                log_callback(f"术语初始化：{len(eligible)} 条已有译文，{text_batches_total} 个 LLM 批次")
            if text_plan.oversized:
                extraction_error = ValueError(text_plan.oversized[0].message)
                text_terms: list[_TermCandidate] = []
                text_batches_completed = 0
                cancelled = False
            else:
                text_terms, text_batches_completed, cancelled, extraction_error = self._extract_text_terms(
                    list(text_plan.batches),
                    progress_callback=progress_callback,
                    log_callback=log_callback,
                    stop_event=stop_event,
                    pause_event=pause_event,
                )
            if cancelled:
                _notify(
                    progress_callback,
                    text_batches_completed,
                    display_total,
                    f"术语抽取已停止（{text_batches_completed}/{text_batches_total} 批）",
                )
                return ExistingTermSeedResult(
                    text_extraction_attempted=True,
                    text_batches_completed=text_batches_completed,
                    text_batches_total=text_batches_total,
                    cancelled=True,
                )
            if extraction_error is not None:
                text_terms = []
                batch_label = (
                    f"第 {extraction_error.batch_number}/{extraction_error.total_batches} 批"
                    if isinstance(extraction_error, _BatchExtractionError)
                    else ""
                )
                message = f"{batch_label}术语抽取失败，已停止后续批次：{extraction_error}"
                _notify(progress_callback, text_batches_completed, display_total, message)
                if log_callback:
                    log_callback(message)
            if text_batches_total == 0 and extraction_error is None:
                _notify(progress_callback, 1, 1, "已有译文中没有需要调用 LLM 的文本")
        else:
            text_terms = []
            text_batches_completed = 0
            text_batches_total = 0
            extraction_error = None
            _notify(progress_callback, 1, 1, "已有术语抽取结果，本次跳过 LLM 初始化")
            if log_callback:
                log_callback("术语初始化：检测到 existing_text 结果，本次跳过 LLM 抽取")

        merged, conflicts, conflict_records = _merge_without_conflicts([*direct_terms, *text_terms])
        accepted: list[_TermCandidate] = []
        skipped_existing = 0
        for candidate in merged:
            term = candidate.entry
            canonical = _lookup_effective_term(self._term_manager, term.term)
            if canonical is not None:
                if _normalized_translation(canonical.translation) == _normalized_translation(term.translation):
                    skipped_existing += 1
                    continue
                conflicts += 1
                conflict_records.extend(
                    _library_conflict_records(
                        candidate,
                        canonical,
                    )
                )
                continue
            # Transitional compatibility until TermDatabaseManager implements
            # resolve_term(term) -> TermEntry | None.
            if self._term_manager.has_term(term.term):
                skipped_existing += 1
                continue
            accepted.append(candidate)

        if accepted:
            dynamic_db.add_many_and_save([
                (candidate.entry.term, candidate.entry.translation, candidate.entry.source, candidate.entry.context)
                for candidate in accepted
            ])

        return ExistingTermSeedResult(
            direct_added=sum(candidate.entry.source == EXISTING_NAME_SOURCE for candidate in accepted),
            text_added=sum(candidate.entry.source == EXISTING_TEXT_SOURCE for candidate in accepted),
            conflicts=conflicts,
            skipped_existing=skipped_existing,
            text_extraction_attempted=text_extraction_attempted,
            text_batches_completed=text_batches_completed,
            text_batches_total=text_batches_total,
            error=str(extraction_error) if extraction_error is not None else None,
            conflict_records=tuple(conflict_records),
        )

    @staticmethod
    def _extract_direct_terms(entries: list[TranslationEntry]) -> list[_TermCandidate]:
        terms: list[_TermCandidate] = []
        for entry in entries:
            context = entry.context or ""
            context_base = context.split("|", 1)[0]
            if context_base not in AUTO_TERM_CONTEXTS:
                continue
            terms.append(
                _TermCandidate(
                    entry=TermEntry(
                        term=entry.original.strip(),
                        translation=entry.translation.strip(),
                        source=EXISTING_NAME_SOURCE,
                        context=context,
                    ),
                    entry_keys=(_entry_identity(entry),),
                )
            )
        return terms

    @staticmethod
    def _text_pairs(entries: list[TranslationEntry]) -> list[_ExistingTextPair]:
        return [
            _ExistingTextPair(
                entry_key=_entry_identity(entry),
                original=entry.original,
                translation=entry.translation,
            )
            for entry in entries
            if (entry.context or "").split("|", 1)[0] not in AUTO_TERM_CONTEXTS
        ]

    def _extract_text_terms(
        self,
        batches: list[ContentBatch[_ExistingTextPair]],
        *,
        progress_callback: Callable[[int, int, str], None] | None,
        log_callback: Callable[[str], None] | None,
        stop_event: threading.Event | None,
        pause_event: threading.Event | None,
    ) -> tuple[list[_TermCandidate], int, bool, Exception | None]:
        if not batches:
            return [], 0, False, None

        total = len(batches)
        completed = 0
        results: dict[int, list[_TermCandidate]] = {}
        next_batch = 0
        futures: dict[Future[list[_TermCandidate]], ContentBatch[_ExistingTextPair]] = {}
        cancelled = False
        extraction_error: Exception | None = None
        abort_event = threading.Event()

        def submit(executor: ThreadPoolExecutor, batch: ContentBatch[_ExistingTextPair]) -> None:
            _notify(
                progress_callback,
                completed,
                total,
                f"正在抽取第 {batch.index + 1}/{total} 批（{len(batch.items)} 条，{batch.content_tokens} Token）",
            )
            future = executor.submit(
                self._extract_one_batch,
                batch,
                stop_event=stop_event,
                pause_event=pause_event,
                abort_event=abort_event,
            )
            futures[future] = batch

        with ThreadPoolExecutor(max_workers=self._max_concurrent, thread_name_prefix="term-extract") as executor:
            while next_batch < total and len(futures) < self._max_concurrent:
                if _stop_requested(stop_event):
                    cancelled = True
                    break
                submit(executor, batches[next_batch])
                next_batch += 1

            while futures:
                done, _pending = wait(tuple(futures), timeout=0.1, return_when=FIRST_COMPLETED)
                if not done:
                    if _stop_requested(stop_event):
                        cancelled = True
                        break
                    continue

                for future in sorted(done, key=lambda item: futures[item].index):
                    batch = futures.pop(future)
                    try:
                        extracted = future.result()
                    except _ExtractionCancelled:
                        cancelled = True
                        break
                    except Exception as exc:
                        extraction_error = _BatchExtractionError(batch.index + 1, total, exc)
                        break
                    results[batch.index] = extracted
                    completed += 1
                    message = f"已完成术语抽取 {completed}/{total} 批，本批新增候选 {len(extracted)} 个"
                    _notify(progress_callback, completed, total, message)
                    if log_callback:
                        log_callback(message)

                if cancelled or extraction_error is not None or _stop_requested(stop_event):
                    cancelled = cancelled or _stop_requested(stop_event)
                    abort_event.set()
                    break

                while next_batch < total and len(futures) < self._max_concurrent:
                    submit(executor, batches[next_batch])
                    next_batch += 1

            if cancelled or extraction_error is not None:
                abort_event.set()
                for future in futures:
                    future.cancel()

        if cancelled:
            return [], completed, True, None
        if extraction_error is not None:
            return [], completed, False, extraction_error

        terms: list[_TermCandidate] = []
        for batch_index in range(total):
            terms.extend(results[batch_index])
        return terms, completed, False, None

    def _extract_one_batch(
        self,
        batch: ContentBatch[_ExistingTextPair],
        *,
        stop_event: threading.Event | None,
        pause_event: threading.Event | None,
        abort_event: threading.Event,
    ) -> list[_TermCandidate]:
        if not _wait_until_runnable(stop_event, pause_event, abort_event):
            raise _ExtractionCancelled
        extracted = self._noun_extractor.extract(
            [pair.as_payload() for pair in batch.items],
            raise_on_error=True,
        )
        candidates: list[_TermCandidate] = []
        for entry in extracted:
            entry_keys = tuple(
                pair.entry_key
                for pair in batch.items
                if entry.term in pair.original and entry.translation in pair.translation
            )
            if not entry_keys:
                continue
            candidates.append(
                _TermCandidate(
                    entry=TermEntry(
                        term=entry.term,
                        translation=entry.translation,
                        source=EXISTING_TEXT_SOURCE,
                    ),
                    entry_keys=entry_keys,
                )
            )
        return candidates


class _ExtractionCancelled(Exception):
    pass


class _BatchExtractionError(Exception):
    def __init__(self, batch_number: int, total_batches: int, cause: Exception) -> None:
        super().__init__(str(cause))
        self.batch_number = batch_number
        self.total_batches = total_batches
        self.__cause__ = cause


def _entry_identity(entry: TranslationEntry) -> EntryKey:
    identity = getattr(entry, "identity", None)
    if isinstance(identity, EntryKey):
        return identity
    return EntryKey(SourceNamespace.legacy(), str(entry.key))


def _stop_requested(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _wait_until_runnable(
    stop_event: threading.Event | None,
    pause_event: threading.Event | None,
    abort_event: threading.Event,
) -> bool:
    if _stop_requested(stop_event) or abort_event.is_set():
        return False
    if pause_event is None:
        return True
    while not pause_event.wait(timeout=0.1):
        if _stop_requested(stop_event) or abort_event.is_set():
            return False
    return not _stop_requested(stop_event) and not abort_event.is_set()


def _notify(
    callback: Callable[[int, int, str], None] | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback is not None:
        callback(current, total, message)


def _is_eligible_existing_entry(entry: TranslationEntry) -> bool:
    return bool(
        entry.original.strip() and entry.translation.strip() and entry.stage not in {STAGE_HIDDEN, STAGE_QUESTIONABLE}
    )


def _normalized_source(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _normalized_translation(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _merge_without_conflicts(
    candidates: list[_TermCandidate],
) -> tuple[list[_TermCandidate], int, list[TermConflictEvidence]]:
    grouped: dict[str, dict[str, _TermCandidate]] = {}
    for candidate in candidates:
        entry = candidate.entry
        term = entry.term.strip()
        translation = entry.translation.strip()
        if not term or not translation:
            continue
        source_key = _normalized_source(term)
        target_key = _normalized_translation(translation)
        translations = grouped.setdefault(source_key, {})
        existing = translations.get(target_key)
        if existing is None:
            translations[target_key] = candidate
        else:
            translations[target_key] = _TermCandidate(
                entry=existing.entry,
                entry_keys=_unique_keys((*existing.entry_keys, *candidate.entry_keys)),
            )

    merged: list[_TermCandidate] = []
    conflicts = 0
    records: list[TermConflictEvidence] = []
    for translations in grouped.values():
        if len(translations) != 1:
            conflicts += 1
            records.extend(_internal_conflict_records(tuple(translations.values())))
            continue
        merged.append(next(iter(translations.values())))
    return merged, conflicts, records


def _internal_conflict_records(candidates: tuple[_TermCandidate, ...]) -> list[TermConflictEvidence]:
    records: list[TermConflictEvidence] = []
    reference = candidates[0]
    alternative = candidates[1]
    for candidate in candidates:
        canonical = alternative if candidate is reference else reference
        records.extend(
            TermConflictEvidence(
                entry_key=entry_key,
                term=candidate.entry.term,
                observed_translation=candidate.entry.translation,
                canonical_translation=canonical.entry.translation,
                kind="candidate_internal",
                candidate_source=candidate.entry.source,
                candidate_context=candidate.entry.context,
                canonical_source=canonical.entry.source,
                canonical_context=canonical.entry.context,
            )
            for entry_key in candidate.entry_keys
        )
    return records


def _library_conflict_records(candidate: _TermCandidate, canonical: TermEntry) -> list[TermConflictEvidence]:
    return [
        TermConflictEvidence(
            entry_key=entry_key,
            term=candidate.entry.term,
            observed_translation=candidate.entry.translation,
            canonical_translation=canonical.translation,
            kind="effective_library",
            candidate_source=candidate.entry.source,
            candidate_context=candidate.entry.context,
            canonical_source=canonical.source,
            canonical_context=canonical.context,
        )
        for entry_key in candidate.entry_keys
    ]


def _lookup_effective_term(term_manager: TermDatabaseManager, term: str) -> TermEntry | None:
    lookup = getattr(term_manager, "resolve_term", None)
    if not callable(lookup):
        return None
    result = lookup(term)
    if result is not None and not isinstance(result, TermEntry):
        raise TypeError("resolve_term() must return TermEntry or None")
    return result


def _unique_keys(keys: tuple[EntryKey, ...]) -> tuple[EntryKey, ...]:
    return tuple(dict.fromkeys(keys))


__all__ = [
    "EXISTING_NAME_SOURCE",
    "EXISTING_TEXT_SOURCE",
    "ExistingTermSeedResult",
    "ExistingTermSeeder",
    "TermConflictEvidence",
    "should_seed_existing_terms",
]
