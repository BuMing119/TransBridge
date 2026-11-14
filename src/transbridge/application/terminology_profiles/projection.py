# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Pure, non-destructive projection of one common translation through a profile."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
from types import MappingProxyType

from .models import ProfileOccurrenceBinding, ProfileTermMapping, TerminologyProfileContent, normalize_term
from .occurrence_alignment import AlignmentResolver, checked_decision
from .occurrence_matching import analyze_occurrences, candidate_for


@dataclass(frozen=True, slots=True)
class ProjectionDiagnostic:
    code: str
    message: str
    entry_key: str
    term_key: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectedTranslation:
    common_translation: str
    translation: str
    changed_term_keys: tuple[str, ...] = ()
    used_override: bool = False
    diagnostics: tuple[ProjectionDiagnostic, ...] = ()

    @property
    def is_derived(self) -> bool:
        return self.translation != self.common_translation


class SourceTermIndex:
    """Bound source recognition to plausible terms for profile and base projections."""

    def __init__(self, originals: Iterable[str]) -> None:
        self._originals = tuple(originals)
        self._normalized = tuple(normalize_term(original) for original in self._originals)
        frequencies = Counter(token for value in self._normalized for token in set(_ascii_term_tokens(value)))
        buckets: dict[str, list[int]] = {}
        for index, normalized in enumerate(self._normalized):
            buckets.setdefault(_source_bucket(normalized, frequencies), []).append(index)
        self._buckets = {key: tuple(values) for key, values in buckets.items()}

    def matching_indexes(self, source: str) -> tuple[int, ...]:
        normalized = normalize_term(source)
        return tuple(
            index
            for index in _source_candidate_indexes(self._buckets, normalized)
            if _normalized_source_contains(normalized, self._normalized[index])
        )

    def matching_originals(self, source: str) -> tuple[str, ...]:
        return tuple(self._originals[index] for index in self.matching_indexes(source))


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    end: int
    value: str
    term_key: str
    explicit: bool


@dataclass(frozen=True, slots=True)
class _ProfileIndexes:
    originals: tuple[str, ...]
    source_index: SourceTermIndex
    by_key: Mapping[str, ProfileTermMapping]
    project: Mapping[str, ProfileTermMapping]
    plugins: Mapping[str, Mapping[str, ProfileTermMapping]]
    overrides: Mapping[str, str]
    bindings: Mapping[str, tuple[ProfileOccurrenceBinding, ...]]

    def resolve(self, normalized: str, plugin_id: str | None) -> ProfileTermMapping | None:
        plugin = self.plugins.get(plugin_id, {})
        return plugin.get(normalized) or self.project.get(normalized)

    def active_mapping(self, key: str, plugin_id: str | None) -> ProfileTermMapping | None:
        mapping = self.by_key.get(key)
        if mapping is None:
            return None
        selected = self.resolve(normalize_term(mapping.original), plugin_id)
        return mapping if selected is mapping else None

    def matching(self, original: str, plugin_id: str | None) -> tuple[ProfileTermMapping, ...]:
        matches = (
            self.resolve(self.originals[index], plugin_id) for index in self.source_index.matching_indexes(original)
        )
        return tuple(sorted((item for item in matches if item is not None), key=lambda item: item.term_key))


def _profile_indexes(content: TerminologyProfileContent) -> _ProfileIndexes:
    bindings: dict[str, list[ProfileOccurrenceBinding]] = {}
    for binding in content.bindings:
        bindings.setdefault(binding.entry_key, []).append(binding)
    project = {}
    plugins = {}
    originals = {}
    for mapping in content.mappings:
        normalized = normalize_term(mapping.original)
        originals.setdefault(normalized, mapping.original)
        scope = project if mapping.scope_kind == "project" else plugins.setdefault(mapping.plugin_id, {})
        scope[normalized] = mapping
    return _ProfileIndexes(
        tuple(originals),
        SourceTermIndex(originals.values()),
        MappingProxyType({mapping.term_key: mapping for mapping in content.mappings}),
        MappingProxyType(project),
        MappingProxyType({key: MappingProxyType(value) for key, value in plugins.items()}),
        MappingProxyType({item.entry_key: item.translation for item in content.overrides}),
        MappingProxyType({key: tuple(values) for key, values in bindings.items()}),
    )


