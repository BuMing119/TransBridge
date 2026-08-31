from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from transbridge.application.contracts import OperationOutcome, OperationResult
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.io.publish import CommitDecision, ImmediateCommitGuard, OsPublishFilesystem
from transbridge.application.ports.paratranz import (
    ExternalServiceCategory,
    ExternalServiceError,
    ParaTranzEntry,
)
from transbridge.application.sync import (
    ArtifactPublishRequest,
    AuthorizedSyncPlan,
    CallbackLocalSyncUnitOfWork,
    ExecuteSyncRequest,
    LocalEntrySnapshot,
    ParaTranzArtifactPublisher,
    ParaTranzSyncExecutor,
    ParaTranzSyncTaskDraft,
    ParaTranzSyncTaskEntrypoint,
    ParaTranzSyncTaskPreparation,
    RemoteEntrySnapshot,
    RetryToken,
    SyncOperation,
    SyncPlanner,
)
from transbridge.application.sync.models import canonical_hash
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime
from transbridge.paratranz.service import ParaTranzService

NAMESPACE = SourceNamespace("test:paratranz-sync")


class _TaskIds:
    def new_id(self) -> str:
        return "paratranz-recovery-run"


class _TaskClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 30, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


class Token:
    def __init__(self) -> None:
        self.cancelled = False

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    def wait(self, timeout=None) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")


class ControlledRemote:
    def __init__(self, entries=()) -> None:
        self.entries = {entry.key: entry for entry in entries}
        self.fail_keys: set[str] = set()
        self.cancel_after_key: str | None = None
        self.token: Token | None = None
        self.calls: list[tuple[str, str]] = []
        self.next_id = 100

    def fetch(self, project_id, namespace, *, limit, cancellation=None):
        assert project_id == 7
        assert namespace == NAMESPACE
        assert len(self.entries) <= limit
        return tuple(_remote_snapshot(entry) for entry in self.entries.values())

    def upsert_entry(self, project_id, entry, *, force_overwrite=False, cancellation=None):
        self.calls.append(("upsert", entry.key))
        if entry.key in self.fail_keys:
            raise ExternalServiceError(
                ExternalServiceCategory.RATE_LIMITED,
                "safe controlled failure",
                status=429,
            )
        existing = self.entries.get(entry.key)
        remote_id = existing.remote_id if existing is not None else self.next_id
        if existing is None:
            self.next_id += 1
        result = ParaTranzEntry(
            remote_id,
            entry.key,
            entry.original,
            entry.translation,
            entry.context,
            entry.stage,
        )
        self.entries[entry.key] = result
        if self.cancel_after_key == entry.key and self.token is not None:
            self.token.cancelled = True
        return result

    def delete_entry(self, project_id, remote_id, *, cancellation=None):
        self.calls.append(("delete", str(remote_id)))
        key = next(key for key, entry in self.entries.items() if entry.remote_id == remote_id)
        del self.entries[key]


def _local(key: str, translation: str) -> LocalEntrySnapshot:
    return LocalEntrySnapshot(
        EntryKey(NAMESPACE, key),
        EntryRevision(),
        f"original-{key}",
        translation,
        "context",
        1,
    )


def _remote_snapshot(entry: ParaTranzEntry) -> RemoteEntrySnapshot:
    revision = canonical_hash({
        "id": entry.remote_id,
        "key": entry.key,
        "original": entry.original,
        "translation": entry.translation,
        "context": entry.context,
        "stage": entry.stage,
    })
    reference = ExternalEntryRef("paratranz", "project:7", entry.remote_id)
    return RemoteEntrySnapshot(
        EntryKey(NAMESPACE, entry.key),
        revision,
        entry.original,
        entry.translation,
        entry.context,
        entry.stage,
        reference,
    )


def _executor(remote: ControlledRemote, official: list[LocalEntrySnapshot], replace=None):
    replacement = replace or (lambda entries: official.__setitem__(slice(None), entries))
    uow = CallbackLocalSyncUnitOfWork(lambda: tuple(official), replacement)
    return ParaTranzSyncExecutor(remote, remote, uow)


