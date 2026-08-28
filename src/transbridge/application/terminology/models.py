"""Immutable domain contracts for project terminology.

These types deliberately contain no UI, storage, spreadsheet, or matcher model.
They are the semantic boundary shared by application use cases and adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from .errors import StaleBuildError


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


def _revision(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _unique_sorted(values: tuple[str, ...], label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    normalized = tuple(sorted(_required(value, label) for value in values))
    if not allow_empty and not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must be unique")
    return normalized


class ScopeKind(StrEnum):
    PROJECT = "project"
    PLUGIN = "plugin"


@dataclass(frozen=True, order=True, slots=True)
class TermScope:
    kind: ScopeKind = ScopeKind.PROJECT
    plugin_id: str | None = None

    def __post_init__(self) -> None:
        kind = ScopeKind(self.kind)
        plugin_id = None if self.plugin_id is None else _required(self.plugin_id, "plugin ID")
        if kind is ScopeKind.PLUGIN and plugin_id is None:
            raise ValueError("plugin scope requires a plugin ID")
        if kind is ScopeKind.PROJECT and plugin_id is not None:
            raise ValueError("project scope cannot carry a plugin ID")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "plugin_id", plugin_id)

    @classmethod
    def project(cls) -> Self:
        return cls()

    @classmethod
    def plugin(cls, plugin_id: str) -> Self:
        return cls(ScopeKind.PLUGIN, plugin_id)

    @property
    def canonical_key(self) -> str:
        return self.kind.value if self.plugin_id is None else f"{self.kind.value}:{self.plugin_id}"


class ExtractionMethod(StrEnum):
    DETERMINISTIC_NAME = "deterministic_name"
    LLM_TEXT = "llm_text"
    MANUAL = "manual"


class ConflictRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConflictStatus(StrEnum):
    UNRESOLVED = "unresolved"
    UNIFIED = "unified"
    PLUGIN_EXCEPTION = "plugin_exception"
    IGNORED = "ignored"


class DecisionStatus(StrEnum):
    ADOPTED = "adopted"
    MANUAL_CONFIRMED = "manual_confirmed"
    REVIEW_REQUIRED = "review_required"
    UNRESOLVED = "unresolved"


class ManualActionType(StrEnum):
    ADD = "add"
    CHANGE_TRANSLATION = "change_translation"
    REPLACE_ORIGINAL = "replace_original"
    CHANGE_SCOPE = "change_scope"
    CHANGE_ATTRIBUTES = "change_attributes"
    RESOLVE_CONFLICT = "resolve_conflict"
    IGNORE_CONFLICT = "ignore_conflict"
    SUPPRESS = "suppress"
    REENABLE = "reenable"


class BuildCompleteness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class BuildFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class LlmExtractionStatus(StrEnum):
    PERFORMED = "performed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"


class ChangeType(StrEnum):
    ADDED = "added"
    SUPPRESSED = "suppressed"
    TRANSLATION_CHANGED = "translation_changed"
    ORIGINAL_REPLACED = "original_replaced"
    SCOPE_CHANGED = "scope_changed"
    ATTRIBUTES_CHANGED = "attributes_changed"
    CONFLICT_STATUS_CHANGED = "conflict_status_changed"
    REENABLED = "reenabled"
    EVIDENCE_ONLY = "evidence_only"


class ArtifactKind(StrEnum):
    QUALITY_EXCEL = "quality_excel"
    CHANGELOG_MARKDOWN = "changelog_markdown"
    CHANGELOG_EXCEL = "changelog_excel"


class ArtifactStatus(StrEnum):
    PENDING = "pending"
    RENDERING = "rendering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, order=True, slots=True)
class BuildResultRef:
    build_key: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_key", _required(self.build_key, "build key"))
        object.__setattr__(self, "content_digest", _required(self.content_digest, "content digest"))


@dataclass(frozen=True, order=True, slots=True)
class DraftRef:
    draft_id: str
    project_id: str
    variant_id: str
    base_version_id: str | None
    base_content_digest: str
    revision: int
    decision_set_digest: str

    def __post_init__(self) -> None:
        for name in ("draft_id", "project_id", "variant_id", "base_content_digest", "decision_set_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        if self.base_version_id is not None:
            object.__setattr__(self, "base_version_id", _required(self.base_version_id, "base version ID"))
        _revision(self.revision, "draft revision")

    @property
    def cache_identity(self) -> tuple[str, str | None, str, int, str]:
        return (
            self.draft_id,
            self.base_version_id,
            self.base_content_digest,
            self.revision,
            self.decision_set_digest,
        )


@dataclass(frozen=True, order=True, slots=True)
class TerminologyVersionRef:
    version_id: str
    project_id: str
    variant_id: str
    content_digest: str

    def __post_init__(self) -> None:
        for name in ("version_id", "project_id", "variant_id", "content_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))


@dataclass(frozen=True, order=True, slots=True)
class TerminologyReportSnapshotRef:
    snapshot_id: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot_id", _required(self.snapshot_id, "snapshot ID"))
        object.__setattr__(self, "content_digest", _required(self.content_digest, "content digest"))


@dataclass(frozen=True, order=True, slots=True)
class ChangeLogDocumentRef:
    document_id: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _required(self.document_id, "document ID"))
        object.__setattr__(self, "content_digest", _required(self.content_digest, "content digest"))


@dataclass(frozen=True, slots=True)
class BilingualEvidence:
    evidence_id: str
    project_id: str
    variant_id: str
    source_chain: tuple[str, ...]
    namespace: str
    entry_key: str
    original: str
    translation: str
    source_format: str
    source_fingerprint: str
    context: str = ""
    stage: str = ""
    plugin_scope: str | None = None
    from_current_variant: bool = True

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "project_id",
            "variant_id",
            "namespace",
            "entry_key",
            "original",
            "translation",
            "source_format",
            "source_fingerprint",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "source_chain", _unique_sorted(self.source_chain, "source chain", allow_empty=False))
        if self.plugin_scope is not None:
            object.__setattr__(self, "plugin_scope", _required(self.plugin_scope, "plugin scope"))


@dataclass(frozen=True, slots=True)
class TermCandidate:
    candidate_id: str
    original: str
    translation: str
    normalized_original: str
    normalized_translation: str
    evidence_ids: tuple[str, ...]
    scope: TermScope
    extraction_method: ExtractionMethod
    algorithm_version: str

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "original",
            "translation",
            "normalized_original",
            "normalized_translation",
            "algorithm_version",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "evidence_ids", _unique_sorted(self.evidence_ids, "evidence ID", allow_empty=False))
        object.__setattr__(self, "extraction_method", ExtractionMethod(self.extraction_method))


@dataclass(frozen=True, slots=True)
class ConflictVariant:
    normalized_translation: str
    candidate_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_translation", _required(self.normalized_translation, "translation"))
        object.__setattr__(self, "candidate_ids", _unique_sorted(self.candidate_ids, "candidate ID", allow_empty=False))
        object.__setattr__(self, "evidence_ids", _unique_sorted(self.evidence_ids, "evidence ID", allow_empty=False))


@dataclass(frozen=True, slots=True)
class ConflictGroup:
    conflict_group_id: str
    project_id: str
    variant_id: str
    normalized_original: str
    variants: tuple[ConflictVariant, ...]
    risk: ConflictRisk = ConflictRisk.MEDIUM
    status: ConflictStatus = ConflictStatus.UNRESOLVED
    recommended_translation: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        for name in ("conflict_group_id", "project_id", "variant_id", "normalized_original"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        variants = tuple(sorted(self.variants, key=lambda item: item.normalized_translation))
        if len(variants) < 2 or len({item.normalized_translation for item in variants}) != len(variants):
            raise ValueError("a conflict group requires at least two distinct translations")
        object.__setattr__(self, "variants", variants)
        object.__setattr__(self, "risk", ConflictRisk(self.risk))
        object.__setattr__(self, "status", ConflictStatus(self.status))
        if self.status is ConflictStatus.UNIFIED and not self.recommended_translation:
            raise ValueError("a unified conflict requires a recommended translation")


@dataclass(frozen=True, slots=True)
class TermDecision:
    term_id: str
    project_id: str
    variant_id: str
    original: str
    normalized_original: str
    translation: str
    scope: TermScope = TermScope()
    status: DecisionStatus = DecisionStatus.REVIEW_REQUIRED
    suppressed: bool = False
    variants: tuple[str, ...] = ()
    notes: str = ""
    replacement_of: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("term_id", "project_id", "variant_id", "original", "normalized_original"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        if not self.suppressed:
            object.__setattr__(self, "translation", _required(self.translation, "translation"))
        object.__setattr__(self, "status", DecisionStatus(self.status))
        object.__setattr__(self, "variants", _unique_sorted(self.variants, "variant"))
        object.__setattr__(self, "evidence_ids", _unique_sorted(self.evidence_ids, "evidence ID"))
        if self.replacement_of is not None:
            object.__setattr__(self, "replacement_of", _required(self.replacement_of, "replacement term ID"))

    @property
    def is_effective(self) -> bool:
        return not self.suppressed and self.status in {DecisionStatus.ADOPTED, DecisionStatus.MANUAL_CONFIRMED}

    def require_effective(self) -> Self:
        if not self.is_effective:
            raise ValueError(
                "unresolved, review-required, or suppressed decisions cannot enter the effective projection"
            )
        return self


@dataclass(frozen=True, slots=True)
class ManualAction:
    action_id: str
    term_id: str
    action_type: ManualActionType
    actor: str
    occurred_at: str
    base_version_id: str | None
    before_digest: str | None
    after_digest: str | None
    reason: str | None = None
    replacement_term_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("action_id", "term_id", "actor", "occurred_at"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "action_type", ManualActionType(self.action_type))
        for name in ("base_version_id", "before_digest", "after_digest", "replacement_term_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(value, name.replace("_", " ")))


@dataclass(frozen=True, slots=True)
class BuildSummary:
    source_count: int
    evidence_count: int
    candidate_count: int
    conflict_count: int
    excluded_count: int = 0

    def __post_init__(self) -> None:
        for name in ("source_count", "evidence_count", "candidate_count", "conflict_count", "excluded_count"):
            _revision(getattr(self, name), name.replace("_", " "))


@dataclass(frozen=True, slots=True)
class BuildResult:
    ref: BuildResultRef
    project_id: str
    variant_id: str
    summary: BuildSummary
    evidence: tuple[BilingualEvidence, ...] = ()
    candidates: tuple[TermCandidate, ...] = ()
    conflicts: tuple[ConflictGroup, ...] = ()
    excluded_reasons: tuple[tuple[str, int], ...] = ()
    diagnostics: tuple[str, ...] = ()
    completeness: BuildCompleteness = BuildCompleteness.FULL
    freshness: BuildFreshness = BuildFreshness.CURRENT
    llm_status: LlmExtractionStatus = LlmExtractionStatus.SKIPPED

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_id", _required(self.project_id, "project ID"))
        object.__setattr__(self, "variant_id", _required(self.variant_id, "variant ID"))
        object.__setattr__(self, "evidence", tuple(sorted(self.evidence, key=lambda item: item.evidence_id)))
        object.__setattr__(self, "candidates", tuple(sorted(self.candidates, key=lambda item: item.candidate_id)))
        object.__setattr__(self, "conflicts", tuple(sorted(self.conflicts, key=lambda item: item.conflict_group_id)))
        reasons = tuple(
            sorted(
                (_required(key, "exclusion reason"), _revision(count, "exclusion count"))
                for key, count in self.excluded_reasons
            )
        )
        if len({key for key, _ in reasons}) != len(reasons):
            raise ValueError("exclusion reasons must be unique")
        object.__setattr__(self, "excluded_reasons", reasons)
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "completeness", BuildCompleteness(self.completeness))
        object.__setattr__(self, "freshness", BuildFreshness(self.freshness))
        object.__setattr__(self, "llm_status", LlmExtractionStatus(self.llm_status))

    def require_publishable(self, *, allow_partial: bool = False) -> Self:
        if self.freshness is BuildFreshness.STALE:
            raise StaleBuildError("a stale build cannot be published")
        if self.completeness is BuildCompleteness.PARTIAL and not allow_partial:
            raise ValueError("a partial build requires an explicit publish policy")
        return self


@dataclass(frozen=True, slots=True)
class TerminologyDraft:
    ref: DraftRef
    decisions: tuple[TermDecision, ...] = ()
    actions: tuple[ManualAction, ...] = ()
    conflict_resolutions: tuple[ConflictGroup, ...] = ()

    def __post_init__(self) -> None:
        decisions = tuple(sorted(self.decisions, key=lambda item: item.term_id))
        actions = tuple(sorted(self.actions, key=lambda item: item.action_id))
        if len({item.term_id for item in decisions}) != len(decisions):
            raise ValueError("draft decisions must have unique term IDs")
        if len({item.action_id for item in actions}) != len(actions):
            raise ValueError("draft actions must have unique action IDs")
        conflict_resolutions = tuple(sorted(self.conflict_resolutions, key=lambda item: item.conflict_group_id))
        if any(item.status is ConflictStatus.UNRESOLVED for item in conflict_resolutions):
            raise ValueError("draft conflict resolutions must have a resolved status")
        if len({item.conflict_group_id for item in conflict_resolutions}) != len(conflict_resolutions):
            raise ValueError("draft conflict resolutions must have unique conflict group IDs")
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "conflict_resolutions", conflict_resolutions)


@dataclass(frozen=True, slots=True)
class CanonicalChange:
    change_id: str
    change_type: ChangeType
    term_id: str
    before_digest: str | None
    after_digest: str | None
    manual: bool = False
    before: TermDecision | None = None
    after: TermDecision | None = None
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_id", _required(self.change_id, "change ID"))
        object.__setattr__(self, "term_id", _required(self.term_id, "term ID"))
        object.__setattr__(self, "change_type", ChangeType(self.change_type))
        details = tuple(sorted((_required(key, "detail key"), str(value)) for key, value in self.details))
        if len({key for key, _ in details}) != len(details):
            raise ValueError("canonical change detail keys must be unique")
        object.__setattr__(self, "details", details)


@dataclass(frozen=True, slots=True)
class CanonicalDiff:
    parent_version_id: str | None
    target_version_id: str
    content_digest: str
    changes: tuple[CanonicalChange, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_version_id", _required(self.target_version_id, "target version ID"))
        object.__setattr__(self, "content_digest", _required(self.content_digest, "content digest"))
        if self.parent_version_id is not None:
            object.__setattr__(self, "parent_version_id", _required(self.parent_version_id, "parent version ID"))
        changes = tuple(sorted(self.changes, key=lambda item: item.change_id))
        if len({item.change_id for item in changes}) != len(changes):
            raise ValueError("canonical changes must have unique IDs")
        object.__setattr__(self, "changes", changes)


@dataclass(frozen=True, slots=True)
class TerminologyVersion:
    ref: TerminologyVersionRef
    parent_version_id: str | None
    build_ref: BuildResultRef
    project_revision: int
    variant_revision: int
    completeness: BuildCompleteness
    published_at: str
    decisions: tuple[TermDecision, ...]
    canonical_diff: CanonicalDiff
    changelog_ref: ChangeLogDocumentRef | None = None
    conflicts: tuple[ConflictGroup, ...] = ()
    manual_actions: tuple[ManualAction, ...] = ()

    def __post_init__(self) -> None:
        _revision(self.project_revision, "project revision")
        _revision(self.variant_revision, "variant revision")
        object.__setattr__(self, "published_at", _required(self.published_at, "published at"))
        object.__setattr__(self, "completeness", BuildCompleteness(self.completeness))
        if self.parent_version_id is not None:
            object.__setattr__(self, "parent_version_id", _required(self.parent_version_id, "parent version ID"))
        if self.parent_version_id != self.canonical_diff.parent_version_id:
            raise ValueError("version parent and canonical diff parent must match")
        if self.ref.version_id != self.canonical_diff.target_version_id:
            raise ValueError("version and canonical diff target must match")
        decisions = tuple(sorted(self.decisions, key=lambda item: item.term_id))
        if any(item.project_id != self.ref.project_id or item.variant_id != self.ref.variant_id for item in decisions):
            raise ValueError("version decisions must belong to its Project/Variant line")
        if any(
            not item.suppressed and item.status not in {DecisionStatus.ADOPTED, DecisionStatus.MANUAL_CONFIRMED}
            for item in decisions
        ):
            raise ValueError("a version can only contain effective or explicitly suppressed decisions")
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "conflicts", tuple(sorted(self.conflicts, key=lambda item: item.conflict_group_id)))
        object.__setattr__(self, "manual_actions", tuple(sorted(self.manual_actions, key=lambda item: item.action_id)))


@dataclass(frozen=True, slots=True)
class TerminologyReportSnapshot:
    ref: TerminologyReportSnapshotRef
    build_ref: BuildResultRef
    draft_ref: DraftRef | None
    no_draft_identity: str | None
    terms: tuple[TermDecision, ...]
    conflicts: tuple[ConflictGroup, ...]
    manual_actions: tuple[ManualAction, ...]

    def __post_init__(self) -> None:
        if (self.draft_ref is None) == (self.no_draft_identity is None):
            raise ValueError("report snapshot requires exactly one draft or no-draft identity")
        if self.no_draft_identity is not None:
            object.__setattr__(self, "no_draft_identity", _required(self.no_draft_identity, "no-draft identity"))
        object.__setattr__(self, "terms", tuple(sorted(self.terms, key=lambda item: item.term_id)))
        object.__setattr__(self, "conflicts", tuple(sorted(self.conflicts, key=lambda item: item.conflict_group_id)))
        object.__setattr__(self, "manual_actions", tuple(sorted(self.manual_actions, key=lambda item: item.action_id)))


@dataclass(frozen=True, slots=True)
class TerminologyReportSnapshotManifest:
    snapshot_ref: TerminologyReportSnapshotRef
    build_ref: BuildResultRef
    draft_identity: str
    section_digests: tuple[tuple[str, str], ...]
    section_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_identity", _required(self.draft_identity, "draft identity"))
        digests = tuple(
            sorted(
                (_required(key, "section name"), _required(value, "section digest"))
                for key, value in self.section_digests
            )
        )
        counts = tuple(
            sorted(
                (_required(key, "section name"), _revision(value, "section count"))
                for key, value in self.section_counts
            )
        )
        if len({key for key, _ in digests}) != len(digests) or len({key for key, _ in counts}) != len(counts):
            raise ValueError("report manifest section names must be unique")
        if {key for key, _ in digests} != {key for key, _ in counts}:
            raise ValueError("report manifest digest and count sections must match")
        object.__setattr__(self, "section_digests", digests)
        object.__setattr__(self, "section_counts", counts)

    def section_count(self, name: str) -> int:
        try:
            return dict(self.section_counts)[name]
        except KeyError as exc:
            raise KeyError(f"unknown report section: {name}") from exc


@dataclass(frozen=True, slots=True)
class ChangeLogDocument:
    ref: ChangeLogDocumentRef
    version_ref: TerminologyVersionRef
    locale: str
    schema_version: str
    template_digest: str
    user_messages: tuple[tuple[str, tuple[str, ...]], ...]
    changes: tuple[CanonicalChange, ...]
    diagnostics: tuple[str, ...] = ()
    conflict_group_ids: tuple[str, ...] = ()
    no_evidence_term_ids: tuple[str, ...] = ()
    manual_action_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("locale", "schema_version", "template_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        messages = tuple(sorted(((_required(key, "message key"), tuple(args))) for key, args in self.user_messages))
        object.__setattr__(self, "user_messages", messages)
        object.__setattr__(self, "changes", tuple(sorted(self.changes, key=lambda item: item.change_id)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "conflict_group_ids", _unique_sorted(self.conflict_group_ids, "conflict group ID"))
        object.__setattr__(self, "no_evidence_term_ids", _unique_sorted(self.no_evidence_term_ids, "term ID"))
        object.__setattr__(self, "manual_action_ids", _unique_sorted(self.manual_action_ids, "manual action ID"))


@dataclass(frozen=True, slots=True)
class ChangeLogDocumentManifest:
    ref: ChangeLogDocumentRef
    version_ref: TerminologyVersionRef
    locale: str
    schema_version: str
    template_digest: str
    section_digests: tuple[tuple[str, str], ...]
    section_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        for name in ("locale", "schema_version", "template_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        digests = tuple(
            sorted(
                (_required(key, "section name"), _required(value, "section digest"))
                for key, value in self.section_digests
            )
        )
        counts = tuple(
            sorted(
                (_required(key, "section name"), _revision(value, "section count"))
                for key, value in self.section_counts
            )
        )
        if len({key for key, _ in digests}) != len(digests) or len({key for key, _ in counts}) != len(counts):
            raise ValueError("changelog manifest section names must be unique")
        if {key for key, _ in digests} != {key for key, _ in counts}:
            raise ValueError("changelog manifest digest and count sections must match")
        object.__setattr__(self, "section_digests", digests)
        object.__setattr__(self, "section_counts", counts)

    def section_digest(self, name: str) -> str:
        try:
            return dict(self.section_digests)[name]
        except KeyError as exc:
            raise KeyError(f"unknown changelog section: {name}") from exc

    def section_count(self, name: str) -> int:
        try:
            return dict(self.section_counts)[name]
        except KeyError as exc:
            raise KeyError(f"unknown changelog section: {name}") from exc


@dataclass(frozen=True, slots=True)
class ArtifactLedgerEntry:
    artifact_id: str
    owner_ref: str
    kind: ArtifactKind
    renderer_version: str
    content_digest: str
    target: str
    status: ArtifactStatus = ArtifactStatus.PENDING
    retry_count: int = 0
    diagnostic: str | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        for name in ("artifact_id", "owner_ref", "renderer_version", "content_digest", "target"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        object.__setattr__(self, "kind", ArtifactKind(self.kind))
        object.__setattr__(self, "status", ArtifactStatus(self.status))
        _revision(self.retry_count, "retry count")
        _revision(self.revision, "artifact revision")


__all__ = [
    "ArtifactKind",
    "ArtifactLedgerEntry",
    "ArtifactStatus",
    "BilingualEvidence",
    "BuildCompleteness",
    "BuildFreshness",
    "BuildResult",
    "BuildResultRef",
    "BuildSummary",
    "CanonicalChange",
    "CanonicalDiff",
    "ChangeLogDocument",
    "ChangeLogDocumentManifest",
    "ChangeLogDocumentRef",
    "ChangeType",
    "ConflictGroup",
    "ConflictRisk",
    "ConflictStatus",
    "ConflictVariant",
    "DecisionStatus",
    "DraftRef",
    "ExtractionMethod",
    "LlmExtractionStatus",
    "ManualAction",
    "ManualActionType",
    "ScopeKind",
    "TermCandidate",
    "TermDecision",
    "TermScope",
    "TerminologyDraft",
    "TerminologyReportSnapshot",
    "TerminologyReportSnapshotManifest",
    "TerminologyReportSnapshotRef",
    "TerminologyVersion",
    "TerminologyVersionRef",
]
