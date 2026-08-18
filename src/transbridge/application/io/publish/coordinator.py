"""Staging, validation, backup, guarded replace, and cleanup coordinator."""

from __future__ import annotations

from dataclasses import replace
import errno
import hashlib
from pathlib import Path
import secrets

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.contracts import WriteRequest
from transbridge.application.security.paths import PathAuthorizationPolicy

from .filesystem import PublishFilesystemPort
from .guards import PublishCommitGuard
from .models import (
    BackupPolicy,
    ConflictPolicy,
    DebugArtifactPolicy,
    PublishManifest,
    PublishResult,
    PublishTarget,
    StagedArtifact,
    ValidationReport,
)
from .validators import ArtifactRenderer, ArtifactValidator


class PublishCoordinator:
    """The only owner of the final target replacement."""

    def __init__(
        self,
        filesystem: PublishFilesystemPort,
        *,
        path_policy: PathAuthorizationPolicy | None = None,
    ) -> None:
        self._filesystem = filesystem
        self._path_policy = path_policy

    def publish(
        self,
        request: WriteRequest,
        target: PublishTarget,
        *,
        renderer: ArtifactRenderer,
        validator: ArtifactValidator,
        commit_guard: PublishCommitGuard,
    ) -> PublishResult:
        run_id = request.context.run_id
        if run_id is None or not run_id.strip():
            return _failed(target.path, "RUN_ID_REQUIRED", "atomic publication requires an explicit run_id")
        if self._filesystem.canonicalize(request.target.uri) != self._filesystem.canonicalize(target.path):
            return _failed(target.path, "TARGET_MISMATCH", "write request and publish target do not match")
        prepared: StagedArtifact | None = None
        validation: ValidationReport | None = None
        result: PublishResult | None = None
        committed = False
        try:
            if _cancelled(request):
                result = _cancelled_result(target.path)
            else:
                prepared = self.prepare(target, run_id)
                result = self._render_validate_commit(
                    request,
                    target,
                    prepared,
                    renderer,
                    validator,
                    commit_guard,
                )
                validation = result.validation
                committed = result.published
        except Exception as exc:  # noqa: BLE001 - map all filesystem/adapter faults
            result = _exception_result(target.path, exc, validation)

        cleanup_error: BaseException | None = None
        if prepared is not None and not committed:
            retain = target.debug_policy is DebugArtifactPolicy.RETAIN_ON_FAILURE
            if retain:
                if result is not None:
                    result = replace(result, retained_staging_path=prepared.path)
            else:
                try:
                    self._filesystem.remove(prepared.path, missing_ok=True)
                except Exception as exc:  # noqa: BLE001 - cleanup evidence must be reported
                    cleanup_error = exc
        if result is None:
            result = _failed(target.path, "PUBLISH_INTERNAL_ERROR", "publication produced no result")
        if cleanup_error is not None:
            return replace(
                result,
                code="STAGING_CLEANUP_FAILED",
                message=f"staging cleanup failed ({type(cleanup_error).__name__})",
                retained_staging_path=prepared.path if prepared is not None else None,
            )
        return result

    def prepare(self, target: PublishTarget, run_id: str) -> StagedArtifact:
        target_path = self._filesystem.canonicalize(target.path)
        parent = str(Path(target_path).parent)
        if self._path_policy is not None:
            decision = self._path_policy.authorize(target_path, for_creation=not self._filesystem.exists(target_path))
            if not decision.allowed:
                raise PermissionError(decision.code)
        if not self._filesystem.exists(parent):
            raise FileNotFoundError("publish target parent must already exist")
        if not self._filesystem.atomic_replace_supported(target_path):
            raise OSError(errno.ENOTSUP, "atomic replace is unavailable for this filesystem")
        initial = self._filesystem.fingerprint(target_path)
        if target.expected_fingerprint is not None and initial != target.expected_fingerprint:
            raise FileExistsError("target fingerprint differs from the caller expectation")
        stage_name = f".{Path(target_path).name}.{secrets.token_urlsafe(18)}.stage"
        stage_path = self._filesystem.canonicalize(str(Path(parent) / stage_name))
        self._filesystem.exclusive_create(stage_path, mode=0o600)
        self._filesystem.chmod(stage_path, 0o600)
        if not self._filesystem.same_volume(stage_path, target_path):
            self._filesystem.remove(stage_path, missing_ok=True)
            raise OSError(errno.EXDEV, "staging and target are on different volumes")
        return StagedArtifact(stage_path, target_path, initial, run_id)

    def _render_validate_commit(
        self,
        request: WriteRequest,
        target: PublishTarget,
        staged: StagedArtifact,
        renderer: ArtifactRenderer,
        validator: ArtifactValidator,
        commit_guard: PublishCommitGuard,
    ) -> PublishResult:
        render_result = renderer.render(request, staged.path)
        if render_result.outcome is OperationOutcome.CANCELLED or _cancelled(request):
            return _cancelled_result(staged.target_path)
        if render_result.outcome is not OperationOutcome.COMPLETED:
            return _failed(staged.target_path, "RENDER_FAILED", "format adapter did not complete staging render")
        self._filesystem.fsync_file(staged.path)
        validation = validator.validate(request, staged.path)
        if not validation.valid:
            return _failed(
                staged.target_path,
                validation.code,
                validation.message,
                validation=validation,
            )
        staged_fingerprint = self._filesystem.fingerprint(staged.path)
        if not staged_fingerprint.exists or staged_fingerprint.sha256 is None:
            return _failed(staged.target_path, "STAGING_MISSING", "validated staging artifact disappeared")
        if _cancelled(request):
            return _cancelled_result(staged.target_path, validation)

        current = self._filesystem.fingerprint(staged.target_path)
        if current != staged.initial_target_fingerprint and target.conflict_policy is ConflictPolicy.FAIL:
            return _failed(
                staged.target_path,
                "TARGET_FINGERPRINT_CONFLICT",
                "publish target changed after staging was prepared",
                validation=validation,
            )
        backup_path = self._backup_if_required(target, staged.target_path, current)
        if _cancelled(request):
            return _cancelled_result(staged.target_path, validation, backup_path)
        if current.exists and current.mode is not None:
            self._filesystem.chmod(staged.path, current.mode)
        manifest = PublishManifest(
            1,
            staged.run_id,
            request.format_id,
            staged.target_path,
            staged_fingerprint.sha256,
            staged_fingerprint.size_bytes,
            None if request.source_snapshot is None else request.source_snapshot.sha256,
            current,
            validation.summary_sha256 or hashlib.sha256(b"").hexdigest(),
            backup_path,
        )
        if _cancelled(request):
            return _cancelled_result(staged.target_path, validation, backup_path)

        decision = commit_guard.commit(
            staged.run_id,
            lambda: self._filesystem.atomic_replace(staged.path, staged.target_path),
        )
        if not decision.accepted:
            if _cancelled(request):
                return _cancelled_result(staged.target_path, validation, backup_path)
            return _failed(
                staged.target_path,
                "COMMIT_GUARD_REJECTED",
                f"publication commit was rejected ({decision.reason or 'unknown'})",
                validation=validation,
                backup_path=backup_path,
            )
        try:
            self._filesystem.fsync_directory(str(Path(staged.target_path).parent))
        except OSError as exc:
            return PublishResult(
                OperationOutcome.PARTIAL,
                "DIRECTORY_FSYNC_FAILED",
                f"artifact was replaced but directory durability is uncertain ({type(exc).__name__})",
                staged.target_path,
                True,
                manifest,
                validation,
                backup_path,
            )
        return PublishResult(
            OperationOutcome.COMPLETED,
            "PUBLISHED",
            "artifact validated and atomically published",
            staged.target_path,
            True,
            manifest,
            validation,
            backup_path,
        )

    def _backup_if_required(self, target: PublishTarget, path: str, current) -> str | None:
        if not current.exists or target.backup_policy is BackupPolicy.NONE:
            return None
        digest = current.sha256 or "unknown"
        backup_name = f".{Path(path).name}.{digest[:12]}.{secrets.token_urlsafe(8)}.bak"
        backup_path = self._filesystem.canonicalize(str(Path(path).parent / backup_name))
        mode = current.mode if current.mode is not None else 0o600
        try:
            self._filesystem.copy_exclusive(path, backup_path, mode=mode)
        except Exception:
            raise
        if self._filesystem.fingerprint(backup_path).sha256 != current.sha256:
            self._filesystem.remove(backup_path, missing_ok=True)
            raise OSError("backup verification failed")
        return backup_path


