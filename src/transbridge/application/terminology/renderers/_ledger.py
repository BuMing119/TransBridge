"""Failure-isolated artifact delivery around immutable terminology facts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Protocol

from ..identity import canonical_digest
from ..models import ArtifactKind, ArtifactLedgerEntry, ArtifactStatus
from ._manifest import RenderedArtifact


class ArtifactLedgerPort(Protocol):
    def get_artifact(self, artifact_id: str) -> ArtifactLedgerEntry | None: ...

    def put_artifact(self, entry: ArtifactLedgerEntry) -> ArtifactLedgerEntry: ...

    def update_artifact(
        self,
        entry: ArtifactLedgerEntry,
        *,
        expected_status: ArtifactStatus,
        expected_revision: int,
    ) -> ArtifactLedgerEntry: ...


class ArtifactRenderError(RuntimeError):
    pass


def pending_artifact(
    *,
    owner_ref: str,
    owner_digest: str,
    kind: ArtifactKind,
    renderer_version: str,
    target: str,
) -> ArtifactLedgerEntry:
    payload = (owner_ref, owner_digest, kind.value, renderer_version, target)
    artifact_id = canonical_digest(payload, namespace="terminology.artifact-ledger.v1")
    return ArtifactLedgerEntry(
        artifact_id,
        owner_ref,
        kind,
        renderer_version,
        owner_digest,
        target,
    )


class ArtifactRenderCoordinator:
    """Update only mutable delivery state when a renderer succeeds or fails."""

    def __init__(self, ledger: ArtifactLedgerPort) -> None:
        self._ledger = ledger

    def render(
        self,
        pending: ArtifactLedgerEntry,
        operation: Callable[[], RenderedArtifact],
    ) -> tuple[RenderedArtifact, ArtifactLedgerEntry]:
        current = self._ledger.get_artifact(pending.artifact_id)
        if current is None:
            current = self._ledger.put_artifact(pending)
        if current.status not in {ArtifactStatus.PENDING, ArtifactStatus.FAILED}:
            raise ArtifactRenderError(
                f"artifact is {current.status.value}; only one renderer can own a pending delivery attempt"
            )
        acquired = replace(
            current,
            status=ArtifactStatus.RENDERING,
            diagnostic=None,
            revision=current.revision + 1,
        )
        acquired = self._ledger.update_artifact(
            acquired,
            expected_status=current.status,
            expected_revision=current.revision,
        )
        try:
            result = operation()
        except Exception as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            self._ledger.update_artifact(
                replace(
                    acquired,
                    status=ArtifactStatus.FAILED,
                    retry_count=acquired.retry_count + 1,
                    diagnostic=diagnostic,
                    revision=acquired.revision + 1,
                ),
                expected_status=ArtifactStatus.RENDERING,
                expected_revision=acquired.revision,
            )
            raise ArtifactRenderError(
                "artifact rendering failed; immutable terminology facts were not changed"
            ) from exc
        succeeded = self._ledger.update_artifact(
            replace(
                acquired,
                target=str(result.path),
                status=ArtifactStatus.SUCCEEDED,
                diagnostic=None,
                revision=acquired.revision + 1,
            ),
            expected_status=ArtifactStatus.RENDERING,
            expected_revision=acquired.revision,
        )
        return result, succeeded


__all__ = [
    "ArtifactLedgerPort",
    "ArtifactRenderCoordinator",
    "ArtifactRenderError",
    "pending_artifact",
]
