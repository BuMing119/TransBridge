"""Validated same-volume download and atomic publication for ParaTranz artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import secrets
import time
from typing import Any
from zipfile import BadZipFile, ZipFile

from transbridge.application.contracts import (
    Diagnostic,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.publish import (
    FileFingerprint,
    OsPublishFilesystem,
    PublishCommitGuard,
    PublishFilesystemPort,
)
from transbridge.application.ports.paratranz import CancellationPort, ExternalServiceError, ParaTranzPort

from .models import canonical_hash


@dataclass(frozen=True, slots=True)
class ArtifactPublishRequest:
    project_id: int
    target_path: str
    run_id: str
    commit_guard: PublishCommitGuard
    cancellation: CancellationPort | None = None
    expected_sha256: str | None = None
    expected_target: FileFingerprint | None = None
    poll_interval_seconds: float = 1.0
    max_poll_attempts: int = 60

    def __post_init__(self) -> None:
        if isinstance(self.project_id, bool) or not isinstance(self.project_id, int) or self.project_id < 1:
            raise ValueError("project_id must be a positive integer")
        if not self.target_path.strip() or not self.run_id.strip():
            raise ValueError("artifact target_path and run_id must not be empty")
        if self.expected_sha256 is not None and not _is_sha256(self.expected_sha256):
            raise ValueError("expected artifact hash must be a lowercase SHA-256 digest")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll interval must not be negative")
        if (
            isinstance(self.max_poll_attempts, bool)
            or not isinstance(self.max_poll_attempts, int)
            or self.max_poll_attempts < 1
        ):
            raise ValueError("max_poll_attempts must be a positive integer")


@dataclass(frozen=True, slots=True)
class ArtifactPublishManifest:
    schema_version: int
    run_id: str
    project_id: int
    artifact_identity: str
    target_path: str
    sha256: str
    size_bytes: int
    previous_target: FileFingerprint
    zip_members: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported artifact manifest schema version")
        if not self.run_id.strip() or not self.artifact_identity.strip():
            raise ValueError("artifact manifest identity fields must not be empty")
        if not _is_sha256(self.sha256) or self.size_bytes < 1:
            raise ValueError("artifact manifest hash or size is invalid")
        if not self.zip_members:
            raise ValueError("artifact manifest requires at least one ZIP member")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "artifact_identity": self.artifact_identity,
            "target_path": self.target_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "previous_target": {
                "exists": self.previous_target.exists,
                "sha256": self.previous_target.sha256,
                "size_bytes": self.previous_target.size_bytes,
                "mode": self.previous_target.mode,
            },
            "zip_members": list(self.zip_members),
        }


class ParaTranzArtifactPublisher:
    """Trigger, poll, validate and atomically publish a ParaTranz ZIP."""

    def __init__(
        self,
        remote: ParaTranzPort,
        *,
        filesystem: PublishFilesystemPort | None = None,
    ) -> None:
        self._remote = remote
        self._filesystem = filesystem or OsPublishFilesystem()

    def publish(self, request: ArtifactPublishRequest) -> OperationResult[dict]:
        target = self._filesystem.canonicalize(request.target_path)
        run_hash = hashlib.sha256(request.run_id.encode()).hexdigest()[:16]
        stage = f"{target}.{run_hash}.{secrets.token_hex(8)}.part"
        initial = self._filesystem.fingerprint(target)
        expected_target = request.expected_target or initial
        published = False
        manifest: ArtifactPublishManifest | None = None
        try:
            if request.expected_target is not None and initial != request.expected_target:
                raise _ArtifactFailure("ARTIFACT_TARGET_CHANGED", "The artifact target changed before execution.")
            _raise_if_cancelled(request.cancellation)
            baseline = self._remote.get_artifacts(
                request.project_id,
                cancellation=request.cancellation,
            )
            _raise_if_cancelled(request.cancellation)
            self._remote.trigger_export(
                request.project_id,
                cancellation=request.cancellation,
            )
            artifact = self._poll_artifact(request, baseline)
            _raise_if_cancelled(request.cancellation)
            self._filesystem.exclusive_create(stage, mode=0o600)
            downloaded = self._remote.download_artifact(
                request.project_id,
                stage,
                cancellation=request.cancellation,
            )
            if self._filesystem.canonicalize(downloaded) != stage:
                raise _ArtifactFailure(
                    "ARTIFACT_STAGING_MISMATCH",
                    "The remote adapter did not use the requested staging path.",
                )
            _raise_if_cancelled(request.cancellation)
            self._filesystem.fsync_file(stage)
            fingerprint = self._filesystem.fingerprint(stage)
            expected_hash = request.expected_sha256 or _metadata_hash(artifact)
            if expected_hash is not None and fingerprint.sha256 != expected_hash:
                raise _ArtifactFailure("ARTIFACT_HASH_MISMATCH", "The downloaded artifact hash is invalid.")
            members = _validate_zip(self._filesystem.read_bytes(stage))
            _raise_if_cancelled(request.cancellation)
            if not self._filesystem.same_volume(stage, target):
                raise _ArtifactFailure(
                    "ARTIFACT_CROSS_VOLUME",
                    "The artifact staging path is not on the target volume.",
                )
            if not self._filesystem.atomic_replace_supported(target):
                raise _ArtifactFailure("ATOMIC_REPLACE_UNAVAILABLE", "Atomic artifact replacement is unavailable.")
            manifest = ArtifactPublishManifest(
                1,
                request.run_id,
                request.project_id,
                _artifact_identity(artifact),
                target,
                fingerprint.sha256 or "",
                fingerprint.size_bytes,
                initial,
                members,
            )

            def mutation() -> None:
                nonlocal published
                if self._filesystem.fingerprint(target) != expected_target:
                    raise _ArtifactFailure("ARTIFACT_TARGET_CHANGED", "The artifact target changed before commit.")
                self._filesystem.atomic_replace(stage, target)
                published = True

            _raise_if_cancelled(request.cancellation)
            decision = request.commit_guard.commit(request.run_id, mutation)
            if not decision.accepted:
                raise _ArtifactFailure("ARTIFACT_COMMIT_REJECTED", "The artifact commit was rejected.")
            try:
                self._filesystem.fsync_directory(str(Path(target).parent))
            except OSError:
                return _artifact_partial(
                    request.run_id,
                    target,
                    manifest,
                    "ARTIFACT_DIRECTORY_FSYNC_FAILED",
                    "The artifact was replaced, but directory durability could not be confirmed.",
                )
            value = {"manifest": manifest.to_dict()}
            return OperationResult.completed(
                value,
                counts=OperationCounts(succeeded=1),
                artifact_refs=(target,),
                run_id=request.run_id,
            )
        except _ArtifactCancelled:
            if published and manifest is not None:
                return _artifact_partial(
                    request.run_id,
                    target,
                    manifest,
                    "ARTIFACT_COMMITTED_BEFORE_CANCEL",
                    "The artifact committed before cancellation won arbitration.",
                )
            self._cleanup(stage)
            return OperationResult.cancelled(
                Diagnostic(
                    "ARTIFACT_CANCELLED",
                    "The artifact publication was cancelled.",
                    category=ErrorCategory.CANCELLED,
                ),
                run_id=request.run_id,
            )
        except ExternalServiceError as exc:
            if exc.category.value == "cancelled" or (
                request.cancellation is not None and request.cancellation.is_cancelled
            ):
                self._cleanup(stage)
                return OperationResult.cancelled(
                    Diagnostic(
                        "ARTIFACT_CANCELLED",
                        "The artifact publication was cancelled.",
                        category=ErrorCategory.CANCELLED,
                    ),
                    run_id=request.run_id,
                )
            self._cleanup(stage)
            return _artifact_failed(
                request.run_id,
                f"REMOTE_{exc.category.value.upper()}",
                "The remote artifact operation failed.",
                retryable=_retryable_external(exc),
            )
        except _ArtifactFailure as exc:
            if published and manifest is not None:
                return _artifact_partial(request.run_id, target, manifest, exc.code, exc.safe_message)
            self._cleanup(stage)
            return _artifact_failed(request.run_id, exc.code, exc.safe_message, retryable=exc.retryable)
        except Exception:
            if published and manifest is not None:
                return _artifact_partial(
                    request.run_id,
                    target,
                    manifest,
                    "ARTIFACT_POST_COMMIT_EVIDENCE_FAILED",
                    "The artifact committed, but final durability evidence failed.",
                )
            if request.cancellation is not None and request.cancellation.is_cancelled:
                self._cleanup(stage)
                return OperationResult.cancelled(
                    Diagnostic(
                        "ARTIFACT_CANCELLED",
                        "The artifact publication was cancelled.",
                        category=ErrorCategory.CANCELLED,
                    ),
                    run_id=request.run_id,
                )
            self._cleanup(stage)
            return _artifact_failed(
                request.run_id,
                "ARTIFACT_PUBLISH_FAILED",
                "The artifact could not be published.",
                retryable=True,
            )

    def _poll_artifact(
        self,
        request: ArtifactPublishRequest,
        baseline: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        baseline_states = {_artifact_identity(dict(item)): _artifact_ready(dict(item)) for item in baseline}
        for attempt in range(request.max_poll_attempts):
            _raise_if_cancelled(request.cancellation)
            try:
                artifacts = self._remote.get_artifacts(
                    request.project_id,
                    cancellation=request.cancellation,
                )
            except ExternalServiceError as exc:
                if not _retryable_external(exc) or attempt + 1 >= request.max_poll_attempts:
                    raise
                _wait(request.cancellation, request.poll_interval_seconds)
                continue
            _raise_if_cancelled(request.cancellation)
            for artifact in artifacts:
                value = dict(artifact)
                identity = _artifact_identity(value)
                was_ready = baseline_states.get(identity)
                if _artifact_ready(value) and (was_ready is None or not was_ready):
                    return value
            if attempt + 1 < request.max_poll_attempts:
                _wait(request.cancellation, request.poll_interval_seconds)
        raise _ArtifactFailure("ARTIFACT_POLL_TIMEOUT", "The exported artifact did not become ready.", retryable=True)

    def _cleanup(self, stage: str) -> None:
        try:
            self._filesystem.remove(stage, missing_ok=True)
        except OSError:
            pass


class _ArtifactFailure(RuntimeError):
    def __init__(self, code: str, safe_message: str, *, retryable: bool = False) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class _ArtifactCancelled(RuntimeError):
    pass


def _raise_if_cancelled(cancellation: CancellationPort | None) -> None:
    if cancellation is not None and cancellation.is_cancelled:
        raise _ArtifactCancelled


def _wait(cancellation: CancellationPort | None, seconds: float) -> None:
    if cancellation is None:
        time.sleep(seconds)
    elif cancellation.wait(seconds):
        raise _ArtifactCancelled


def _metadata_hash(artifact: dict[str, Any]) -> str | None:
    for name in ("sha256", "sha256sum", "hash"):
        value = artifact.get(name)
        if isinstance(value, str) and _is_sha256(value.lower()):
            return value.lower()
    return None


def _artifact_ready(artifact: dict[str, Any]) -> bool:
    status = artifact.get("status")
    if status is None:
        return True
    if isinstance(status, bool):
        return status
    if isinstance(status, int):
        return status == 2
    if isinstance(status, str):
        return status.casefold() in {"2", "completed", "ready", "success", "succeeded"}
    return False


def _artifact_identity(artifact: dict[str, Any]) -> str:
    for name in ("id", "artifactId", "artifact_id", "revision"):
        value = artifact.get(name)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return f"{name}:{value}"
    return canonical_hash(artifact)


def _retryable_external(error: ExternalServiceError) -> bool:
    return error.category.value in {"rate_limited", "timeout", "unavailable", "transport"}


def _validate_zip(content: bytes) -> tuple[str, ...]:
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = tuple(info.filename for info in archive.infolist() if not info.is_dir())
            if not members:
                raise _ArtifactFailure("ARTIFACT_EMPTY", "The exported ZIP contains no files.")
            for name in members:
                normalized = name.replace("\\", "/")
                path = PurePosixPath(normalized)
                if path.is_absolute() or ".." in path.parts:
                    raise _ArtifactFailure("ARTIFACT_UNSAFE_PATH", "The exported ZIP contains an unsafe path.")
            if archive.testzip() is not None:
                raise _ArtifactFailure("ARTIFACT_CRC_INVALID", "The exported ZIP failed its integrity check.")
            return members
    except BadZipFile:
        raise _ArtifactFailure("ARTIFACT_INVALID_ZIP", "The downloaded artifact is not a valid ZIP.") from None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _artifact_failed(run_id: str, code: str, message: str, *, retryable: bool = False) -> OperationResult[dict]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, message, category=ErrorCategory.EXTERNAL, retryable=retryable),),
        counts=OperationCounts(failed=1),
        run_id=run_id,
    )


def _artifact_partial(
    run_id: str,
    target: str,
    manifest: ArtifactPublishManifest,
    code: str,
    message: str,
) -> OperationResult[dict]:
    return OperationResult.partial(
        {"manifest": manifest.to_dict()},
        diagnostics=(Diagnostic(code, message, category=ErrorCategory.INTERNAL, retryable=True),),
        counts=OperationCounts(succeeded=1, failed=1),
        artifact_refs=(target,),
        run_id=run_id,
    )