def _cancelled(request: WriteRequest) -> bool:
    token = request.cancellation
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _failed(
    path: str,
    code: str,
    message: str,
    *,
    validation: ValidationReport | None = None,
    backup_path: str | None = None,
) -> PublishResult:
    return PublishResult(
        OperationOutcome.FAILED,
        code,
        message,
        path,
        validation=validation,
        backup_path=backup_path,
    )


def _cancelled_result(
    path: str,
    validation: ValidationReport | None = None,
    backup_path: str | None = None,
) -> PublishResult:
    return PublishResult(
        OperationOutcome.CANCELLED,
        "PUBLISH_CANCELLED",
        "publication was cancelled before atomic replace",
        path,
        validation=validation,
        backup_path=backup_path,
    )


def _exception_result(path: str, exc: Exception, validation: ValidationReport | None) -> PublishResult:
    if isinstance(exc, PermissionError):
        code = "PERMISSION_DENIED"
    elif isinstance(exc, FileExistsError):
        code = "TARGET_FINGERPRINT_CONFLICT"
    elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        code = "DISK_FULL"
    elif isinstance(exc, OSError) and exc.errno in {errno.EXDEV, errno.ENOTSUP}:
        code = "ATOMIC_REPLACE_UNAVAILABLE"
    else:
        code = "PUBLISH_IO_FAILED"
    error_number = getattr(exc, "errno", None)
    detail = type(exc).__name__ if error_number is None else f"{type(exc).__name__}, errno={error_number}"
    return _failed(path, code, f"publication failed ({detail})", validation=validation)
