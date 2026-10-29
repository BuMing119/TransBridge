"""Terminology leases over the Project's durable parsed source content."""

from dataclasses import replace
from pathlib import Path

from transbridge.application.contracts import DiagnosticSeverity, DomainError, ErrorCategory, OperationOutcome
from transbridge.application.io import ParseResult, ParseStats
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.terminology.input_capture import BuildInputSnapshot, SourceLease
from transbridge.persistence.saved_project_sources import load_saved_project_source
from transbridge.persistence.source_artifact_store import ProjectSourceArtifactStore


def lightweight_build_snapshot(snapshot: BuildInputSnapshot) -> BuildInputSnapshot:
    """Keep commit identity without retaining parsed entries or captured source bytes."""
    sources = tuple(
        replace(
            source,
            lease=replace(
                source.lease,
                saved_result=None,
                snapshot=replace(
                    source.lease.snapshot,
                    content=None,
                    metadata=(),
                    lease_id=source.lease.snapshot.lease_id
                    or f"project-source-artifact:{source.lease.source_artifact_id or source.lease.actual_fingerprint}",
                ),
            ),
        )
        for source in snapshot.sources
    )
    return replace(snapshot, sources=sources)


class ProjectSourceLeases:
    def __init__(self, root: str | Path, lifecycle) -> None:
        self._artifacts = ProjectSourceArtifactStore(str(root))
        self._lifecycle = lifecycle

    def _load(self, registration):
        if registration.source_artifact_id is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "TERMINOLOGY_SOURCE_REQUIRED",
                "该工程尚未保存来源内容，请补充来源文件后重新构建。",
                details={"source_id": registration.source_id},
            )
        prepared = load_saved_project_source(self._artifacts, registration.to_dict(), ())
        if prepared.hydration is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "PROJECT_SOURCE_ARTIFACT_UNAVAILABLE",
                "工程保存的来源内容不可用，请补充来源文件后重试。",
                details={"source_id": registration.source_id},
            )
        return prepared

    def acquire(self, registration) -> SourceLease:
        return self.acquire_bounded(registration, max_bytes=None)

    def acquire_bounded(self, registration, *, max_bytes: int | None) -> SourceLease:
        prepared = self._load(registration)
        hydration = prepared.hydration
        snapshot = hydration.source_snapshot
        if max_bytes is not None and snapshot.size_bytes > max_bytes:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "TERMINOLOGY_STREAMING_REQUIRED",
                "该来源超过当前完整载入适配器的安全边界，需要流式读取能力后才能构建。",
                details={
                    "source_id": registration.source_id,
                    "source_bytes": snapshot.size_bytes,
                    "limit_bytes": max_bytes,
                    "recovery": "拆分来源或使用支持流式读取的适配器后重试。",
                },
            )
        errors = sum(item.severity is DiagnosticSeverity.ERROR for item in prepared.diagnostics)
        result = ParseResult(
            OperationOutcome.PARTIAL
            if errors and hydration.entries
            else (OperationOutcome.FAILED if errors else OperationOutcome.COMPLETED),
            hydration.format_id,
            snapshot.source,
            snapshot if hydration.entries or not errors else None,
            hydration.entries,
            prepared.diagnostics,
            ParseStats(parsed=len(hydration.entries), failed=errors),
        )
        return SourceLease(registration.source_id, snapshot, snapshot.sha256, result, registration.source_artifact_id)

    def current_fingerprint(self, lease: SourceLease) -> str:
        active = self._lifecycle.active
        if active is None:
            return ""
        registry = SourceRegistrySnapshot.from_project_data(active.project.envelope.data)
        registration = next((item for item in registry.sources if item.source_id == lease.source_id), None)
        if registration is None or registration.source_artifact_id != lease.source_artifact_id:
            return ""
        return registration.fingerprint or ""
