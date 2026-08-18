"""FOMOD staging build, reopen-validation and atomic publication.

Every pack output goes to a same-volume sibling staging path first; the
official target is only ever touched through one guarded atomic replace.  A
successful run writes a :class:`FomodManifest` that corresponds to the exact
input hashes, policies and run_id of the archive, and staging is cleaned
according to the chosen policy with leftovers reported as diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, OperationOutcome
from transbridge.fileops.archive import inspect_archive, pack
from transbridge.fileops.archive_policy import ArchivePolicy, ArchivePolicyError

from .models import FomodRunSpec


class CleanupPolicy(StrEnum):
    CLEAN_ALWAYS = "clean_always"
    RETAIN_ON_FAILURE = "retain_on_failure"
    RETAIN_ALWAYS = "retain_always"


@dataclass(frozen=True, slots=True)
class StagedPack:
    """One immutable staged archive pending validated replace."""

    path: str
    target: str
    initial_target_sha256: str | None = None
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class FomodManifest:
    """Corresponds the published archive to its exact input, policies and run."""

    schema_version: int = 1
    run_id: str = ""
    target_locale: str = ""
    config_hash: str = ""
    output_archive: str = ""
    output_format: str = ""
    new_archive_hash: str = ""
    old_archive_hash: str | None = None
    policy_ids: tuple[str, ...] = ()
    build_fingerprint: str = ""
    artifact_sha256: str = ""
    artifact_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "target_locale": self.target_locale,
            "config_hash": self.config_hash,
            "output_archive": self.output_archive,
            "output_format": self.output_format,
            "new_archive_hash": self.new_archive_hash,
            "old_archive_hash": self.old_archive_hash,
            "policy_ids": list(self.policy_ids),
            "build_fingerprint": self.build_fingerprint,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size": self.artifact_size,
        }

    @classmethod
    def from_dict(cls, value: Any) -> FomodManifest:
        if not isinstance(value, dict):
            raise ValueError("FOMOD manifest root must be an object")
        if value.get("schema_version") != 1:
            raise ValueError("unsupported FOMOD manifest schema version")
        return cls(
            run_id=str(value["run_id"]),
            target_locale=str(value["target_locale"]),
            config_hash=str(value["config_hash"]),
            output_archive=str(value["output_archive"]),
            output_format=str(value["output_format"]),
            new_archive_hash=str(value["new_archive_hash"]),
            old_archive_hash=value.get("old_archive_hash"),
            policy_ids=tuple(value.get("policy_ids", ())),
            build_fingerprint=str(value["build_fingerprint"]),
            artifact_sha256=str(value["artifact_sha256"]),
            artifact_size=int(value["artifact_size"]),
        )

    @classmethod
    def from_spec(
        cls,
        spec: FomodRunSpec,
        *,
        build_fingerprint: str,
        artifact_sha256: str,
        artifact_size: int,
    ) -> FomodManifest:
        return cls(
            run_id=spec.run_id,
            target_locale=spec.target_locale,
            config_hash=spec.config_hash,
            output_archive=spec.output_archive,
            output_format=spec.output_format,
            new_archive_hash=spec.new_archive_hash,
            old_archive_hash=spec.old_archive_hash,
            policy_ids=spec.policies.as_tuple(),
            build_fingerprint=build_fingerprint,
            artifact_sha256=artifact_sha256,
            artifact_size=artifact_size,
        )


@dataclass(frozen=True, slots=True)
class StagingPublishResult:
    outcome: OperationOutcome
    code: str
    message: str
    target: str
    published: bool = False
    staged_path: str | None = None
    manifest_path: str | None = None
    artifact_sha256: str | None = None
    artifact_size: int | None = None
    backup_path: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def succeeded(self) -> bool:
        return self.published and self.outcome is OperationOutcome.COMPLETED


class TargetFingerprintConflict(Exception):
    pass


class StagingPackPublisher:
    """The only owner of the official FOMOD output replacement."""

    def __init__(self, policy: ArchivePolicy | None = None) -> None:
        self._policy = policy

    def publish(
        self,
        spec: FomodRunSpec,
        build_dir: str,
        *,
        cancellation: object | None = None,
        commit_guard: Callable[[str, Callable[[], None]], bool] | None = None,
        cleanup_policy: CleanupPolicy = CleanupPolicy.CLEAN_ALWAYS,
        expected_target_sha256: str | None = None,
        build_fingerprint: str = "",
        progress: Callable[[int, int], None] | None = None,
    ) -> StagingPublishResult:
        guard = commit_guard or (lambda run_id, mutation: (mutation(), True)[1])
        if _cancelled(cancellation):
            return _result(
                OperationOutcome.CANCELLED,
                "PUBLISH_CANCELLED",
                "cancelled before staging",
                spec.output_archive,
            )

        target = Path(spec.output_archive)
        target_parent = target.parent
        if not target_parent.exists():
            return _result(
                OperationOutcome.FAILED,
                "TARGET_PARENT_MISSING",
                "publish target parent is unavailable",
                str(target),
            )

        staged = None
        try:
            staged = self._prepare_staging(spec, target, expected_target_sha256)
        except TargetFingerprintConflict:
            return _result(
                OperationOutcome.FAILED,
                "TARGET_FINGERPRINT_CONFLICT",
                "publish target changed after staging was prepared",
                str(target),
            )
        except OSError as exc:
            return _result(OperationOutcome.FAILED, "STAGING_PREPARE_FAILED", str(exc), str(target))
        assert staged is not None

        try:
            if _cancelled(cancellation):
                return self._finish(
                    OperationOutcome.CANCELLED,
                    "PUBLISH_CANCELLED",
                    "cancelled before pack",
                    spec,
                    staged,
                    None,
                    cleanup_policy,
                )
            try:
                pack(str(Path(build_dir).resolve()), staged.path, fmt=spec.output_format, progress=progress)
            except Exception as exc:  # noqa: BLE001 - pack failure is a publication failure
                diagnostic = Diagnostic(
                    "FOMOD_PACK_FAILED",
                    f"staging pack failed ({type(exc).__name__})",
                    severity=DiagnosticSeverity.ERROR,
                )
                return self._finish(
                    OperationOutcome.FAILED,
                    "FOMOD_PACK_FAILED",
                    str(exc),
                    spec,
                    staged,
                    (diagnostic,),
                    cleanup_policy,
                )

            if _cancelled(cancellation):
                return self._finish(
                    OperationOutcome.CANCELLED,
                    "PUBLISH_CANCELLED",
                    "cancelled after pack",
                    spec,
                    staged,
                    None,
                    cleanup_policy,
                )

            # Reopen with the same ArchivePolicy: entries, root layout, budget.
            try:
                archive_manifest = inspect_archive(staged.path, policy=self._policy)
            except (ArchivePolicyError, ValueError, OSError) as exc:
                diagnostic = Diagnostic(
                    "FOMOD_ARCHIVE_REOPEN_FAILED",
                    f"packed archive failed reopen validation ({type(exc).__name__})",
                    severity=DiagnosticSeverity.ERROR,
                )
                return self._finish(
                    OperationOutcome.FAILED,
                    "FOMOD_ARCHIVE_REOPEN_FAILED",
                    str(exc),
                    spec,
                    staged,
                    (diagnostic,),
                    cleanup_policy,
                )
            if not archive_manifest.files:
                diagnostic = Diagnostic(
                    "FOMOD_ARCHIVE_EMPTY",
                    "packed archive contains no files",
                    severity=DiagnosticSeverity.ERROR,
                )
                return self._finish(
                    OperationOutcome.FAILED,
                    "FOMOD_ARCHIVE_EMPTY",
                    "packed archive contains no files",
                    spec,
                    staged,
                    (diagnostic,),
                    cleanup_policy,
                )

            artifact_sha256 = _sha256_file(Path(staged.path))
            artifact_size = Path(staged.path).stat().st_size
            if _cancelled(cancellation):
                return self._finish(
                    OperationOutcome.CANCELLED,
                    "PUBLISH_CANCELLED",
                    "cancelled before commit",
                    spec,
                    staged,
                    None,
                    cleanup_policy,
                )

            current = _sha256_or_none(target)
            if expected_target_sha256 is not None and current is not None and current != expected_target_sha256:
                return self._finish(
                    OperationOutcome.FAILED,
                    "TARGET_FINGERPRINT_CONFLICT",
                    "publish target changed after staging was prepared",
                    spec,
                    staged,
                    None,
                    cleanup_policy,
                )
            backup = None
            if current is not None:
                backup = self._backup_verified(target, current)

            manifest = FomodManifest.from_spec(
                spec,
                build_fingerprint=build_fingerprint or _directory_fingerprint(build_dir),
                artifact_sha256=artifact_sha256,
                artifact_size=artifact_size,
            )

            accepted = guard(spec.run_id, lambda: _atomic_replace(staged.path, str(target)))
            if not accepted:
                return self._finish(
                    OperationOutcome.CANCELLED,
                    "PUBLISH_COMMIT_REJECTED",
                    "publish commit guard rejected the run",
                    spec,
                    staged,
                    None,
                    cleanup_policy,
                )

            try:
                manifest_path = self._write_manifest(spec, target, manifest)
            except Exception as exc:  # archive commit already happened and must be reported truthfully
                diagnostic = Diagnostic(
                    "PUBLISH_MANIFEST_FAILED",
                    f"archive published but manifest finalization failed ({type(exc).__name__})",
                    severity=DiagnosticSeverity.ERROR,
                )
                return self._finish(
                    OperationOutcome.PARTIAL,
                    "PUBLISH_MANIFEST_FAILED",
                    "FOMOD archive was published, but its manifest could not be finalized",
                    spec,
                    staged,
                    (diagnostic,),
                    cleanup_policy,
                    published=True,
                    artifact_sha256=artifact_sha256,
                    artifact_size=artifact_size,
                    backup_path=backup,
                )
            return self._finish(
                OperationOutcome.COMPLETED,
                "PUBLISHED",
                "FOMOD archive validated and atomically published",
                spec,
                staged,
                None,
                cleanup_policy,
                published=True,
                artifact_sha256=artifact_sha256,
                artifact_size=artifact_size,
                manifest_path=manifest_path,
                backup_path=backup,
            )
        except Exception as exc:  # noqa: BLE001 - failures before guarded replace leave target untouched
            return self._finish(
                OperationOutcome.FAILED,
                "PUBLISH_IO_FAILED",
                f"{type(exc).__name__}: {exc}",
                spec,
                staged,
                None,
                cleanup_policy,
            )

    def _prepare_staging(self, spec: FomodRunSpec, target: Path, expected_target_sha256: str | None) -> StagedPack:
        parent = target.parent
        if not parent.exists() or not parent.is_dir():
            raise OSError("publish target parent must exist and be a directory")
        initial = _sha256_or_none(target)
        if expected_target_sha256 is not None and initial is not None and initial != expected_target_sha256:
            raise TargetFingerprintConflict()
        # The staged artifact keeps the target's format extension so the archive
        # inspector can dispatch on its suffix; ".stage" marks it as non-official.
        suffix = target.suffix if target.suffix else ("." + spec.output_format)
        stage_name = f".{target.stem}.{secrets.token_urlsafe(14)}.stage{suffix}"
        stage_path = parent / stage_name
        try:
            descriptor = os.open(stage_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError as exc:
            raise OSError("staging path collided; retry the run") from exc
        except OSError as exc:
            raise OSError(f"cannot create staging path ({type(exc).__name__})") from exc
        return StagedPack(str(stage_path), str(target), initial, spec.run_id)

    def _backup_verified(self, target: Path, sha256: str) -> str:
        backup_name = f".{target.name}.{sha256[:12]}.{secrets.token_urlsafe(8)}.bak"
        backup_path = target.parent / backup_name
        shutil.copyfile(target, backup_path)
        if _sha256_file(backup_path) != sha256:
            backup_path.unlink(missing_ok=True)
            raise OSError("backup verification failed")
        return str(backup_path)

    def _write_manifest(self, spec: FomodRunSpec, target: Path, manifest: FomodManifest) -> str:
        manifest_path = target.with_name(f"{target.name}.manifest.json")
        payload = json.dumps(
            manifest.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary = manifest_path.with_name(f".{manifest_path.name}.{secrets.token_urlsafe(8)}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, manifest_path)
        return str(manifest_path)

    def _finish(
        self,
        outcome: OperationOutcome,
        code: str,
        message: str,
        spec: FomodRunSpec,
        staged: StagedPack,
        diagnostics: tuple[Diagnostic, ...] | None,
        cleanup_policy: CleanupPolicy,
        *,
        published: bool = False,
        artifact_sha256: str | None = None,
        artifact_size: int | None = None,
        manifest_path: str | None = None,
        backup_path: str | None = None,
    ) -> StagingPublishResult:
        retain = cleanup_policy in {CleanupPolicy.RETAIN_ALWAYS, CleanupPolicy.RETAIN_ON_FAILURE}
        if retain and not published:
            pass  # staging is kept per policy and reported
        elif not published:
            try:
                Path(staged.path).unlink(missing_ok=True)
            except OSError as exc:
                diagnostics = tuple(diagnostics or ()) + (
                    Diagnostic(
                        "STAGING_CLEANUP_FAILED",
                        f"staging cleanup failed ({type(exc).__name__})",
                        severity=DiagnosticSeverity.ERROR,
                    ),
                )
                return _result(
                    outcome,
                    code,
                    message,
                    staged.target,
                    published=published,
                    staged_path=staged.path,
                    artifact_sha256=artifact_sha256,
                    artifact_size=artifact_size,
                    manifest_path=manifest_path,
                    backup_path=backup_path,
                    diagnostics=diagnostics,
                )
        return _result(
            outcome,
            code,
            message,
            staged.target,
            published=published,
            staged_path=staged.path if (retain and not published) else None,
            artifact_sha256=artifact_sha256,
            artifact_size=artifact_size,
            manifest_path=manifest_path,
            backup_path=backup_path,
            diagnostics=diagnostics,
        )


def _result(
    outcome: OperationOutcome,
    code: str,
    message: str,
    target: str,
    *,
    published: bool = False,
    staged_path: str | None = None,
    manifest_path: str | None = None,
    artifact_sha256: str | None = None,
    artifact_size: int | None = None,
    backup_path: str | None = None,
    diagnostics: tuple[Diagnostic, ...] | None = None,
) -> StagingPublishResult:
    return StagingPublishResult(
        outcome,
        code,
        message,
        target,
        published=published,
        staged_path=staged_path,
        manifest_path=manifest_path,
        artifact_sha256=artifact_sha256,
        artifact_size=artifact_size,
        backup_path=backup_path,
        diagnostics=diagnostics or (),
    )


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _atomic_replace(source: str, target: str) -> None:
    os.replace(source, target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_or_none(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return _sha256_file(path)


def _directory_fingerprint(root: str) -> str:
    digest = hashlib.sha256()
    base = Path(root)
    for path in sorted((item for item in base.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(base).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()
