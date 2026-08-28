from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.terminology.errors import RepositoryConflictError
from transbridge.application.terminology.models import ArtifactKind, ArtifactLedgerEntry, ArtifactStatus
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository


def test_only_one_concurrent_renderer_can_acquire_pending_artifact(tmp_path) -> None:
    first = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    second = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        pending = ArtifactLedgerEntry(
            "artifact-concurrent",
            "document-1",
            ArtifactKind.CHANGELOG_MARKDOWN,
            "renderer-v1",
            "document-digest",
            "changes.md",
        )
        first.put_artifact(pending)
        stale = second.get_artifact(pending.artifact_id)
        assert stale == pending

        rendering = replace(pending, status=ArtifactStatus.RENDERING, revision=1)
        first.update_artifact(
            rendering,
            expected_status=ArtifactStatus.PENDING,
            expected_revision=0,
        )

        with pytest.raises(RepositoryConflictError, match="expected artifact|revision changed"):
            second.update_artifact(
                rendering,
                expected_status=ArtifactStatus.PENDING,
                expected_revision=0,
            )

        assert second.get_artifact(pending.artifact_id) == rendering
    finally:
        second.close()
        first.close()


def test_failed_artifact_retry_reacquires_with_monotonic_revision(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        pending = ArtifactLedgerEntry(
            "artifact-retry",
            "document-1",
            ArtifactKind.CHANGELOG_EXCEL,
            "renderer-v1",
            "document-digest",
            "changes.xlsx",
        )
        repository.put_artifact(pending)
        rendering = repository.update_artifact(
            replace(pending, status=ArtifactStatus.RENDERING, revision=1),
            expected_status=ArtifactStatus.PENDING,
            expected_revision=0,
        )
        failed = repository.update_artifact(
            replace(rendering, status=ArtifactStatus.FAILED, retry_count=1, diagnostic="disk full", revision=2),
            expected_status=ArtifactStatus.RENDERING,
            expected_revision=1,
        )
        reacquired = repository.update_artifact(
            replace(failed, status=ArtifactStatus.RENDERING, diagnostic=None, revision=3),
            expected_status=ArtifactStatus.FAILED,
            expected_revision=2,
        )

        assert reacquired.retry_count == 1
        assert reacquired.revision == 3
    finally:
        repository.close()
