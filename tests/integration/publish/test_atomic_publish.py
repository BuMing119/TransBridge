from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import errno
import hashlib
from pathlib import Path
import shutil

import pytest

from transbridge.application.contracts import (
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import (
    EetXmlAdapter,
    FormatId,
    LocalizedStringsAdapter,
    ParseRequest,
    SourceDescriptor,
    SsePluginAdapter,
    WriteRequest,
    XtXmlAdapter,
)
from transbridge.application.io.publish import (
    BackupPolicy,
    CommitDecision,
    ConflictPolicy,
    DebugArtifactPolicy,
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
    TaskRuntimeCommitGuard,
    ValidationReport,
)
from transbridge.application.security.paths import PathAuthorizationPolicy, PathGrant
from transbridge.application.tasks import (
    CancellationToken,
    JobSpec,
    OwnerRef,
    TaskRuntime,
)

_VALIDATION_HASH = hashlib.sha256(b"validated-summary").hexdigest()
_FIXTURE = Path("tests/contracts/io/fixtures/eet-small.xml")


class FakePublishFilesystem:
    def __init__(self, root: Path) -> None:
        self.root = str(root.resolve())
        self.files: dict[str, tuple[bytes, int]] = {}
        self.failures: dict[str, BaseException] = {}
        self.removed: list[str] = []
        self.replaced: list[tuple[str, str]] = []
        self.volume_matches = True
        self.atomic_supported = True

    def canonicalize(self, path: str) -> str:
        return str(Path(path).resolve(strict=False))

    def exists(self, path: str) -> bool:
        canonical = self.canonicalize(path)
        return canonical in self.files or canonical == self.root

    def read_bytes(self, path: str) -> bytes:
        self._raise("read")
        return self.files[self.canonicalize(path)][0]

    def fingerprint(self, path: str):
        from transbridge.application.io.publish import FileFingerprint

        self._raise("fingerprint")
        item = self.files.get(self.canonicalize(path))
        if item is None:
            return FileFingerprint.missing()
        data, mode = item
        return FileFingerprint(True, hashlib.sha256(data).hexdigest(), len(data), mode)

    def exclusive_create(self, path: str, *, mode: int) -> None:
        self._raise("exclusive_create")
        canonical = self.canonicalize(path)
        if canonical in self.files:
            raise FileExistsError(canonical)
        self.files[canonical] = (b"", mode)

    def chmod(self, path: str, mode: int) -> None:
        self._raise("chmod")
        canonical = self.canonicalize(path)
        data, _ = self.files[canonical]
        self.files[canonical] = (data, mode)

    def write(self, path: str, data: bytes) -> None:
        self._raise("write")
        canonical = self.canonicalize(path)
        _, mode = self.files[canonical]
        self.files[canonical] = (data, mode)

    def fsync_file(self, path: str) -> None:
        self._raise("fsync_file")
        if self.canonicalize(path) not in self.files:
            raise FileNotFoundError(path)

    def fsync_directory(self, path: str) -> None:
        del path
        self._raise("fsync_directory")

    def atomic_replace(self, source: str, destination: str) -> None:
        self._raise("atomic_replace")
        source = self.canonicalize(source)
        destination = self.canonicalize(destination)
        self.files[destination] = self.files.pop(source)
        self.replaced.append((source, destination))

    def remove(self, path: str, *, missing_ok: bool = False) -> None:
        self._raise("remove")
        canonical = self.canonicalize(path)
        self.removed.append(canonical)
        if canonical not in self.files and not missing_ok:
            raise FileNotFoundError(canonical)
        self.files.pop(canonical, None)

    def copy_exclusive(self, source: str, destination: str, *, mode: int) -> None:
        self._raise("copy")
        source = self.canonicalize(source)
        destination = self.canonicalize(destination)
        if destination in self.files:
            raise FileExistsError(destination)
        self.files[destination] = (self.files[source][0], mode)

    def same_volume(self, first: str, second: str) -> bool:
        del first, second
        return self.volume_matches

    def atomic_replace_supported(self, path: str) -> bool:
        del path
        return self.atomic_supported

    def _raise(self, operation: str) -> None:
        error = self.failures.get(operation)
        if error is not None:
            raise error


class BytesRenderer:
    def __init__(self, filesystem: FakePublishFilesystem, content: bytes = b"new artifact") -> None:
        self.filesystem = filesystem
        self.content = content
        self.after_render = None

    def render(self, request, staging_path):
        del request
        self.filesystem.write(staging_path, self.content)
        if self.after_render is not None:
            self.after_render()
        return OperationResult.completed((staging_path,))


class FixedValidator:
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid

    def validate(self, request, staging_path):
        del staging_path
        return ValidationReport(
            self.valid,
            request.format_id,
            self.valid,
            self.valid,
            self.valid,
            1 if self.valid else 0,
            _VALIDATION_HASH if self.valid else None,
            "VALID" if self.valid else "TEST_VALIDATION_FAILED",
            "validated" if self.valid else "fault-injected validation failure",
        )


def _request(target: str, *, run_id: str = "publish-run", cancellation=None) -> WriteRequest:
    return WriteRequest(
        SourceDescriptor(target, Path(target).name),
        FormatId.XML_EET,
        (),
        1,
        RequestContext("publish-test", run_id=run_id),
        new_template=b"template",
        cancellation=cancellation,
    )


def _publish(
    filesystem: FakePublishFilesystem,
    target_path: str,
    *,
    renderer=None,
    validator=None,
    target=None,
    guard=None,
    request=None,
):
    request = request or _request(target_path)
    return PublishCoordinator(filesystem).publish(
        request,
        target or PublishTarget(target_path),
        renderer=renderer or BytesRenderer(filesystem),
        validator=validator or FixedValidator(),
        commit_guard=guard or ImmediateCommitGuard(request.context.run_id),
    )


def test_fake_success_chain_preserves_permissions_creates_verified_backup_and_manifest(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "正式文件.xml"))
    filesystem.files[target] = (b"old artifact", 0o640)

    result = _publish(filesystem, target)

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert result.published
    assert filesystem.files[target] == (b"new artifact", 0o640)
    assert result.backup_path is not None
    assert filesystem.files[result.backup_path][0] == b"old artifact"
    assert result.manifest is not None
    assert result.manifest.artifact_sha256 == hashlib.sha256(b"new artifact").hexdigest()
    assert result.manifest.validation_summary_sha256 == _VALIDATION_HASH
    assert result.retained_staging_path is None


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (PermissionError("denied"), "PERMISSION_DENIED"),
        (OSError(errno.ENOSPC, "disk full"), "DISK_FULL"),
    ],
)
def test_permission_and_disk_full_leave_old_target(failure, code, tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    operation = "exclusive_create" if isinstance(failure, PermissionError) else "write"
    filesystem.failures[operation] = failure

    result = _publish(filesystem, target)

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == code
    assert filesystem.files[target][0] == b"old"
    assert not filesystem.replaced


@pytest.mark.parametrize("fault", ["fsync_file", "atomic_replace"])
def test_fsync_or_replace_fault_never_deletes_old_target(fault: str, tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    filesystem.failures[fault] = OSError("fault")

    result = _publish(filesystem, target)

    assert result.outcome is OperationOutcome.FAILED
    assert filesystem.files[target][0] == b"old"
    assert target not in filesystem.removed


def test_validation_failure_cleans_stage_and_preserves_target(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)

    result = _publish(filesystem, target, validator=FixedValidator(valid=False))

    assert result.code == "TEST_VALIDATION_FAILED"
    assert filesystem.files[target][0] == b"old"
    assert not any(path.endswith(".stage") for path in filesystem.files)


def test_explicit_debug_policy_retains_failed_stage_and_reports_path(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)

    result = _publish(
        filesystem,
        target,
        validator=FixedValidator(valid=False),
        target=PublishTarget(target, debug_policy=DebugArtifactPolicy.RETAIN_ON_FAILURE),
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.retained_staging_path is not None
    assert filesystem.files[result.retained_staging_path][0] == b"new artifact"
    assert filesystem.files[target][0] == b"old"


def test_target_change_conflicts_unless_explicit_overwrite_and_backs_up_latest(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target_path = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target_path] = (b"original", 0o600)
    renderer = BytesRenderer(filesystem)
    renderer.after_render = lambda: filesystem.files.__setitem__(target_path, (b"concurrent", 0o600))

    conflict = _publish(filesystem, target_path, renderer=renderer)

    assert conflict.code == "TARGET_FINGERPRINT_CONFLICT"
    assert filesystem.files[target_path][0] == b"concurrent"

    overwrite = _publish(
        filesystem,
        target_path,
        renderer=renderer,
        target=PublishTarget(target_path, ConflictPolicy.EXPLICIT_OVERWRITE),
    )
    assert overwrite.outcome is OperationOutcome.COMPLETED
    assert overwrite.backup_path is not None
    assert filesystem.files[overwrite.backup_path][0] == b"concurrent"
    assert filesystem.files[target_path][0] == b"new artifact"


def test_expected_target_fingerprint_and_backup_failure_are_fail_closed(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target_path = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target_path] = (b"old", 0o600)
    wrong = filesystem.fingerprint(target_path)
    wrong = replace(wrong, sha256=hashlib.sha256(b"different").hexdigest())

    conflict = _publish(
        filesystem,
        target_path,
        target=PublishTarget(target_path, expected_fingerprint=wrong),
    )
    assert conflict.code == "TARGET_FINGERPRINT_CONFLICT"
    assert filesystem.files[target_path][0] == b"old"

    filesystem.failures["copy"] = OSError(errno.ENOSPC, "backup disk full")
    backup_failed = _publish(
        filesystem,
        target_path,
        target=PublishTarget(target_path, backup_policy=BackupPolicy.REQUIRED_IF_EXISTS),
    )
    assert backup_failed.code == "DISK_FULL"
    assert filesystem.files[target_path][0] == b"old"


def test_directory_fsync_failure_is_partial_not_completed(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    filesystem.failures["fsync_directory"] = OSError("durability uncertain")

    result = _publish(filesystem, target)

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.published
    assert result.code == "DIRECTORY_FSYNC_FAILED"
    assert filesystem.files[target][0] == b"new artifact"


def test_cancel_after_render_and_run_guard_race_preserve_target(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    cancellation = CancellationToken()
    renderer = BytesRenderer(filesystem)
    renderer.after_render = lambda: cancellation._cancel("after render")
    request = _request(target, cancellation=cancellation)

    cancelled = _publish(filesystem, target, renderer=renderer, request=request)

    assert cancelled.outcome is OperationOutcome.CANCELLED
    assert filesystem.files[target][0] == b"old"

    active = [True]

    class RacingGuard:
        def commit(self, run_id, mutation):
            del run_id, mutation
            active[0] = False
            return CommitDecision(False, "cancelled")

    request = _request(target)
    rejected = _publish(filesystem, target, request=request, guard=RacingGuard())
    assert rejected.code == "COMMIT_GUARD_REJECTED"
    assert filesystem.files[target][0] == b"old"


def test_task_runtime_commit_permit_rejects_cancel_race(tmp_path: Path) -> None:
    class Ids:
        def new_id(self):
            return "runtime-publish"

    class Clock:
        def now(self):
            return datetime.now(UTC)

    runtime = TaskRuntime(id_generator=Ids(), clock=Clock())
    owner = OwnerRef("owner", "test")
    ref = runtime.submit(JobSpec("publish", "input", "fingerprint"), owner).ref
    runtime.start(ref, owner)
    token = runtime.cancellation_token(ref, owner)
    permit = runtime.commit_permit(ref, owner)
    guarded = TaskRuntimeCommitGuard(runtime, permit)

    class CancellingGuard:
        def commit(self, run_id, mutation):
            runtime.cancel(ref, owner)
            return guarded.commit(run_id, mutation)

    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    result = _publish(
        filesystem,
        target,
        request=_request(target, run_id=ref.run_id, cancellation=token),
        guard=CancellingGuard(),
    )

    assert result.outcome is OperationOutcome.CANCELLED
    assert filesystem.files[target][0] == b"old"
    assert not filesystem.replaced


@pytest.mark.parametrize(
    ("same_volume", "atomic_supported"),
    [(False, True), (True, False)],
)
def test_cross_volume_or_non_atomic_filesystem_is_rejected(same_volume, atomic_supported, tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    filesystem.volume_matches = same_volume
    filesystem.atomic_supported = atomic_supported
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)

    result = _publish(filesystem, target)

    assert result.code == "ATOMIC_REPLACE_UNAVAILABLE"
    assert filesystem.files[target][0] == b"old"


def test_authorized_root_rejects_symlink_escape_before_staging(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    protected = outside / "protected.xml"
    protected.write_bytes(b"protected")
    link = allowed / "escaped.xml"
    try:
        link.symlink_to(protected)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    filesystem = OsPublishFilesystem()
    request = _request(str(link))
    coordinator = PublishCoordinator(
        filesystem,
        path_policy=PathAuthorizationPolicy((PathGrant(allowed, allow_create=True),)),
    )

    result = coordinator.publish(
        request,
        PublishTarget(str(link)),
        renderer=BytesRenderer(FakePublishFilesystem(tmp_path)),
        validator=FixedValidator(),
        commit_guard=ImmediateCommitGuard("publish-run"),
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "PERMISSION_DENIED"
    assert protected.read_bytes() == b"protected"


def test_cleanup_fault_is_reported_without_claiming_success(tmp_path: Path) -> None:
    filesystem = FakePublishFilesystem(tmp_path)
    target = filesystem.canonicalize(str(tmp_path / "target.xml"))
    filesystem.files[target] = (b"old", 0o600)
    filesystem.failures["remove"] = PermissionError("cleanup denied")

    result = _publish(filesystem, target, validator=FixedValidator(valid=False))

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "STAGING_CLEANUP_FAILED"
    assert result.retained_staging_path is not None
    assert filesystem.files[target][0] == b"old"


def test_real_eet_parse_write_reparse_atomic_success_with_unicode_long_path(tmp_path: Path) -> None:
    nested = tmp_path
    for index in range(6):
        nested /= f"很长的目录-{index}-abcdefghijklmnop"
    nested.mkdir(parents=True)
    source = nested / "源文件.xml"
    target = nested / "正式译文输出-非常长的文件名称.xml"
    shutil.copyfile(_FIXTURE, source)
    filesystem = OsPublishFilesystem()
    long_target = Path(filesystem.canonicalize(str(target)))
    long_target.write_bytes(b"old target")
    adapter = EetXmlAdapter()
    context = RequestContext("publish-real", run_id="real-publish")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source), source.name), context, FormatId.XML_EET))
    changed = replace(parsed.entries[0], translation="你好，旅行者。", stage=1)
    request = WriteRequest(
        SourceDescriptor(str(target), target.name),
        FormatId.XML_EET,
        (changed,),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
    )
    result = PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(str(target), backup_policy=BackupPolicy.REQUIRED_IF_EXISTS),
        renderer=FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard("real-publish"),
    )
    reparsed = adapter.parse(
        ParseRequest(
            SourceDescriptor(str(long_target), target.name),
            context,
            FormatId.XML_EET,
            changed.identity.namespace,
        )
    )

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert result.manifest is not None
    assert result.manifest.artifact_sha256 == hashlib.sha256(filesystem.read_bytes(str(long_target))).hexdigest()
    assert result.backup_path is not None
    assert Path(result.backup_path).read_bytes() == b"old target"
    assert reparsed.outcome is OperationOutcome.COMPLETED
    assert reparsed.entries[0].translation == "你好，旅行者。"
    assert not list(nested.glob("*.stage"))


@pytest.mark.parametrize(
    ("adapter", "fixture", "format_id", "translation"),
    [
        (XtXmlAdapter(), Path("tests/contracts/io/fixtures/xt-small.xml"), FormatId.XML_XT, "XT atomic"),
        (
            LocalizedStringsAdapter(FormatId.STRINGS),
            Path("tests/contracts/io/fixtures/strings/integrity.strings"),
            FormatId.STRINGS,
            "Localized atomic ✓",
        ),
    ],
)
def test_round_trip_validator_uses_xt_strings_and_plugin_parsers(
    tmp_path: Path,
    adapter,
    fixture: Path,
    format_id: FormatId,
    translation: str,
) -> None:
    source = tmp_path / fixture.name
    target = tmp_path / f"published-{fixture.name}"
    shutil.copyfile(fixture, source)
    context = RequestContext("publish-formats", run_id=f"publish-{format_id.value}")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source), source.name), context, format_id))
    assert parsed.entries
    changed = replace(parsed.entries[0], translation=translation, stage=1)
    request = WriteRequest(
        SourceDescriptor(str(target), target.name),
        format_id,
        (changed,),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
    )
    filesystem = OsPublishFilesystem()

    result = PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(str(target), backup_policy=BackupPolicy.NONE),
        renderer=FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard(context.run_id),
    )

    assert result.outcome is OperationOutcome.COMPLETED, result.message
    assert result.validation is not None
    assert result.validation.structure_valid
    assert result.validation.reparse_valid
    assert result.validation.fidelity_valid


@pytest.mark.filterwarnings("ignore:get_by_key is deprecated:DeprecationWarning")
def test_plugin_partial_reparse_is_not_misreported_as_publish_success(tmp_path: Path) -> None:
    source = tmp_path / "sample.esp"
    target = tmp_path / "published.esp"
    shutil.copyfile(Path("tests/parser/data/sample.esp"), source)
    adapter = SsePluginAdapter()
    context = RequestContext("publish-plugin", run_id="publish-plugin")
    parsed = adapter.parse(ParseRequest(SourceDescriptor(str(source), source.name), context, FormatId.PLUGIN_SSE))
    changed = replace(parsed.entries[0], translation="Plugin staged", stage=1)
    request = WriteRequest(
        SourceDescriptor(str(target), target.name),
        FormatId.PLUGIN_SSE,
        (changed,),
        1,
        context,
        source_snapshot=parsed.source_snapshot,
    )
    filesystem = OsPublishFilesystem()

    result = PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(str(target), backup_policy=BackupPolicy.NONE),
        renderer=FormatAdapterRenderer(adapter),
        validator=FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard("publish-plugin"),
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "REPARSE_FAILED"
    assert not target.exists()
