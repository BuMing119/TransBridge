# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Snapshot a terminology source into a complete naming-scheme profile."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.identity import canonical_digest, normalize_original
from transbridge.application.terminology.models import TermDecision

from .models import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
    logical_term_key,
)
from .publication import ProfilePublicationPreview
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
    scope_kind: str = "project"
    plugin_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "original", _required(self.original, "source original"))
        object.__setattr__(self, "translation", _required(self.translation, "source translation"))
        mapping = ProfileTermMapping(
            self.original, self.translation, scope_kind=self.scope_kind, plugin_id=self.plugin_id
        )
        object.__setattr__(self, "scope_kind", mapping.scope_kind)
        object.__setattr__(self, "plugin_id", mapping.plugin_id)

    @property
    def term_key(self) -> str:
        return logical_term_key(self.original, scope_kind=self.scope_kind, plugin_id=self.plugin_id)


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
        # Source order resolves duplicate translations under the selected batch
        # policy, so it is part of the immutable decision evidence.
        payload = tuple((item.term_key, item.original, item.translation) for item in captured)
        digest = canonical_digest(payload, namespace="transbridge.terminology-source-snapshot.v2")
        return cls(source_id, source_label, captured, digest)


class TerminologyProfileImportPolicy(StrEnum):
    KEEP_EXISTING = "keep_existing"
    USE_IMPORTED = "use_imported"


class TerminologyProfileImportConflictKind(StrEnum):
    SOURCE_TRANSLATIONS = "source_translations"
    BASE_SCOPES = "base_scopes"
    EXISTING_TRANSLATION = "existing_translation"


@dataclass(frozen=True, order=True, slots=True)
class TerminologyProfileImportConflict:
    normalized_original: str
    original: str
    kind: TerminologyProfileImportConflictKind
    source_translations: tuple[str, ...]
    base_scopes: tuple[str, ...]
    scope_kind: str = "project"
    plugin_id: str | None = None

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

    @property
    def term_key(self) -> str:
        return logical_term_key(self.original, scope_kind=self.scope_kind, plugin_id=self.plugin_id)


