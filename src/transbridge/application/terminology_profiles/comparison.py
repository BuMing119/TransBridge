# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compare immutable translations using the same projection as plugin export."""

from dataclasses import dataclass
from datetime import UTC, datetime

from transbridge.application.io.operation_write import _translation_entries
from transbridge.application.terminology_profiles.entry_projection import ProfileEntryProjector


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    source: str
    entry_key: str
    original: str
    current: str
    translations: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class SchemeComparison:
    schemes: tuple
    items: tuple[ComparisonRow, ...]
    total_count: int
    created_at: str
    warning_count: int = 0
    kind: str = "scheme_comparison"


def compare_scheme_translations(sources, projections, *, cancellation=None):
    projections = tuple(projections)
    if len(projections) < 2:
        raise ValueError("请至少勾选两个术语方案进行比较。")
    projectors = tuple(
        ProfileEntryProjector(
            item.profile, item.base_snapshot, source_profile=item.source_profile, projector=item.projector
        )
        for item in projections
    )
    items, total, warning_count = [], 0, 0
    for source in sources:
        for entry in _translation_entries(source.entries):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            total += 1
            results = tuple(projector.project(entry) for projector in projectors)
            translations = tuple(result.translation for result in results)
            notes = tuple(
                f"{projection.profile.name}：{diagnostic.message}"
                for projection, result in zip(projections, results, strict=True)
                for diagnostic in result.diagnostics
            )
            warning_count += bool(notes)
            if len(set(translations)) < 2:
                continue
            items.append(
                ComparisonRow(
                    source.source_snapshot.source.uri,
                    entry.identity.serialize(),
                    entry.original,
                    entry.translation,
                    translations,
                    "\n".join(notes),
                )
            )
    return SchemeComparison(
        tuple(item.profile for item in projections), tuple(items), total, datetime.now(UTC).isoformat(), warning_count
    )