class TerminologyProfileProjector:
    """Project text without mutating the authoritative common translation."""

    def __init__(self, *, resolver: AlignmentResolver | None = None) -> None:
        self._prepared: tuple[TerminologyProfileContent, PreparedProfileProjector] | None = None
        self._resolver = resolver

    def prepare(
        self,
        content: TerminologyProfileContent,
        *,
        ignore_missing: bool = False,
        target_term_keys: frozenset[str] = frozenset(),
    ) -> PreparedProfileProjector:
        cached = self._prepared
        if (
            cached is not None
            and cached[0] is content
            and cached[1]._ignore_missing == ignore_missing
            and cached[1]._target_term_keys == target_term_keys
        ):
            return cached[1]
        prepared = PreparedProfileProjector(_profile_indexes(content), ignore_missing, self._resolver, target_term_keys)
        # Publish the pair together; concurrent callers retain their own frozen result.
        self._prepared = (content, prepared)
        return prepared

    def project(
        self,
        *,
        entry_key: str,
        original: str,
        common_translation: str,
        content: TerminologyProfileContent,
        plugin_id: str | None = None,
    ) -> ProjectedTranslation:
        return self.prepare(content).project(
            entry_key=entry_key, original=original, common_translation=common_translation, plugin_id=plugin_id
        )


@dataclass(frozen=True, slots=True)
class PreparedProfileProjector:
    """A complete immutable index bound once, reusable across entries and threads."""

    _indexes: _ProfileIndexes
    _ignore_missing: bool = False
    _resolver: AlignmentResolver | None = None
    _target_term_keys: frozenset[str] = frozenset()

    def project(
        self, *, entry_key: str, original: str, common_translation: str, plugin_id: str | None = None
    ) -> ProjectedTranslation:
        indexes = self._indexes
        if entry_key in indexes.overrides:
            value = indexes.overrides[entry_key]
            return ProjectedTranslation(common_translation, value, used_override=True)

        diagnostics: list[ProjectionDiagnostic] = []
        replacements: list[_Replacement] = []
        bound_keys: set[str] = set()

        for binding in indexes.bindings.get(entry_key, ()):
            # The presence of an explicit binding is itself authoritative. If
            # it is stale or unmapped, do not fall through to heuristic
            # recognition and silently move the occurrence elsewhere.
            bound_keys.add(binding.term_key)
            mapping = indexes.active_mapping(binding.term_key, plugin_id)
            if mapping is None:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "binding_mapping_missing",
                        "受控术语位置在当前译名方案中没有对应译名，已保留项目译文。",
                        entry_key,
                        binding.term_key,
                    )
                )
                continue
            if common_translation[binding.start : binding.end] != binding.expected_text:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "binding_text_changed",
                        "项目译文已变化，受控术语位置需要重新确认。",
                        entry_key,
                        binding.term_key,
                    )
                )
                continue
            if mapping.translation == binding.expected_text:
                continue
            replacements.append(_Replacement(binding.start, binding.end, mapping.translation, binding.term_key, True))

        matches = indexes.matching(original, plugin_id)
        candidates = []
        for mapping in matches:
            if mapping.term_key in bound_keys:
                continue
            if mapping.translation == mapping.base_translation:
                continue
            if not mapping.base_translation:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "historical_translation_missing",
                        "术语映射未填写当前译文中的叫法，无法安全定位，已保留整条项目译文。",
                        entry_key,
                        mapping.term_key,
                    )
                )
                continue
            spans = _literal_spans(common_translation, mapping.base_translation)
            if not spans:
                if self._ignore_missing or mapping.translation in common_translation:
                    continue
                diagnostics.append(
                    ProjectionDiagnostic(
                        "historical_translation_not_found",
                        "项目译文中找不到已登记的旧术语译名，已保留整条项目译文。",
                        entry_key,
                        mapping.term_key,
                    )
                )
                continue
            candidates.extend(candidate_for(mapping, common_translation, start, end) for start, end in spans)

        if candidates and not diagnostics:
            candidates = [
                candidate
                for candidate in candidates
                if not any(
                    candidate.start < explicit.end and explicit.start < candidate.end for explicit in replacements
                )
            ]
            analysis = analyze_occurrences(
                entry_key, original, common_translation, plugin_id, matches, candidates, self._target_term_keys
            )
            selected = analysis.automatic
            if selected is None:
                decision = None if self._resolver is None else self._resolver.resolve(analysis.request)
                if decision is not None:
                    decision = checked_decision(decision, analysis.request)
                if decision is not None and decision.certain:
                    selected = decision.selected_ids
                else:
                    diagnostics.append(
                        ProjectionDiagnostic(
                            "occurrence_alignment_required",
                            analysis.reason if decision is None else f"位置判断未确定：{decision.rationale}",
                            entry_key,
                        )
                    )
            for index in selected or ():
                candidate = analysis.request.candidates[index]
                replacements.append(
                    _Replacement(candidate.start, candidate.end, candidate.translation, candidate.term_key, False)
                )

        accepted, rejected = _without_overlaps(replacements)
        for item in rejected:
            diagnostics.append(
                ProjectionDiagnostic(
                    "replacement_overlap",
                    "多个术语候选位置重叠，未自动替换。",
                    entry_key,
                    item.term_key,
                )
            )
        if diagnostics:
            return ProjectedTranslation(common_translation, common_translation, diagnostics=tuple(diagnostics))
        result = common_translation
        changed: list[str] = []
        for item in sorted(accepted, key=lambda candidate: candidate.start, reverse=True):
            current = result[item.start : item.end]
            if current != item.value:
                result = result[: item.start] + item.value + result[item.end :]
                changed.append(item.term_key)
        return ProjectedTranslation(
            common_translation,
            result,
            tuple(sorted(set(changed))),
            diagnostics=tuple(diagnostics),
        )