@dataclass(frozen=True, slots=True)
class TerminologyProfileImportPreview:
    project_id: str
    variant_id: str
    base_version_id: str | None
    base_content_digest: str | None
    source: TerminologySourceSnapshot
    content: TerminologyProfileContent
    source_entry_count: int
    source_term_count: int
    duplicate_entry_count: int
    matched_term_count: int
    changed_mapping_count: int
    source_only_term_count: int
    conflicts: tuple[TerminologyProfileImportConflict, ...] = ()
    policy: TerminologyProfileImportPolicy = TerminologyProfileImportPolicy.KEEP_EXISTING
    adopted_mapping_count: int = 0
    kept_mapping_count: int = 0
    target_publication: ProfilePublicationPreview | None = None

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        for name in ("base_version_id", "base_content_digest"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        if (self.base_version_id is None) != (self.base_content_digest is None):
            raise ValueError("import preview base version and digest must both be present or absent")
        object.__setattr__(self, "policy", TerminologyProfileImportPolicy(self.policy))
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
            "adopted_mapping_count",
            "kept_mapping_count",
        ):
            _count(getattr(self, name), name.replace("_", " "))
        object.__setattr__(self, "conflicts", tuple(sorted(self.conflicts)))

    @property
    def base_mapping_count(self) -> int:
        return len(self.content.mappings)

    @property
    def mapping_count(self) -> int:
        return len(self.content.mappings)

    @property
    def overwritten_mapping_count(self) -> int:
        return sum(
            bool(item.base_translation) and item.translation != item.base_translation for item in self.content.mappings
        )

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
        *,
        policy: TerminologyProfileImportPolicy = TerminologyProfileImportPolicy.KEEP_EXISTING,
        existing_mappings: tuple[ProfileTermMapping, ...] | None = None,
    ) -> TerminologyProfileImportPreview:
        project_id = _required(project_id, "project ID")
        variant_id = _required(variant_id, "variant ID")
        if (base_snapshot.local_project_id, base_snapshot.local_variant_id) != (project_id, variant_id):
            raise TerminologyProfileImportError("base terminology snapshot belongs to another Project/Variant")
        if base_snapshot.status not in {EffectiveSnapshotStatus.READY, EffectiveSnapshotStatus.NO_PROJECT_VERSION}:
            raise TerminologyProfileImportError(
                "base terminology snapshot must be ready or unpublished before importing"
            )
        if not source.entries:
            raise TerminologyProfileImportError("terminology source snapshot is empty")

        base_decisions = tuple(item for item in base_snapshot.decisions if item.is_effective)
        policy = TerminologyProfileImportPolicy(policy)
        base_by_key: dict[str, TermDecision] = {}
        for decision in base_decisions:
            key = logical_term_key(
                decision.original, scope_kind=decision.scope.kind.value, plugin_id=decision.scope.plugin_id
            )
            base_by_key[key] = decision

        if existing_mappings is not None:
            from transbridge.application.terminology.models import DecisionStatus, TermScope

            base_by_key = {
                item.term_key: TermDecision(
                    item.term_key,
                    project_id,
                    variant_id,
                    item.original,
                    normalize_original(item.original),
                    item.translation,
                    TermScope.project() if item.plugin_id is None else TermScope.plugin(item.plugin_id),
                    DecisionStatus.ADOPTED,
                )
                for item in existing_mappings
            }

        source_by_key: dict[str, list[TerminologySourceEntry]] = defaultdict(list)
        for entry in source.entries:
            source_by_key[entry.term_key].append(entry)

        duplicate_count = sum(
            len(entries) - len({item.translation for item in entries}) for entries in source_by_key.values()
        )
        matched_keys = set(base_by_key) & set(source_by_key)
        conflicts: list[TerminologyProfileImportConflict] = []
        mappings = []
        adopted = 0
        for key in sorted(base_by_key.keys() | source_by_key.keys()):
            decision = base_by_key.get(key)
            entries = source_by_key.get(key, ())
            entry = None if not entries else entries[-1 if policy is TerminologyProfileImportPolicy.USE_IMPORTED else 0]
            different = decision is not None and entry is not None and decision.translation != entry.translation
            use_source = entry is not None and (
                decision is None or policy is TerminologyProfileImportPolicy.USE_IMPORTED
            )
            if entry is not None:
                translations = tuple(sorted({item.translation for item in entries}))
                duplicate_conflict = len(translations) > 1
                if duplicate_conflict or different:
                    conflicts.append(
                        TerminologyProfileImportConflict(
                            normalize_original(entry.original),
                            entry.original,
                            TerminologyProfileImportConflictKind.SOURCE_TRANSLATIONS
                            if duplicate_conflict
                            else TerminologyProfileImportConflictKind.EXISTING_TRANSLATION,
                            translations,
                            () if decision is None else (decision.scope.canonical_key,),
                            entry.scope_kind,
                            entry.plugin_id,
                        )
                    )
            if decision is not None:
                mappings.append(
                    ProfileTermMapping(
                        decision.original,
                        entry.translation if use_source else decision.translation,
                        decision.translation,
                        decision.scope.kind.value,
                        decision.scope.plugin_id,
                    )
                )
            else:
                assert entry is not None
                mappings.append(
                    ProfileTermMapping(
                        entry.original,
                        entry.translation,
                        scope_kind=entry.scope_kind,
                        plugin_id=entry.plugin_id,
                        scheme_owned=True,
                    )
                )
            adopted += int(entry is not None and (decision is None or (different and use_source)))
        changed_count = sum(item.translation != item.base_translation for item in mappings)
        source_only = set(source_by_key) - set(base_by_key)
        return TerminologyProfileImportPreview(
            project_id=project_id,
            variant_id=variant_id,
            base_version_id=base_snapshot.version_id,
            base_content_digest=base_snapshot.content_digest,
            source=source,
            content=TerminologyProfileContent(mappings=mappings),
            source_entry_count=len(source.entries),
            source_term_count=len(source_by_key),
            duplicate_entry_count=duplicate_count,
            matched_term_count=len(matched_keys),
            changed_mapping_count=changed_count,
            source_only_term_count=len(source_only),
            conflicts=tuple(conflicts),
            policy=policy,
            adopted_mapping_count=adopted,
            kept_mapping_count=len(mappings) - adopted,
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
    "TerminologyProfileImportPolicy",
    "TerminologyProfileImportResult",
    "TerminologyProfileImportService",
    "TerminologySourceEntry",
    "TerminologySourceSnapshot",
]
