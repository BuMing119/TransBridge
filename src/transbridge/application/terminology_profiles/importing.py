"""Snapshot a terminology source into a complete naming-scheme profile."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import canonical_digest, normalize_original, normalize_translation
from transbridge.application.terminology.models import TermDecision

from .models import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)
from .service import TerminologyProfileService


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _count(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True, order=True, slots=True)
class TerminologySourceEntry:
    """Application-neutral source row; no AI loader or UI type crosses this boundary."""

    original: str
    translation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "original", _required(self.original, "source original"))
        object.__setattr__(self, "translation", _required(self.translation, "source translation"))


@dataclass(frozen=True, slots=True)
class TerminologySourceSnapshot:
    """Immutable one-time capture of one configured terminology source."""

    source_id: str
    source_label: str
    entries: tuple[TerminologySourceEntry, ...]
    source_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _required(self.source_id, "source ID"))
        object.__setattr__(self, "source_label", _required(self.source_label, "source label"))
        entries = tuple(self.entries)
        if any(not isinstance(item, TerminologySourceEntry) for item in entries):
            raise TypeError("source entries must be TerminologySourceEntry values")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "source_digest", _required(self.source_digest, "source digest"))

    @classmethod
    def capture(
        cls,
        source_id: str,
        source_label: str,
        entries: Iterable[TerminologySourceEntry],
    ) -> TerminologySourceSnapshot:
        captured = tuple(entries)
        payload = tuple(
            sorted((normalize_original(item.original), normalize_translation(item.translation)) for item in captured)
        )
        digest = canonical_digest(payload, namespace="transbridge.terminology-source-snapshot.v1")
        return cls(source_id, source_label, captured, digest)


class TerminologyProfileImportConflictKind(StrEnum):
    SOURCE_TRANSLATIONS = "source_translations"
    BASE_SCOPES = "base_scopes"


@dataclass(frozen=True, order=True, slots=True)
class TerminologyProfileImportConflict:
    normalized_original: str
    original: str
    kind: TerminologyProfileImportConflictKind
    source_translations: tuple[str, ...]
    base_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_original", _required(self.normalized_original, "normalized original"))
        object.__setattr__(self, "original", _required(self.original, "original"))
        object.__setattr__(self, "kind", TerminologyProfileImportConflictKind(self.kind))
        source_translations = tuple(
            sorted({_required(item, "source translation") for item in self.source_translations})
        )
        base_scopes = tuple(sorted({_required(item, "base scope") for item in self.base_scopes}))
        object.__setattr__(self, "source_translations", source_translations)
        object.__setattr__(self, "base_scopes", base_scopes)


@dataclass(frozen=True, slots=True)
class TerminologyProfileImportPreview:
    project_id: str
    variant_id: str
    base_version_id: str
    base_content_digest: str
    source: TerminologySourceSnapshot
    content: TerminologyProfileContent
    source_entry_count: int
    source_term_count: int
    duplicate_entry_count: int
    matched_term_count: int
    changed_mapping_count: int
    source_only_term_count: int
    conflicts: tuple[TerminologyProfileImportConflict, ...] = ()

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id", "base_version_id", "base_content_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        if not isinstance(self.source, TerminologySourceSnapshot):
            raise TypeError("preview source must be a TerminologySourceSnapshot")
        if not isinstance(self.content, TerminologyProfileContent):
            raise TypeError("preview content must be TerminologyProfileContent")
        for name in (
            "source_entry_count",
            "source_term_count",
            "duplicate_entry_count",
            "matched_term_count",
            "changed_mapping_count",
            "source_only_term_count",
        ):
            _count(getattr(self, name), name.replace("_", " "))
        object.__setattr__(self, "conflicts", tuple(sorted(self.conflicts)))

    @property
    def base_mapping_count(self) -> int:
        return len(self.content.mappings)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)


@dataclass(frozen=True, slots=True)
class TerminologyProfileImportResult:
    profile: TerminologyProfile
    published: PublishedTerminologyProfile
    selection: TerminologyProfileSelection | None = None


class TerminologyProfileImportError(ValueError):
    pass


class TerminologyProfileImportService:
    def __init__(self, profiles: TerminologyProfileService) -> None:
        self._profiles = profiles

    def preview(
        self,
        project_id: str,
        variant_id: str,
        base_snapshot: EffectiveTerminologySnapshot,
        source: TerminologySourceSnapshot,
    ) -> TerminologyProfileImportPreview:
        project_id = _required(project_id, "project ID")
        variant_id = _required(variant_id, "variant ID")
        if (base_snapshot.local_project_id, base_snapshot.local_variant_id) != (project_id, variant_id):
            raise TerminologyProfileImportError("base terminology snapshot belongs to another Project/Variant")
        if base_snapshot.status is not EffectiveSnapshotStatus.READY:
            raise TerminologyProfileImportError("base terminology snapshot must be ready before importing a scheme")
        if not source.entries:
            raise TerminologyProfileImportError("terminology source snapshot is empty")

        base_decisions = tuple(item for item in base_snapshot.decisions if item.is_effective)
        if not base_decisions:
            raise TerminologyProfileImportError("base terminology snapshot contains no effective decisions")

        base_by_original: dict[str, list[TermDecision]] = defaultdict(list)
        for decision in base_decisions:
            base_by_original[normalize_original(decision.original)].append(decision)

        source_by_original: dict[str, list[TerminologySourceEntry]] = defaultdict(list)
        for entry in source.entries:
            source_by_original[normalize_original(entry.original)].append(entry)

        duplicate_count = sum(
            len(entries) - len({normalize_translation(item.translation) for item in entries})
            for entries in source_by_original.values()
        )
        matched_originals = set(base_by_original) & set(source_by_original)
        conflicts: list[TerminologyProfileImportConflict] = []
        targets: dict[str, str] = {}

        for normalized in sorted(matched_originals):
            base_group = base_by_original[normalized]
            translations_by_normalized: dict[str, list[str]] = defaultdict(list)
            for item in source_by_original[normalized]:
                translations_by_normalized[normalize_translation(item.translation)].append(item.translation)
            normalized_translations = tuple(sorted(translations_by_normalized))
            source_translations = tuple(
                sorted({value for values in translations_by_normalized.values() for value in values})
            )
            original = sorted((item.original for item in base_group), key=str.casefold)[0]
            scopes = tuple(item.scope.canonical_key for item in base_group)
            if len(normalized_translations) > 1:
                conflicts.append(
                    TerminologyProfileImportConflict(
                        normalized,
                        original,
                        TerminologyProfileImportConflictKind.SOURCE_TRANSLATIONS,
                        source_translations,
                        scopes,
                    )
                )
            elif len(base_group) > 1:
                conflicts.append(
                    TerminologyProfileImportConflict(
                        normalized,
                        original,
                        TerminologyProfileImportConflictKind.BASE_SCOPES,
                        source_translations,
                        scopes,
                    )
                )
            else:
                targets[normalized] = sorted(translations_by_normalized[normalized_translations[0]])[0]

        mappings = tuple(
            ProfileTermMapping(
                decision.original,
                targets.get(normalize_original(decision.original), decision.translation),
                decision.translation,
                decision.scope.kind.value,
                decision.scope.plugin_id,
            )
            for decision in base_decisions
        )
        changed_count = sum(
            normalize_translation(item.translation) != normalize_translation(item.base_translation) for item in mappings
        )
        source_only = set(source_by_original) - set(base_by_original)
        return TerminologyProfileImportPreview(
            project_id=project_id,
            variant_id=variant_id,
            base_version_id=base_snapshot.version_id or "",
            base_content_digest=base_snapshot.content_digest or "",
            source=source,
            content=TerminologyProfileContent(mappings=mappings),
            source_entry_count=len(source.entries),
            source_term_count=len(source_by_original),
            duplicate_entry_count=duplicate_count,
            matched_term_count=len(matched_originals),
            changed_mapping_count=changed_count,
            source_only_term_count=len(source_only),
            conflicts=tuple(conflicts),
        )

    def create_and_publish(
        self,
        project_id: str,
        variant_id: str,
        name: str,
        preview: TerminologyProfileImportPreview,
        *,
        select: bool = False,
    ) -> TerminologyProfileImportResult:
        project_id = _required(project_id, "project ID")
        variant_id = _required(variant_id, "variant ID")
        if (preview.project_id, preview.variant_id) != (project_id, variant_id):
            raise TerminologyProfileImportError("import preview belongs to another Project/Variant")
        profile = self._profiles.create_with_content(project_id, name, preview.content)
        published = self._profiles.publish(profile.profile_id, expected_draft_revision=profile.draft_revision)
        selection = self._profiles.select(project_id, variant_id, profile.profile_id) if select else None
        return TerminologyProfileImportResult(profile, published, selection)


__all__ = [
    "TerminologyProfileImportConflict",
    "TerminologyProfileImportConflictKind",
    "TerminologyProfileImportError",
    "TerminologyProfileImportPreview",
    "TerminologyProfileImportResult",
    "TerminologyProfileImportService",
    "TerminologySourceEntry",
    "TerminologySourceSnapshot",
]
