"""Storage, capture, and commit guards for terminology composition."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from threading import RLock

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatCapabilitySnapshot, FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.projects import ProjectLifecycleService
from transbridge.application.terminology.identity import canonical_digest
from transbridge.application.terminology.input_capture import (
    BuildInputSnapshot,
    ProjectVariantCapture,
    SourceLease,
    TerminologyBaseline,
)
from transbridge.application.terminology.runtime import TerminologyBusinessGuardResult
from transbridge.application.terminology.workloads import TerminologyExpectedState
from transbridge.persistence.terminology import SqliteTerminologyRepository, TerminologyPaths
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope

_SOURCE_HASH_CHUNK_BYTES = 1024 * 1024


class ProjectTerminologyRepositories:
    """Lazily open and own one real SQLite repository per Project."""

    def __init__(self, root: str | Path) -> None:
        self.paths = TerminologyPaths(Path(root).resolve(strict=False))
        self._items: dict[str, SqliteTerminologyRepository] = {}
        self._lock = RLock()

    def for_project(self, project_id: str) -> SqliteTerminologyRepository:
        with self._lock:
            repository = self._items.get(project_id)
            if repository is None:
                repository = SqliteTerminologyRepository.open(str(self.paths.root), project_id)
                self._items[project_id] = repository
            return repository

    def close(self) -> None:
        with self._lock:
            repositories = tuple(self._items.values())
            self._items.clear()
        for repository in repositories:
            repository.close()


class LifecycleCapture:
    def __init__(self, lifecycle: ProjectLifecycleService) -> None:
        self._lifecycle = lifecycle

    def capture_project_variant(self) -> ProjectVariantCapture | None:
        active = self._lifecycle.active
        if active is None or active.variant is None:
            return None
        envelope = active.project.envelope
        project = ProjectDto(
            SchemaEnvelope(
                envelope.schema_version,
                envelope.entity_type,
                envelope.identity,
                envelope.revision,
                deepcopy(envelope.data),
            )
        )
        return ProjectVariantCapture(project, active.variant.snapshot())


class FilesystemSourceLeases:
    def __init__(self, *, max_unstreamed_source_bytes: int | None = None) -> None:
        self._max_unstreamed_source_bytes = max_unstreamed_source_bytes

    @staticmethod
    def source_size(registration) -> int:
        return Path(registration.location).stat().st_size

    def acquire(self, registration) -> SourceLease:
        return self.acquire_bounded(registration, max_bytes=None)

    def acquire_bounded(self, registration, *, max_bytes: int | None) -> SourceLease:
        path = Path(registration.location)
        source_bytes = path.stat().st_size
        limits = tuple(value for value in (self._max_unstreamed_source_bytes, max_bytes) if value is not None)
        effective_limit = min(limits) if limits else None
        if effective_limit is not None and source_bytes > effective_limit:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "TERMINOLOGY_STREAMING_REQUIRED",
                "该来源超过当前完整载入适配器的安全边界，需要流式读取能力后才能构建。",
                details={
                    "source_id": registration.source_id,
                    "source_bytes": source_bytes,
                    "limit_bytes": effective_limit,
                    "recovery": "拆分来源或使用支持流式读取的适配器后重试。",
                },
            )
        digest = hashlib.sha256()
        observed_bytes = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_SOURCE_HASH_CHUNK_BYTES):
                observed_bytes += len(chunk)
                if effective_limit is not None and observed_bytes > effective_limit:
                    raise DomainError(
                        ErrorCategory.PREREQUISITE,
                        "TERMINOLOGY_STREAMING_REQUIRED",
                        "该来源在捕获期间超过完整载入适配器的安全边界，需要流式读取能力后才能构建。",
                        details={
                            "source_id": registration.source_id,
                            "source_bytes": observed_bytes,
                            "limit_bytes": effective_limit,
                            "recovery": "拆分来源或使用支持流式读取的适配器后重试。",
                        },
                    )
                digest.update(chunk)
        fingerprint = digest.hexdigest()
        snapshot = SourceSnapshot(
            SourceDescriptor(str(path), path.name, observed_bytes),
            registration.format_id,
            fingerprint,
            observed_bytes,
            lease_id=f"filesystem-sha256:{fingerprint}",
        )
        return SourceLease(registration.source_id, snapshot, snapshot.sha256)

    def current_fingerprint(self, lease: SourceLease) -> str:
        path = Path(lease.snapshot.source.uri)
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()


class FormatCapabilities:
    def __init__(self, catalog) -> None:
        self.catalog = catalog
        self._snapshots = {item.format_id: item for item in self.catalog.capability_snapshot()}

    def capability_for(self, format_id: FormatId) -> FormatCapabilitySnapshot | None:
        return self._snapshots.get(format_id)


class RepositoryBaselines:
    def __init__(self, repositories: ProjectTerminologyRepositories) -> None:
        self._repositories = repositories

    def capture_baseline(self, project_id: str, variant_id: str) -> TerminologyBaseline:
        repository = self._repositories.for_project(project_id)
        effective = repository.effective_version(project_id, variant_id)
        draft = repository.active_draft(project_id, variant_id)
        return TerminologyBaseline(
            effective_version_id=None if effective is None else effective.ref.version_id,
            effective_content_digest=(
                empty_digest() if effective is None else sha256_part(effective.ref.content_digest)
            ),
            draft_id="no-draft" if draft is None else draft.ref.draft_id,
            draft_base_version_id=None if draft is None else draft.ref.base_version_id,
            draft_base_content_digest=(empty_digest() if draft is None else sha256_part(draft.ref.base_content_digest)),
            draft_revision=0 if draft is None else draft.ref.revision,
            decision_digest=(
                hashlib.sha256(b"[]").hexdigest() if draft is None else sha256_part(draft.ref.decision_set_digest)
            ),
        )


class ProductionState:
    def __init__(self) -> None:
        self.snapshots: dict[str, BuildInputSnapshot] = {}
        self.contexts: dict[str, object] = {}
        self.latest_builds: dict[tuple[str, str], object] = {}
        self.latest_reports: dict[tuple[str, str], object] = {}
        self.publish_payloads: dict[str, dict[str, object]] = {}
        self.report_payloads: dict[str, dict[str, object]] = {}
        self.changelog_payloads: dict[str, dict[str, object]] = {}
        self.compare_payloads: dict[str, dict[str, object]] = {}
        self.latest_comparisons: dict[tuple[str, str], object] = {}
        self._lock = RLock()

    def put(self, mapping: dict, key, value) -> None:
        with self._lock:
            mapping[key] = value

    def get(self, mapping: dict, key):
        with self._lock:
            return mapping[key]


class ProductionTerminologyCommitPort:
    """Re-check captured revisions, source bytes, and SQLite line before commit."""

    def __init__(self, lifecycle, repositories, state, leases) -> None:
        self._lifecycle = lifecycle
        self._repositories = repositories
        self._state = state
        self._leases = leases

    def commit_if_current(self, expected: TerminologyExpectedState, mutation) -> TerminologyBusinessGuardResult:
        try:
            snapshot = self._state.get(self._state.snapshots, expected.digest)
            active = self._lifecycle.active
            if active is None or active.variant is None:
                return TerminologyBusinessGuardResult(False, "TERMINOLOGY_PROJECT_CLOSED")
            if (
                active.project.envelope.identity != snapshot.project_id
                or active.variant.ref.identity.value != snapshot.variant_id
                or active.project.envelope.revision != expected.project_revision
                or active.variant.revision != expected.variant_revision
            ):
                return TerminologyBusinessGuardResult(False, "TERMINOLOGY_REVISION_STALE")
            source_changed = any(
                self._leases.current_fingerprint(item.lease) != item.lease.actual_fingerprint
                for item in snapshot.sources
            )
            if source_changed:
                return TerminologyBusinessGuardResult(False, "TERMINOLOGY_SOURCE_CHANGED")
            repository = self._repositories.for_project(snapshot.project_id)
            if expected_state(snapshot, repository) != expected:
                return TerminologyBusinessGuardResult(False, "TERMINOLOGY_EXPECTED_STATE_STALE")
            mutation()
            return TerminologyBusinessGuardResult(True)
        except KeyError:
            return TerminologyBusinessGuardResult(False, "TERMINOLOGY_CAPTURE_LEASE_MISSING")


def expected_state(
    snapshot: BuildInputSnapshot,
    repository: SqliteTerminologyRepository,
) -> TerminologyExpectedState:
    effective = repository.effective_version(snapshot.project_id, snapshot.variant_id)
    draft = repository.active_draft(snapshot.project_id, snapshot.variant_id)
    return TerminologyExpectedState(
        snapshot.project_revision,
        snapshot.variant_revision,
        canonical_digest(snapshot.relations, namespace="terminology.source-graph.v1"),
        canonical_digest(
            tuple((item.registration.source_id, item.lease.actual_fingerprint) for item in snapshot.sources),
            namespace="terminology.source-fingerprints.v1",
        ),
        effective_version_id=None if effective is None else effective.ref.version_id,
        base_version_id=None if draft is None else draft.ref.base_version_id,
        draft_id="no-draft" if draft is None else draft.ref.draft_id,
        draft_revision=0 if draft is None else draft.ref.revision,
        effective_content_digest="no-effective" if effective is None else effective.ref.content_digest,
        base_content_digest="no-base" if draft is None else draft.ref.base_content_digest,
        decision_set_digest="no-draft" if draft is None else draft.ref.decision_set_digest,
    )


def empty_digest() -> str:
    return hashlib.sha256(b"").hexdigest()


def sha256_part(value: str) -> str:
    candidate = value.rsplit(":", 1)[-1]
    if len(candidate) != 64:
        raise ValueError("terminology content digest does not contain SHA-256")
    return candidate


__all__ = [
    "FilesystemSourceLeases",
    "FormatCapabilities",
    "LifecycleCapture",
    "ProductionState",
    "ProductionTerminologyCommitPort",
    "ProjectTerminologyRepositories",
    "RepositoryBaselines",
    "empty_digest",
    "expected_state",
]
