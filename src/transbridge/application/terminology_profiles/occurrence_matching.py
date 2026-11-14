# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Count and interval evidence for fast terminology substitution, without entity classes."""

from dataclasses import dataclass
import re

from .models import normalize_term
from .occurrence_alignment import AlignmentCandidate, AlignmentRequest, TermOccurrenceCount


def source_spans(source: str, term: str) -> tuple[tuple[int, int], ...]:
    """Coordinates refer to normalized source, used only for source-side relationships."""
    source, term = normalize_term(source), normalize_term(term)
    if not term:
        return ()
    pattern = re.escape(term)
    if re.fullmatch(r"[\w .'-]+", term, flags=re.ASCII):
        pattern = rf"(?<![\w]){pattern}(?![\w])"
    return tuple(match.span() for match in re.finditer(pattern, source))


def literal_spans(text, needle):
    return tuple(match.span() for match in re.finditer(re.escape(needle), text)) if needle else ()


def contains(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


@dataclass(frozen=True, slots=True)
class OccurrenceAnalysis:
    request: AlignmentRequest
    automatic: tuple[int, ...] | None
    reason: str = ""


def analyze_occurrences(entry_key, original, text, plugin_id, mappings, candidates, target_term_keys=frozenset()):
    """Counts prioritize work; matching counts are a heuristic, not semantic proof."""
    by_key = {mapping.term_key: mapping for mapping in mappings}
    source = {key: source_spans(original, mapping.original) for key, mapping in by_key.items()}
    old = {key: literal_spans(text, mapping.base_translation) for key, mapping in by_key.items()}
    target = {key: literal_spans(text, mapping.translation) for key, mapping in by_key.items()}
    # A complete changed mapping owns an exact whole-entry source/translation pair.
    # Base-filled no-op names are context, never authority to freeze their components.
    exact = [
        mapping
        for mapping in mappings
        if (mapping.base_translation != mapping.translation or mapping.term_key in target_term_keys)
        and normalize_term(original) == normalize_term(mapping.original)
        and text in (mapping.base_translation, mapping.translation)
    ]
    if len(exact) == 1:
        mapping = exact[0]
        chosen = (
            tuple(
                candidate
                for candidate in candidates
                if candidate.term_key == mapping.term_key and candidate.start == 0 and candidate.end == len(text)
            )
            if text != mapping.translation
            else ()
        )
        request = AlignmentRequest(entry_key, original, text, plugin_id, chosen, ())
        return OccurrenceAnalysis(request, tuple(range(len(chosen))))

    completed = {
        key: spans
        for key, spans in target.items()
        if spans
        and len(spans) == len(source[key])
        and (by_key[key].translation != by_key[key].base_translation or key in target_term_keys)
        and all(any(contains(outer, inner) for outer in spans) for inner in old[key])
    }

    owners = {}

    def completed_owners(key):
        if key in owners:
            return owners[key]
        owners[key] = tuple(
            other_key
            for other_key in completed
            if key != other_key
            and any(contains(outer, inner) for outer in completed[other_key] for inner in old[key] + target[key])
            and source_spans(by_key[other_key].original, by_key[key].original)
        )
        return owners[key]

    def completed_container(key, span):
        return any(contains(outer, span) for owner in completed_owners(key) for outer in completed[owner])

    def unrelated_container(key, span):
        short = by_key[key]
        for other_key, other in by_key.items():
            if other_key == key:
                continue
            # Only balanced complete long-name evidence can exclude a substring.
            long_spans = tuple(set(old[other_key] + target[other_key]))
            if len(long_spans) != len(source[other_key]):
                continue
            if any(outer != span and contains(outer, span) for outer in long_spans) and not source_spans(
                other.original, short.original
            ):
                return True
        return False

    retained = []
    for candidate in candidates:
        span = candidate.start, candidate.end
        if any(outer != span and contains(outer, span) for outer in target[candidate.term_key]):
            continue  # The same term already has its longer target spelling.
        if unrelated_container(candidate.term_key, span):
            continue
        if completed_container(candidate.term_key, span):
            continue
        retained.append(candidate)

    counts = []
    for key in dict.fromkeys(candidate.term_key for candidate in candidates):
        mapping = by_key[key]
        independent_targets = tuple(
            span
            for span in target[key]
            if not any(outer != span and contains(outer, span) for outer in old[key])
            and not unrelated_container(key, span)
            and not completed_container(key, span)
        )
        remaining_source = tuple(
            span
            for span in source[key]
            if not any(contains(outer, span) for owner in completed_owners(key) for outer in source[owner])
        )
        counts.append(
            TermOccurrenceCount(
                key,
                mapping.original,
                len(remaining_source),
                sum(candidate.term_key == key for candidate in retained),
                len(independent_targets),
            )
        )
    request = AlignmentRequest(entry_key, original, text, plugin_id, tuple(retained), tuple(counts))
    if not retained:
        return OccurrenceAnalysis(request, ())
    mismatch = [count for count in counts if count.source_count != count.old_count + count.target_count]
    ordered = sorted(retained, key=lambda item: (item.start, item.end))
    overlap = any(left.end > right.start for left, right in zip(ordered, ordered[1:]))
    shared = any(
        (candidate.start, candidate.end) in old[other_key] + target[other_key]
        for candidate in retained
        for other_key in by_key
        if other_key != candidate.term_key
    )
    if mismatch or overlap or shared:
        detail = "；".join(
            f"{count.original}：原文 {count.source_count} 处，旧译名 {count.old_count} 处，"
            f"目标译名 {count.target_count} 处"
            for count in mismatch
        )
        if overlap:
            detail = f"{detail}；候选位置重叠" if detail else "候选位置重叠"
        if shared:
            detail = f"{detail}；同一区间对应多个术语" if detail else "同一区间对应多个术语"
        return OccurrenceAnalysis(request, None, f"{detail}。已保留整条译文，可使用 AI 定位。")
    return OccurrenceAnalysis(request, tuple(range(len(retained))))


def candidate_for(mapping, text, start, end):
    return AlignmentCandidate(start, end, text[start:end], mapping.translation, mapping.term_key, mapping.original)
