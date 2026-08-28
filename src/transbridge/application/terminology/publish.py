"""Immutable terminology publication with business-state guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from .diff import CanonicalDiffEngine
from .errors import RepositoryConflictError
from .identity import canonical_digest
from .models import (
    ArtifactKind,
    ArtifactLedgerEntry,
    ArtifactStatus,
    BuildResult,
    BuildResultRef,
    ChangeLogDocument,
    DraftRef,
    TerminologyDraft,
    TerminologyVersion,
    TerminologyVersionRef,
)
from .narrative import ChangeNarrativeProjector
from .versions import VersionMaterializer
from .workloads import TerminologyExpectedState


class PublishGuardRejectedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishTerminologyRequest:
    project_id: str
    variant_id: str
    expected: TerminologyExpectedState
    build_ref: BuildResultRef
    version_id: str
    published_at: str
    draft_ref: DraftRef | None = None
    rollback_from: TerminologyVersionRef | None = None
    markdown_target: str = "terminology-changelog.md"
    excel_target: str = "terminology-changelog.xlsx"

    def __post_init__(self) -> None:
        for value, label in (
            (self.project_id, "project ID"),
            (self.variant_id, "variant ID"),
            (self.version_id, "version ID"),
            (self.published_at, "published timestamp"),
            (self.markdown_target, "Markdown target"),
            (self.excel_target, "Excel target"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        if (self.draft_ref is None) == (self.rollback_from is None):
            raise ValueError("publication requires exactly one reviewed draft or rollback source")


@dataclass(frozen=True, slots=True)
class AtomicPublishBundle:
    version: TerminologyVersion
    changelog: ChangeLogDocument
    artifacts: tuple[ArtifactLedgerEntry, ...]
    expected: TerminologyExpectedState
    expected_draft_ref: DraftRef | None


@dataclass(frozen=True, slots=True)
class PublishTerminologyResult:
    version_ref: TerminologyVersionRef
    changelog: ChangeLogDocument
    artifacts: tuple[ArtifactLedgerEntry, ...]


class PublishStatePort(Protocol):
    def current(self, project_id: str, variant_id: str) -> TerminologyExpectedState: ...


class PublishRunPermitPort(Protocol):
    def is_permitted(self) -> bool: ...


class PublishRepositoryPort(Protocol):
    def get_build(self, ref: BuildResultRef) -> BuildResult: ...

    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None: ...

    def effective_version(self, project_id: str, variant_id: str) -> TerminologyVersion | None: ...

    def get_version(self, ref: TerminologyVersionRef) -> TerminologyVersion: ...

    def publish_version_atomically(
        self,
        bundle: AtomicPublishBundle,
        *,
        business_guard: Callable[[], bool],
        fault_injector: Callable[[str], None] | None = None,
    ) -> TerminologyVersionRef: ...

    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None: ...

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry: ...


def terminology_version_content_digest(version: TerminologyVersion) -> str:
    """Recompute the immutable version digest without trusting its stored ref."""
    return _version_content_digest(
        project_id=version.ref.project_id,
        variant_id=version.ref.variant_id,
        version_id=version.ref.version_id,
        parent_version_id=version.parent_version_id,
        build_ref=version.build_ref,
        project_revision=version.project_revision,
        variant_revision=version.variant_revision,
        published_at=version.published_at,
        decisions=version.decisions,
        conflicts=version.conflicts,
        manual_actions=version.manual_actions,
        diff=version.canonical_diff,
    )


def _version_content_digest(
    *,
    project_id: str,
    variant_id: str,
    version_id: str,
    parent_version_id: str | None,
    build_ref: BuildResultRef,
    project_revision: int,
    variant_revision: int,
    published_at: str,
    decisions: tuple,
    conflicts: tuple,
    manual_actions: tuple,
    diff: object,
) -> str:
    return canonical_digest(
        {
            "project": project_id,
            "variant": variant_id,
            "version": version_id,
            "parent": parent_version_id,
            "build": build_ref,
            "project_revision": project_revision,
            "variant_revision": variant_revision,
            "published_at": published_at,
            "decisions": decisions,
            "conflicts": conflicts,
            "manual_actions": manual_actions,
            "diff": diff,
        },
        namespace="terminology.version-content.v1",
    )


class VersionPublisher:
    def __init__(
        self,
        repository: PublishRepositoryPort,
        state: PublishStatePort,
        run_permit: PublishRunPermitPort,
        *,
        materializer: VersionMaterializer | None = None,
        diff_engine: CanonicalDiffEngine | None = None,
        narrative: ChangeNarrativeProjector | None = None,
    ) -> None:
        self._repository = repository
        self._state = state
        self._run_permit = run_permit
        self._materializer = materializer or VersionMaterializer()
        self._diff = diff_engine or CanonicalDiffEngine()
        self._narrative = narrative or ChangeNarrativeProjector()

    def publish(
        self,
        request: PublishTerminologyRequest,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> PublishTerminologyResult:
        build = self._repository.get_build(request.build_ref).require_publishable()
        if (build.project_id, build.variant_id) != (request.project_id, request.variant_id):
            raise ValueError("build does not belong to the publication Project/Variant")
        parent = self._repository.effective_version(request.project_id, request.variant_id)
        parent_id = None if parent is None else parent.ref.version_id
        if parent_id != request.expected.effective_version_id:
            raise PublishGuardRejectedError("effective version changed before publication")

        if request.rollback_from is not None:
            source = self._repository.get_version(request.rollback_from)
            content = self._materializer.materialize(build, rollback_source=source)
        else:
            draft = self._repository.active_draft(request.project_id, request.variant_id)
            if draft is None or draft.ref != request.draft_ref:
                raise PublishGuardRejectedError("reviewed draft is absent or changed")
            if (draft.ref.base_version_id, request.expected.base_version_id) != (parent_id, parent_id):
                raise PublishGuardRejectedError("draft base is not the current effective version")
            content = self._materializer.materialize(build, draft=draft)

        diff = self._diff.compare(
            parent,
            target_version_id=request.version_id,
            decisions=content.decisions,
            conflicts=content.conflicts,
            manual_actions=content.manual_actions,
        )
        version_digest = _version_content_digest(
            project_id=request.project_id,
            variant_id=request.variant_id,
            version_id=request.version_id,
            parent_version_id=parent_id,
            build_ref=build.ref,
            project_revision=request.expected.project_revision,
            variant_revision=request.expected.variant_revision,
            published_at=request.published_at,
            decisions=content.decisions,
            conflicts=content.conflicts,
            manual_actions=content.manual_actions,
            diff=diff,
        )
        version_ref = TerminologyVersionRef(
            request.version_id,
            request.project_id,
            request.variant_id,
            version_digest,
        )
        changelog = self._narrative.project(
            version_ref=version_ref,
            diff=diff,
            decisions=content.decisions,
            conflicts=content.conflicts,
            manual_actions=content.manual_actions,
            diagnostics=build.diagnostics,
        )
        version = TerminologyVersion(
            version_ref,
            parent_id,
            build.ref,
            request.expected.project_revision,
            request.expected.variant_revision,
            build.completeness,
            request.published_at,
            content.decisions,
            diff,
            changelog.ref,
            content.conflicts,
            content.manual_actions,
        )
        artifacts = (
            _pending_artifact(changelog, ArtifactKind.CHANGELOG_MARKDOWN, request.markdown_target),
            _pending_artifact(changelog, ArtifactKind.CHANGELOG_EXCEL, request.excel_target),
        )
        bundle = AtomicPublishBundle(version, changelog, artifacts, request.expected, request.draft_ref)

        def guard() -> bool:
            return (
                self._run_permit.is_permitted()
                and self._state.current(request.project_id, request.variant_id) == request.expected
            )

        published = self._repository.publish_version_atomically(
            bundle,
            business_guard=guard,
            fault_injector=fault_injector,
        )
        return PublishTerminologyResult(published, changelog, artifacts)

    def record_renderer_result(self, artifact_id: str, *, diagnostic: str | None = None) -> ArtifactLedgerEntry:
        current = self._repository.get_artifact(artifact_id)
        if current is None:
            raise KeyError(f"artifact ledger entry was not found: {artifact_id}")
        if current.status not in {ArtifactStatus.PENDING, ArtifactStatus.FAILED}:
            raise RepositoryConflictError(f"artifact is already {current.status.value}")
        rendering = replace(
            current,
            status=ArtifactStatus.RENDERING,
            diagnostic=None,
            revision=current.revision + 1,
        )
        rendering = self._repository.update_artifact(
            rendering,
            expected_status=current.status,
            expected_revision=current.revision,
        )
        failed = diagnostic is not None
        updated = replace(
            rendering,
            status=ArtifactStatus.FAILED if failed else ArtifactStatus.SUCCEEDED,
            retry_count=rendering.retry_count + (1 if failed else 0),
            diagnostic=diagnostic,
            revision=rendering.revision + 1,
        )
        return self._repository.update_artifact(
            updated,
            expected_status=ArtifactStatus.RENDERING,
            expected_revision=rendering.revision,
        )


def _pending_artifact(
    changelog: ChangeLogDocument,
    kind: ArtifactKind,
    target: str,
) -> ArtifactLedgerEntry:
    identity = canonical_digest(
        {"owner": changelog.ref.document_id, "kind": kind, "target": target},
        namespace="terminology.artifact-ledger.v1",
    )
    return ArtifactLedgerEntry(
        identity,
        changelog.ref.document_id,
        kind,
        "pending-renderer.v1",
        changelog.ref.content_digest,
        target,
    )


__all__ = [
    "AtomicPublishBundle",
    "PublishGuardRejectedError",
    "PublishRepositoryPort",
    "PublishRunPermitPort",
    "PublishStatePort",
    "PublishTerminologyRequest",
    "PublishTerminologyResult",
    "VersionPublisher",
    "terminology_version_content_digest",
]
