"""Freeze quality-report facts independently from rendering and UI layout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .identity import canonical_digest
from .models import (
    BuildResult,
    BuildResultRef,
    ConflictGroup,
    DraftRef,
    ManualAction,
    TermDecision,
    TerminologyDraft,
    TerminologyReportSnapshot,
    TerminologyReportSnapshotManifest,
    TerminologyReportSnapshotRef,
)

NO_DRAFT_DECISION_SET_DIGEST = "no-draft"


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value.strip()


@dataclass(frozen=True, slots=True)
class NoDraftIdentity:
    """Explicitly pin the absence of a draft to one Project/Variant baseline."""

    project_id: str
    variant_id: str
    base_version_id: str | None
    base_content_digest: str
    revision: int = 0
    decision_set_digest: str = NO_DRAFT_DECISION_SET_DIGEST

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id", "base_content_digest", "decision_set_digest"):
            object.__setattr__(self, name, _required(getattr(self, name), name.replace("_", " ")))
        if self.base_version_id is not None:
            object.__setattr__(self, "base_version_id", _required(self.base_version_id, "base version ID"))
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("no-draft revision must be a non-negative integer")

    @property
    def token(self) -> str:
        return canonical_digest(self, namespace="terminology.no-draft-identity.v1")


class ReportFactReader(Protocol):
    def get_build(self, ref: BuildResultRef) -> BuildResult: ...


class ReportSnapshotWriter(Protocol):
    def put_report_snapshot(self, snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotRef: ...


def build_report_manifest(snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotManifest:
    draft_identity = snapshot.no_draft_identity
    if draft_identity is None:
        if snapshot.draft_ref is None:  # TerminologyReportSnapshot validates this invariant
            raise ValueError("report snapshot has no pinned identity")
        draft_identity = snapshot.draft_ref.decision_set_digest
    sections = {
        "terms": snapshot.terms,
        "conflicts": snapshot.conflicts,
        "manual": snapshot.manual_actions,
    }
    return TerminologyReportSnapshotManifest(
        snapshot.ref,
        snapshot.build_ref,
        draft_identity,
        tuple(
            (name, canonical_digest(items, namespace=f"terminology.report-section.{name}.v1"))
            for name, items in sections.items()
        ),
        tuple((name, len(items)) for name, items in sections.items()),
    )


class TerminologyReportSnapshotFactory:
    """Create an immutable report manifest from already-frozen business facts."""

    def __init__(self, facts: ReportFactReader) -> None:
        self._facts = facts

    def freeze(
        self,
        build_ref: BuildResultRef,
        *,
        draft: TerminologyDraft | None = None,
        no_draft: NoDraftIdentity | None = None,
        terms: tuple[TermDecision, ...] | None = None,
        conflicts: tuple[ConflictGroup, ...] | None = None,
        manual_actions: tuple[ManualAction, ...] | None = None,
    ) -> TerminologyReportSnapshot:
        if (draft is None) == (no_draft is None):
            raise ValueError("provide exactly one pinned draft or explicit no-draft identity")
        build = self._facts.get_build(build_ref)
        draft_ref: DraftRef | None = None
        no_draft_identity: str | None = None
        if draft is not None:
            draft_ref = draft.ref
            self._validate_line(build, draft.ref.project_id, draft.ref.variant_id)
            if terms is not None and terms != draft.decisions:
                raise ValueError("report terms must match the pinned draft")
            if manual_actions is not None and manual_actions != draft.actions:
                raise ValueError("report manual actions must match the pinned draft")
            frozen_terms = draft.decisions
            frozen_actions = draft.actions
        else:
            if no_draft is None:  # guarded by the exclusive identity check above
                raise ValueError("an explicit no-draft identity is required")
            self._validate_line(build, no_draft.project_id, no_draft.variant_id)
            no_draft_identity = no_draft.token
            frozen_terms = () if terms is None else terms
            frozen_actions = () if manual_actions is None else manual_actions
        if draft is not None:
            resolutions = {item.conflict_group_id: item for item in draft.conflict_resolutions}
            expected_conflicts = tuple(resolutions.get(item.conflict_group_id, item) for item in build.conflicts)
            if conflicts is not None and conflicts != expected_conflicts:
                raise ValueError("report conflicts must match the pinned build and draft resolutions")
            frozen_conflicts = expected_conflicts
        else:
            frozen_conflicts = build.conflicts if conflicts is None else conflicts
        self._validate_membership(build, frozen_terms, frozen_conflicts)
        payload = {
            "build_ref": build.ref,
            "draft_ref": draft_ref,
            "no_draft_identity": no_draft_identity,
            "terms": frozen_terms,
            "conflicts": frozen_conflicts,
            "manual_actions": frozen_actions,
        }
        digest = canonical_digest(payload, namespace="terminology.report-snapshot.v1")
        ref = TerminologyReportSnapshotRef(f"report:{digest.rsplit(':', 1)[-1]}", digest)
        return TerminologyReportSnapshot(
            ref,
            build.ref,
            draft_ref,
            no_draft_identity,
            frozen_terms,
            frozen_conflicts,
            frozen_actions,
        )

    @staticmethod
    def _validate_line(build: BuildResult, project_id: str, variant_id: str) -> None:
        if (project_id, variant_id) != (build.project_id, build.variant_id):
            raise ValueError("report identity must belong to the BuildResult Project/Variant line")

    @staticmethod
    def _validate_membership(
        build: BuildResult,
        terms: tuple[TermDecision, ...],
        conflicts: tuple[ConflictGroup, ...],
    ) -> None:
        if any((item.project_id, item.variant_id) != (build.project_id, build.variant_id) for item in terms):
            raise ValueError("report terms must belong to the BuildResult Project/Variant line")
        if any((item.project_id, item.variant_id) != (build.project_id, build.variant_id) for item in conflicts):
            raise ValueError("report conflicts must belong to the BuildResult Project/Variant line")


class TerminologyReportService:
    """Persist a newly frozen snapshot without changing its BuildResult or draft."""

    def __init__(self, factory: TerminologyReportSnapshotFactory, snapshots: ReportSnapshotWriter) -> None:
        self._factory = factory
        self._snapshots = snapshots

    def freeze(
        self,
        build_ref: BuildResultRef,
        *,
        draft: TerminologyDraft | None = None,
        no_draft: NoDraftIdentity | None = None,
        terms: tuple[TermDecision, ...] | None = None,
        conflicts: tuple[ConflictGroup, ...] | None = None,
        manual_actions: tuple[ManualAction, ...] | None = None,
    ) -> TerminologyReportSnapshotRef:
        snapshot = self._factory.freeze(
            build_ref,
            draft=draft,
            no_draft=no_draft,
            terms=terms,
            conflicts=conflicts,
            manual_actions=manual_actions,
        )
        return self._snapshots.put_report_snapshot(snapshot)


__all__ = [
    "NO_DRAFT_DECISION_SET_DIGEST",
    "NoDraftIdentity",
    "ReportFactReader",
    "ReportSnapshotWriter",
    "TerminologyReportService",
    "TerminologyReportSnapshotFactory",
    "build_report_manifest",
]
