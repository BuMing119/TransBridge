"""Stage, validate and commit a plugin together with all localized companions.

The OS cannot atomically rename several files. A single runtime commit permit
serializes the set, with the plugin installed last and reverse rollback on any
exception. Rollback failure retains the private staging directory and reports
its recovery location; it never reports a successful publication.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import secrets
import shutil
from tempfile import mkdtemp

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.application.io.contracts import SourceDescriptor, WriteRequest
from transbridge.application.io.plugin_write import localized_write_requests, plugin_artifact_paths

from .filesystem import PublishFilesystemPort
from .guards import PublishCommitGuard
from .models import BackupPolicy, ConflictPolicy, FileFingerprint
from .validators import FormatAdapterRenderer, FormatRoundTripValidator


class _BundleFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retain: bool = False):
        super().__init__(message)
        self.code = code
        self.retain = retain


class PluginBundlePublisher:
    """Own every final output path, overwrite check, backup and rollback."""

    def __init__(self, filesystem: PublishFilesystemPort, adapter) -> None:
        self._fs = filesystem
        self._adapter = adapter

    def publish(
        self,
        request: WriteRequest,
        fingerprints: tuple[tuple[str, FileFingerprint], ...],
        *,
        conflict_policy: ConflictPolicy,
        backup_policy: BackupPolicy,
        commit_guard: PublishCommitGuard,
    ) -> OperationResult[dict[str, object]]:
        stage_dir: Path | None = None
        retain = False
        result = None
        try:
            expected = dict(fingerprints)
            paths = tuple(
                self._fs.canonicalize(path)
                for path in plugin_artifact_paths(request.source_snapshot, request.target.uri, request.options)
            )
            if tuple(expected) != paths or len(paths) != len(set(paths)):
                raise _BundleFailure("WRITE_OUTPUT_SET_CHANGED", "写回输出集合与预检不一致。")
            self._check_targets(expected, conflict_policy)
            if self._cancelled(request):
                return OperationResult.cancelled(run_id=request.context.run_id)
            parent = Path(paths[0]).parent
            stage_dir = Path(mkdtemp(prefix=".transbridge-publish-", dir=parent))
            staged = {path: str(stage_dir / Path(path).relative_to(parent)) for path in paths}
            staged_request = replace(request, target=SourceDescriptor(staged[paths[0]], Path(paths[0]).name))
            rendered = FormatAdapterRenderer(self._adapter).render(staged_request, staged[paths[0]])
            if rendered.outcome is OperationOutcome.CANCELLED or self._cancelled(request):
                result = OperationResult.cancelled(run_id=request.context.run_id)
            else:
                if rendered.outcome is not OperationOutcome.COMPLETED:
                    raise _BundleFailure(
                        "RENDER_FAILED",
                        "；".join(item.message for item in rendered.diagnostics) or "插件及 Strings 写入未完成。",
                    )
                if {self._fs.canonicalize(path) for path in rendered.artifact_refs} != set(staged.values()):
                    raise _BundleFailure("WRITE_OUTPUT_SET_CHANGED", "格式写入器产生了预检之外的输出。")
                self._validate(staged_request, staged)
                rollback = self._copy_previous(stage_dir, expected)
                backups = self._commit(
                    request, expected, staged, rollback, conflict_policy, backup_policy, commit_guard
                )
                if backups is None:
                    result = OperationResult.cancelled(run_id=request.context.run_id)
                else:
                    result = OperationResult.completed(
                        {
                            "outcomes": tuple(
                                {
                                    "object_ref": path,
                                    "label": Path(path).name,
                                    "status": "succeeded",
                                    "code": "PUBLISHED",
                                    "retryable": False,
                                }
                                for path in paths
                            ),
                            "backup_paths": tuple(backups),
                        },
                        counts=OperationCounts(succeeded=len(paths)),
                        artifact_refs=paths,
                        run_id=request.context.run_id,
                    )
        except _BundleFailure as exc:
            retain = exc.retain
            result = self._failure(exc.code, str(exc), request, stage_dir if retain else None)
        except Exception as exc:
            result = self._failure("BUNDLE_PUBLISH_FAILED", f"插件及 Strings 发布失败：{exc}", request)
        finally:
            if stage_dir is not None and not retain:
                try:
                    shutil.rmtree(stage_dir)
                except OSError as exc:
                    cleanup = Diagnostic("BUNDLE_STAGING_CLEANUP_FAILED", f"临时发布目录清理失败：{stage_dir}（{exc}）")
                    if result is not None and result.is_success:
                        result = OperationResult.partial(
                            result.value,
                            counts=OperationCounts(succeeded=result.counts.succeeded, failed=1),
                            diagnostics=(cleanup,),
                            artifact_refs=result.artifact_refs,
                            run_id=request.context.run_id,
                        )
                    elif result is not None:
                        result = replace(result, diagnostics=(*result.diagnostics, cleanup))
        return result

    def _validate(self, request: WriteRequest, staged: dict[str, str]) -> None:
        checks = ((self._adapter, request), *localized_write_requests(request))
        for adapter, member in checks:
            validation = FormatRoundTripValidator(adapter, self._fs).validate(member, member.target.uri)
            if not validation.valid:
                raise _BundleFailure(validation.code, f"{Path(member.target.uri).name}：{validation.message}")
        for path in staged.values():
            self._fs.fsync_file(path)

    def _check_targets(self, expected, policy) -> None:
        for path, fingerprint in expected.items():
            if self._fs.canonicalize(path) != path:
                raise _BundleFailure("TARGET_PATH_CHANGED", f"输出目录在预检后发生变化：{path}")
            if self._fs.fingerprint(path) != fingerprint:
                raise _BundleFailure("TARGET_FINGERPRINT_CONFLICT", f"输出文件在预检后发生变化：{path}")
            if fingerprint.exists and policy is not ConflictPolicy.EXPLICIT_OVERWRITE:
                raise _BundleFailure("OVERWRITE_CONFIRMED", f"输出已存在，需要显式确认覆盖：{path}")
            if not self._fs.atomic_replace_supported(path):
                raise _BundleFailure("ATOMIC_REPLACE_UNAVAILABLE", f"输出位置不支持原子替换：{path}")

    def _copy_previous(self, directory, expected):
        rollback = {}
        for index, (path, fingerprint) in enumerate(expected.items()):
            if fingerprint.exists:
                saved = str(directory / f"rollback-{index}")
                self._fs.copy_exclusive(path, saved, mode=fingerprint.mode or 0o600)
                if self._fs.fingerprint(saved).sha256 != fingerprint.sha256:
                    raise _BundleFailure("TARGET_FINGERPRINT_CONFLICT", f"备份期间输出发生变化：{path}")
                rollback[path] = saved
        return rollback

    def _commit(self, request, expected, staged, rollback, conflict_policy, backup_policy, guard):
        attempted: list[str] = []
        backups: list[str] = []
        created_dirs: list[Path] = []
        paths = tuple(expected)

        def commit():
            self._check_targets(expected, conflict_policy)
            try:
                for path, saved in rollback.items():
                    if backup_policy is BackupPolicy.NONE:
                        continue
                    fingerprint = expected[path]
                    backup = str(
                        Path(path).with_name(f".{Path(path).name}.{fingerprint.sha256[:12]}.{secrets.token_hex(8)}.bak")
                    )
                    try:
                        self._fs.copy_exclusive(saved, backup, mode=fingerprint.mode or 0o600)
                    except FileExistsError:
                        raise
                    except Exception:
                        backups.append(backup)  # An interrupted exclusive copy may own a partial file.
                        raise
                    backups.append(backup)
                    if self._fs.fingerprint(backup).sha256 != fingerprint.sha256:
                        raise OSError("backup verification failed")
                for path in (*paths[1:], paths[0]):
                    parent = Path(path).parent
                    if not parent.exists():
                        parent.mkdir()
                        created_dirs.append(parent)
                    if expected[path].mode is not None:
                        self._fs.chmod(staged[path], expected[path].mode)
                    attempted.append(path)
                    self._fs.atomic_replace(staged[path], path)
                for parent in {str(Path(path).parent) for path in paths}:
                    self._fs.fsync_directory(parent)
            except Exception as exc:
                failures = self._rollback(attempted, expected, rollback)
                if not failures:
                    for backup in backups:
                        try:
                            self._fs.remove(backup, missing_ok=True)
                        except OSError as cleanup:
                            failures.append(f"备份清理失败 {backup}: {cleanup}")
                    for directory in reversed(created_dirs):
                        try:
                            directory.rmdir()
                        except OSError as cleanup:
                            failures.append(f"目录清理失败 {directory}: {cleanup}")
                if failures:
                    raise _BundleFailure(
                        "BUNDLE_ROLLBACK_FAILED", f"发布失败（{exc}），恢复未完成：{'；'.join(failures)}", retain=True
                    ) from exc
                raise _BundleFailure("BUNDLE_COMMIT_FAILED", f"发布失败，原有输出已恢复：{exc}") from exc

        if self._cancelled(request):
            return None
        decision = guard.commit(request.context.run_id, commit)
        if not decision.accepted:
            if self._cancelled(request):
                return None
            raise _BundleFailure("COMMIT_GUARD_REJECTED", f"发布已被任务权限拒绝：{decision.reason}")
        return backups

    def _rollback(self, attempted, expected, rollback) -> list[str]:
        failures = []
        for path in reversed(attempted):
            try:
                if expected[path].exists:
                    restore = rollback[path] + ".restore"
                    self._fs.copy_exclusive(rollback[path], restore, mode=expected[path].mode or 0o600)
                    self._fs.atomic_replace(restore, path)
                else:
                    self._fs.remove(path, missing_ok=True)
                if self._fs.fingerprint(path) != expected[path]:
                    raise OSError("restored output fingerprint differs")
            except Exception as exc:
                failures.append(f"{path}: {exc}")
        return failures

    @staticmethod
    def _cancelled(request):
        return request.cancellation is not None and request.cancellation.is_cancelled

    @staticmethod
    def _failure(code, message, request, retained=None):
        details = () if retained is None else (("recovery_directory", str(retained)),)
        return OperationResult(
            OperationOutcome.FAILED,
            diagnostics=(Diagnostic(code, message, details=details),),
            counts=OperationCounts(failed=1),
            run_id=request.context.run_id,
        )
