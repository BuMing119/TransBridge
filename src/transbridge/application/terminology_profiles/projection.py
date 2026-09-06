"""Pure, non-destructive projection of one common translation through a profile."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re

from .models import ProfileOccurrenceBinding, ProfileTermMapping, TerminologyProfileContent, normalize_term


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


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    end: int
    value: str
    term_key: str
    explicit: bool


@dataclass(frozen=True, slots=True)
class _ProfileIndexes:
    mappings: tuple[tuple[ProfileTermMapping, str], ...]
    source_buckets: dict[str, tuple[int, ...]]
    overrides: dict[str, str]
    bindings: dict[str, tuple[ProfileOccurrenceBinding, ...]]


@lru_cache(maxsize=64)
def _profile_indexes(content: TerminologyProfileContent) -> _ProfileIndexes:
    bindings: dict[str, list[ProfileOccurrenceBinding]] = {}
    for binding in content.bindings:
        bindings.setdefault(binding.entry_key, []).append(binding)
    mappings = tuple((mapping, normalize_term(mapping.original)) for mapping in content.mappings)
    buckets: dict[str, list[int]] = {}
    for index, (_mapping, normalized) in enumerate(mappings):
        buckets.setdefault(_source_bucket(normalized), []).append(index)
    return _ProfileIndexes(
        mappings,
        {key: tuple(values) for key, values in buckets.items()},
        {item.entry_key: item.translation for item in content.overrides},
        {key: tuple(values) for key, values in bindings.items()},
    )


class TerminologyProfileProjector:
    """Project text without mutating the authoritative common translation."""

    def project(
        self,
        *,
        entry_key: str,
        original: str,
        common_translation: str,
        content: TerminologyProfileContent,
        plugin_id: str | None = None,
    ) -> ProjectedTranslation:
        indexes = _profile_indexes(content)
        if entry_key in indexes.overrides:
            value = indexes.overrides[entry_key]
            return ProjectedTranslation(common_translation, value, used_override=True)

        plugin_originals = {
            normalized
            for item, normalized in indexes.mappings
            if item.scope_kind == "plugin" and item.plugin_id == plugin_id
        }
        active_mappings = tuple(
            (item, normalized)
            for item, normalized in indexes.mappings
            if (item.scope_kind == "plugin" and item.plugin_id == plugin_id)
            or (item.scope_kind == "project" and normalized not in plugin_originals)
        )
        mappings = {item.term_key: item for item, _normalized in active_mappings}
        diagnostics: list[ProjectionDiagnostic] = []
        replacements: list[_Replacement] = []
        bound_keys: set[str] = set()

        for binding in indexes.bindings.get(entry_key, ()):
            # The presence of an explicit binding is itself authoritative. If
            # it is stale or unmapped, do not fall through to heuristic
            # recognition and silently move the occurrence elsewhere.
            bound_keys.add(binding.term_key)
            mapping = mappings.get(binding.term_key)
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
            replacements.append(_Replacement(binding.start, binding.end, mapping.translation, binding.term_key, True))

        source_normalized = normalize_term(original)
        active_keys = set(mappings)
        for mapping_index in _source_candidate_indexes(indexes, source_normalized):
            mapping, original_normalized = indexes.mappings[mapping_index]
            if mapping.term_key not in active_keys:
                continue
            if not _normalized_source_contains(source_normalized, original_normalized):
                continue
            if mapping.term_key in bound_keys:
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
            if len(spans) != 1:
                diagnostics.append(
                    ProjectionDiagnostic(
                        "historical_translation_not_found" if not spans else "historical_translation_ambiguous",
                        (
                            "项目译文中找不到已登记的旧术语译名，已保留整条项目译文。"
                            if not spans
                            else "项目译文中的旧术语译名不是唯一位置，已保留整条项目译文。"
                        ),
                        entry_key,
                        mapping.term_key,
                    )
                )
                continue
            start, end = spans[0]
            replacements.append(_Replacement(start, end, mapping.translation, mapping.term_key, False))

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
    if not term_normalized:
        return False
    if re.fullmatch(r"[\w .'-]+", term_normalized, flags=re.ASCII):
        return re.search(rf"(?<![\w]){re.escape(term_normalized)}(?![\w])", source_normalized) is not None
    return term_normalized in source_normalized


def _source_bucket(term_normalized: str) -> str:
    token = re.search(r"[a-z0-9_]+", term_normalized, flags=re.ASCII)
    if token is not None:
        return f"ascii:{token.group()}"
    if not term_normalized or term_normalized[0].isascii():
        return "fallback"
    return f"unicode:{term_normalized[0]}"


def _source_candidate_indexes(indexes: _ProfileIndexes, source_normalized: str) -> tuple[int, ...]:
    bucket_keys = {f"ascii:{value}" for value in re.findall(r"[a-z0-9_]+", source_normalized, flags=re.ASCII)}
    bucket_keys.update(f"unicode:{value}" for value in source_normalized if not value.isascii())
    bucket_keys.add("fallback")
    candidates: set[int] = set()
    for key in bucket_keys:
        candidates.update(indexes.source_buckets.get(key, ()))
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
    "ProjectedTranslation",
    "ProjectionDiagnostic",
    "TerminologyProfileProjector",
    "source_contains",
]