def source_contains(source: str, term: str) -> bool:
    return _normalized_source_contains(normalize_term(source), normalize_term(term))


def _normalized_source_contains(source_normalized: str, term_normalized: str) -> bool:
    # Reject absent literals before compiling a term-specific boundary pattern;
    # large profiles otherwise churn Python's bounded regular-expression cache.
    if not term_normalized or term_normalized not in source_normalized:
        return False
    if re.fullmatch(r"[\w .'-]+", term_normalized, flags=re.ASCII):
        return re.search(rf"(?<![\w]){re.escape(term_normalized)}(?![\w])", source_normalized) is not None
    return term_normalized in source_normalized


def _ascii_term_tokens(term_normalized: str) -> tuple[str, ...]:
    if re.fullmatch(r"[\w .'-]+", term_normalized, flags=re.ASCII) is None:
        return ()
    return tuple(re.findall(r"[a-z0-9_]+", term_normalized, flags=re.ASCII))


def _source_bucket(term_normalized: str, frequencies: Mapping[str, int]) -> str:
    if re.fullmatch(r"[\w .'-]+", term_normalized, flags=re.ASCII) is None:
        # These terms use literal substring recognition, so an ASCII fragment
        # may sit inside a larger token (e.g. C++ in XC++). A Unicode character
        # bucket is safe; otherwise retain the term in the fallback candidates.
        character = next((value for value in term_normalized if not value.isascii()), None)
        return "fallback" if character is None else f"unicode:{character}"
    tokens = _ascii_term_tokens(term_normalized)
    if tokens:
        # Every matching source must include every token. Choose the rarest
        # token so large families such as "Term 000001" do not share one bucket.
        token = min(tokens, key=lambda value: frequencies[value])
        return f"ascii:{token}"
    if not term_normalized or term_normalized[0].isascii():
        return "fallback"
    return f"unicode:{term_normalized[0]}"


def _source_candidate_indexes(buckets: Mapping[str, tuple[int, ...]], source_normalized: str) -> tuple[int, ...]:
    bucket_keys = {f"ascii:{value}" for value in re.findall(r"[a-z0-9_]+", source_normalized, flags=re.ASCII)}
    bucket_keys.update(f"unicode:{value}" for value in source_normalized if not value.isascii())
    bucket_keys.add("fallback")
    candidates: set[int] = set()
    for key in bucket_keys:
        candidates.update(buckets.get(key, ()))
    return tuple(sorted(candidates))


def _literal_spans(text: str, needle: str) -> tuple[tuple[int, int], ...]:
    if not needle:
        return ()
    return tuple((match.start(), match.end()) for match in re.finditer(re.escape(needle), text))


def _without_overlaps(replacements: list[_Replacement]) -> tuple[tuple[_Replacement, ...], tuple[_Replacement, ...]]:
    accepted: list[_Replacement] = []
    rejected: list[_Replacement] = []
    ordered = sorted(replacements, key=lambda item: (item.start, item.end, not item.explicit, item.term_key))
    for item in ordered:
        conflicts = [other for other in accepted if item.start < other.end and other.start < item.end]
        if not conflicts:
            accepted.append(item)
            continue
        # An explicit binding is stronger than historical recognition.  Two
        # explicit bindings are both rejected because the stored state is inconsistent.
        if item.explicit and all(not other.explicit for other in conflicts):
            for other in conflicts:
                accepted.remove(other)
                rejected.append(other)
            accepted.append(item)
        elif not item.explicit and any(other.explicit for other in conflicts):
            rejected.append(item)
        else:
            # Competing candidates with the same confidence are ambiguous as
            # a group.  Reject all of them instead of depending on sort order.
            rejected.append(item)
            for other in conflicts:
                if other in accepted:
                    accepted.remove(other)
                    rejected.append(other)
    return tuple(accepted), tuple(rejected)


__all__ = [
    "PreparedProfileProjector",
    "ProjectedTranslation",
    "ProjectionDiagnostic",
    "SourceTermIndex",
    "TerminologyProfileProjector",
    "source_contains",
]