def _request(plan, local, *, token=None, cancellation=None, confirmation="NOT_REQUIRED"):
    return ExecuteSyncRequest(
        AuthorizedSyncPlan(plan, "owner-1", confirmation),
        7,
        NAMESPACE,
        tuple(local),
        "run-1",
        ImmediateCommitGuard("run-1"),
        cancellation=cancellation,
        retry_token=token,
    )


def test_sync_task_creates_recovery_point_before_executor_runs() -> None:
    local = (_local("a", "A"),)
    plan = SyncPlanner().plan(local, (), operation=SyncOperation.UPLOAD)
    authorized = AuthorizedSyncPlan(plan, "owner-1", "NOT_REQUIRED")
    events = []
    executor = MagicMock()
    executor.execute.side_effect = lambda _request: events.append("execute") or OperationResult.completed({})
    runtime = TaskRuntime(
        id_generator=_TaskIds(),
        clock=_TaskClock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    owner = OwnerRef("owner-1", "gui")

    ref = ParaTranzSyncTaskEntrypoint(runtime, executor).submit(
        ParaTranzSyncTaskDraft(
            authorized,
            7,
            NAMESPACE,
            local,
            recovery_snapshot=lambda _run_id: events.append("recovery"),
            recovery_snapshot_name="下载前还原点",
            preparation=lambda _run_id: events.append("prepare") or ParaTranzSyncTaskPreparation(authorized, local),
        ),
        owner,
    )

    assert events == ["prepare", "recovery", "execute"]
    assert runtime.get(ref, owner).state is JobState.COMPLETED


def test_sync_task_submit_returns_before_remote_preparation_starts() -> None:
    local = (_local("a", "A"),)
    plan = SyncPlanner().plan(local, (), operation=SyncOperation.UPLOAD)
    authorized = AuthorizedSyncPlan(plan, "owner-1", "NOT_REQUIRED")
    queued = []
    events = []
    executor = MagicMock()
    executor.execute.return_value = OperationResult.completed({})
    runtime = TaskRuntime(
        id_generator=_TaskIds(),
        clock=_TaskClock(),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )
    owner = OwnerRef("owner-1", "gui")

    ref = ParaTranzSyncTaskEntrypoint(runtime, executor).submit(
        ParaTranzSyncTaskDraft(
            authorized,
            7,
            NAMESPACE,
            local,
            preparation=lambda _run_id: events.append("prepare") or ParaTranzSyncTaskPreparation(authorized, local),
        ),
        owner,
    )

    assert events == []
    assert len(queued) == 1
    assert runtime.get(ref, owner).state is JobState.RUNNING

    queued[0]()
    assert events == ["prepare"]
    assert runtime.get(ref, owner).state is JobState.COMPLETED


def test_partial_remote_failure_and_retry_do_not_repeat_confirmed_success() -> None:
    local = [_local("a", "A"), _local("b", "B")]
    remote = ControlledRemote()
    plan = SyncPlanner().plan(
        local,
        (),
        operation=SyncOperation.UPLOAD,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )
    remote.fail_keys.add("b")

    first = _executor(remote, local).execute(_request(plan, local))

    assert first.outcome is OperationOutcome.PARTIAL
    assert first.counts.succeeded == 1
    assert first.counts.failed == 1
    retry = RetryToken.from_dict(first.value["retry_token"])
    assert remote.calls == [("upsert", "a"), ("upsert", "b")]

    remote.fail_keys.clear()
    second = _executor(remote, local).execute(_request(plan, local, token=retry))

    assert second.outcome is OperationOutcome.COMPLETED
    assert second.counts.succeeded == 1
    assert second.counts.skipped == 1
    assert remote.calls == [("upsert", "a"), ("upsert", "b"), ("upsert", "b")]

    projection = second.to_dict()
    assert OperationResult.from_dict(projection).to_dict() == projection


def test_local_download_uses_one_atomic_candidate_and_preserves_official_on_fault() -> None:
    remote = ControlledRemote((
        ParaTranzEntry(1, "a", "oa", "A", "", 1),
        ParaTranzEntry(2, "b", "ob", "B", "", 1),
    ))
    official: list[LocalEntrySnapshot] = []
    plan = SyncPlanner().plan(
        (),
        remote.fetch(7, NAMESPACE, limit=10),
        operation=SyncOperation.DOWNLOAD,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )

    def fail_replace(entries):
        raise OSError("controlled disk fault")

    result = _executor(remote, official, fail_replace).execute(_request(plan, official, confirmation="CONFIRMED"))

    assert result.outcome is OperationOutcome.FAILED
    assert result.counts.failed == 2
    assert official == []


def test_late_cancellation_records_remote_commit_and_retry_skips_it() -> None:
    local = [_local("a", "A"), _local("b", "B")]
    remote = ControlledRemote()
    token = Token()
    remote.token = token
    remote.cancel_after_key = "a"
    plan = SyncPlanner().plan(
        local,
        (),
        operation=SyncOperation.UPLOAD,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )

    result = _executor(remote, local).execute(_request(plan, local, cancellation=token))

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == 1
    assert result.counts.cancelled == 1
    retry = RetryToken.from_dict(result.value["retry_token"])
    assert len(retry.confirmed_item_ids) == 1
    assert remote.calls == [("upsert", "a")]


def test_retry_token_rejects_tampering() -> None:
    local = [_local("a", "A")]
    remote = ControlledRemote()
    plan = SyncPlanner().plan(
        local,
        (),
        operation=SyncOperation.UPLOAD,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )
    result = _executor(remote, local).execute(_request(plan, local))
    payload = result.value["retry_token"]
    payload["owner_id"] = "attacker"

    with pytest.raises(ValueError, match="does not match"):
        RetryToken.from_dict(payload)


class ArtifactRemote:
    def __init__(self, content: bytes, *, cancel_on_download: Token | None = None) -> None:
        self.content = content
        self.triggered = False
        self.cancel_on_download = cancel_on_download

    def get_artifacts(self, project_id, *, cancellation=None):
        if self.triggered:
            return ({"id": 2, "status": "ready"},)
        return ({"id": 1, "status": "ready"},)

    def trigger_export(self, project_id, *, cancellation=None):
        self.triggered = True
        return {"job": "export-2"}

    def download_artifact(self, project_id, destination, *, cancellation=None):
        Path(destination).write_bytes(self.content)
        if self.cancel_on_download is not None:
            self.cancel_on_download.cancelled = True
        return destination


class FlakyArtifactRemote(ArtifactRemote):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.failed_poll = False

    def get_artifacts(self, project_id, *, cancellation=None):
        if self.triggered and not self.failed_poll:
            self.failed_poll = True
            raise ExternalServiceError(
                ExternalServiceCategory.UNAVAILABLE,
                "controlled transient failure",
                status=503,
            )
        return super().get_artifacts(project_id, cancellation=cancellation)


def _zip_bytes() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("translations/result.json", "{}")
    return output.getvalue()


def test_artifact_publish_validates_zip_and_atomically_replaces_target(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    publisher = ParaTranzArtifactPublisher(ArtifactRemote(_zip_bytes()))

    result = publisher.publish(
        ArtifactPublishRequest(
            7,
            str(target),
            "artifact-run",
            ImmediateCommitGuard("artifact-run"),
            poll_interval_seconds=0,
        )
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert target.read_bytes() == _zip_bytes()
    assert result.value["manifest"]["zip_members"] == ["translations/result.json"]
    assert list(tmp_path.glob("*.part")) == []


def test_invalid_artifact_never_replaces_previous_target(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    publisher = ParaTranzArtifactPublisher(ArtifactRemote(b"not-a-zip"))

    result = publisher.publish(
        ArtifactPublishRequest(
            7,
            str(target),
            "artifact-run",
            ImmediateCommitGuard("artifact-run"),
            poll_interval_seconds=0,
        )
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "ARTIFACT_INVALID_ZIP"
    assert target.read_bytes() == b"old-artifact"
    assert list(tmp_path.glob("*.part")) == []


def test_artifact_poll_retries_transient_service_failure(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    expected = _zip_bytes()
    publisher = ParaTranzArtifactPublisher(FlakyArtifactRemote(expected))

    result = publisher.publish(
        ArtifactPublishRequest(
            7,
            str(target),
            "artifact-run",
            ImmediateCommitGuard("artifact-run"),
            poll_interval_seconds=0,
            max_poll_attempts=2,
        )
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert target.read_bytes() == expected


def test_artifact_hash_mismatch_keeps_previous_target(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    publisher = ParaTranzArtifactPublisher(ArtifactRemote(_zip_bytes()))

    result = publisher.publish(
        ArtifactPublishRequest(
            7,
            str(target),
            "artifact-run",
            ImmediateCommitGuard("artifact-run"),
            expected_sha256="0" * 64,
            poll_interval_seconds=0,
        )
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "ARTIFACT_HASH_MISMATCH"
    assert target.read_bytes() == b"old-artifact"


class RejectingGuard:
    def commit(self, run_id, mutation):
        return CommitDecision(False, "terminal")


def test_terminal_guard_rejection_keeps_previous_artifact(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    publisher = ParaTranzArtifactPublisher(ArtifactRemote(_zip_bytes()))

    result = publisher.publish(
        ArtifactPublishRequest(7, str(target), "artifact-run", RejectingGuard(), poll_interval_seconds=0)
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "ARTIFACT_COMMIT_REJECTED"
    assert target.read_bytes() == b"old-artifact"


class ReplaceFaultFilesystem(OsPublishFilesystem):
    def atomic_replace(self, source: str, destination: str) -> None:
        raise OSError("controlled replace fault")


class DirectoryFsyncFaultFilesystem(OsPublishFilesystem):
    def fsync_directory(self, path: str) -> None:
        raise OSError("controlled directory fsync fault")


def test_replace_fault_keeps_previous_artifact(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    publisher = ParaTranzArtifactPublisher(
        ArtifactRemote(_zip_bytes()),
        filesystem=ReplaceFaultFilesystem(),
    )

    result = publisher.publish(
        ArtifactPublishRequest(7, str(target), "artifact-run", ImmediateCommitGuard("artifact-run"))
    )

    assert result.outcome is OperationOutcome.FAILED
    assert target.read_bytes() == b"old-artifact"


def test_post_replace_durability_fault_is_partial_not_false_failure(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    expected = _zip_bytes()
    publisher = ParaTranzArtifactPublisher(
        ArtifactRemote(expected),
        filesystem=DirectoryFsyncFaultFilesystem(),
    )

    result = publisher.publish(
        ArtifactPublishRequest(7, str(target), "artifact-run", ImmediateCommitGuard("artifact-run"))
    )

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.diagnostics[0].code == "ARTIFACT_DIRECTORY_FSYNC_FAILED"
    assert target.read_bytes() == expected


def test_cancellation_after_download_never_replaces_previous_artifact(tmp_path: Path) -> None:
    target = tmp_path / "export.zip"
    target.write_bytes(b"old-artifact")
    token = Token()
    publisher = ParaTranzArtifactPublisher(ArtifactRemote(_zip_bytes(), cancel_on_download=token))

    result = publisher.publish(
        ArtifactPublishRequest(
            7,
            str(target),
            "artifact-run",
            ImmediateCommitGuard("artifact-run"),
            cancellation=token,
        )
    )

    assert result.outcome is OperationOutcome.CANCELLED
    assert target.read_bytes() == b"old-artifact"


def test_typed_service_forwards_delete_and_download_cancellation(tmp_path: Path) -> None:
    projects = MagicMock()
    strings = MagicMock()
    history = MagicMock()
    exports = MagicMock()
    service = ParaTranzService(projects, strings, history, exports)
    token = Token()
    destination = str(tmp_path / "artifact.part")
    exports.download_artifacts.return_value = destination

    service.delete_entry(7, 42, cancellation=token)
    result = service.download_artifact(7, destination, cancellation=token)

    strings.delete_string.assert_called_once_with(7, 42, cancellation=token)
    exports.download_artifacts.assert_called_once_with(7, destination, cancellation=token)
    assert result == destination
