"""TaskRuntime adapters for typed FOMOD workloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import threading

from transbridge.application.contracts import JobRef, OperationOutcome
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    JobState,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
    TransitionError,
)
from transbridge.fileops.archive import inspect_archive
from transbridge.fileops.archive_policy import ArchivePolicy, ArchivePolicyError

from .models import FomodRunSpec, PipelineResult
from .pipeline import PipelineEngine


class FomodPipelineFailed(RuntimeError):
    def __init__(self, report: PipelineResult) -> None:
        self.report = report
        codes = ",".join(diagnostic.code for diagnostic in report.diagnostics)
        super().__init__(f"FOMOD pipeline failed: {codes or 'unknown'}")


class TaskRuntimeRunGuard:
    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner

    def allows(self, run_id: str) -> bool:
        if run_id != self._ref.run_id:
            raise ValueError("FOMOD RunSpec does not match the scheduled TaskRuntime run")
        snapshot = self._runtime.get(self._ref, self._owner)
        return snapshot.state is JobState.RUNNING


class TaskRuntimeCommitGuard:
    """Issue and consume the runtime's one-shot permit around publication."""

    def __init__(self, runtime: TaskRuntime, ref: JobRef, owner: OwnerRef) -> None:
        self._runtime = runtime
        self._ref = ref
        self._owner = owner

    def commit(self, run_id: str, mutation: Callable[[], None]) -> bool:
        if run_id != self._ref.run_id:
            raise ValueError("FOMOD RunSpec does not match the scheduled TaskRuntime run")
        try:
            permit = self._runtime.commit_permit(self._ref, self._owner)
        except TransitionError:
            snapshot = self._runtime.get(self._ref, self._owner)
            if snapshot.state in {JobState.CANCELLING, JobState.CANCELLED}:
                return False
            raise
        result = self._runtime.try_commit(permit, mutation)
        if result.accepted:
            return True
        if result.reason in {"cancelled", "terminal_or_inactive"} and result.snapshot.state in {
            JobState.CANCELLING,
            JobState.CANCELLED,
        }:
            return False
        raise RuntimeError(f"FOMOD_COMMIT_REJECTED:{result.reason}")


class FomodPipelineWorkload:
    """Return reports to projections while leaving terminal state to TaskRuntime."""

    def __init__(
        self,
        spec: FomodRunSpec,
        engine: PipelineEngine,
        *,
        on_report: Callable[[PipelineResult], None] | None = None,
    ) -> None:
        self._spec = spec
        self._engine = engine
        self._on_report = on_report
        self._lock = threading.Lock()
        self._last_report: PipelineResult | None = None

    @property
    def last_report(self) -> PipelineResult | None:
        with self._lock:
            return self._last_report

    def __call__(self, cancellation) -> PipelineResult:
        report = self._engine.run(self._spec, cancellation)
        with self._lock:
            self._last_report = report
        if self._on_report is not None:
            try:
                self._on_report(report)
            except Exception:
                # Report consumers are projections and cannot own job terminal state.
                pass
        if report.outcome is OperationOutcome.CANCELLED:
            raise TaskCancelled("FOMOD pipeline cancelled")
        if report.outcome is OperationOutcome.FAILED:
            raise FomodPipelineFailed(report)
        return report


@dataclass(frozen=True, slots=True)
class FomodTaskDraft:
    new_archive: str
    output_archive: str
    target_locale: str
    config_hash: str
    output_format: str = "zip"
    old_archive: str | None = None
    workspace_root: str | None = None
    ai_enabled: bool = True
    overwrite_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class FomodTaskPreflight:
    draft: FomodTaskDraft
    request_digest: str
    new_archive_hash: str | None
    old_archive_hash: str | None
    target_revision: str
    diagnostics: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.diagnostics


class FomodTaskPreflightService:
    """Archive inspection and output checks with no workspace/publication side effect."""

    def __init__(self, archive_policy: ArchivePolicy | None = None) -> None:
        self._archive_policy = archive_policy

    def preflight(self, draft: FomodTaskDraft) -> FomodTaskPreflight:
        diagnostics: list[str] = []
        warnings: list[str] = []
        new_path = Path(draft.new_archive)
        old_path = None if draft.old_archive is None else Path(draft.old_archive)
        output = Path(draft.output_archive)
        if draft.output_format not in {"zip", "7z"}:
            diagnostics.append("FOMOD_OUTPUT_FORMAT_UNSUPPORTED")
        if not draft.target_locale.strip() or not draft.config_hash.strip():
            diagnostics.append("FOMOD_CONFIG_INCOMPLETE")
        new_hash = _preflight_archive(new_path, self._archive_policy, "NEW", diagnostics)
        old_hash = None
        if old_path is not None:
            old_hash = _preflight_archive(old_path, self._archive_policy, "OLD", diagnostics)
        if not output.parent.is_dir() or not os.access(output.parent, os.W_OK):
            diagnostics.append("FOMOD_OUTPUT_UNWRITABLE")
        if output.exists() and not draft.overwrite_confirmed:
            diagnostics.append("FOMOD_OVERWRITE_CONFIRMATION_REQUIRED")
        if output.exists() and draft.overwrite_confirmed:
            warnings.append("已有输出将在提交前创建校验备份")
        target_revision = _hash_path(output) if output.is_file() else "missing"
        digest = _fomod_request_digest(draft, new_hash, old_hash, target_revision)
        return FomodTaskPreflight(
            draft,
            digest,
            new_hash,
            old_hash,
            target_revision,
            tuple(diagnostics),
            tuple(warnings),
        )


FomodEngineFactory = Callable[[FomodRunSpec, TaskRuntimeRunGuard, TaskRuntimeCommitGuard], PipelineEngine]


class FomodTaskEntrypoint:
    """Bind typed FOMOD stages to one TaskRuntime identity and terminal owner."""

    def __init__(self, runtime: TaskRuntime, engine_factory: FomodEngineFactory) -> None:
        self._runtime = runtime
        self._engine_factory = engine_factory
        self._workloads: dict[str, FomodPipelineWorkload] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        preflight: FomodTaskPreflight,
        owner: OwnerRef,
        *,
        on_report: Callable[[PipelineResult], None] | None = None,
    ) -> JobRef:
        if not preflight.ready or preflight.new_archive_hash is None:
            raise ValueError("FOMOD task requires a successful current preflight")
        if self._is_stale(preflight):
            raise ValueError("FOMOD preflight became stale before submission")
        deferred = self._runtime.submit(
            JobSpec(
                job_type="operation.fomod",
                input_ref=preflight.draft.new_archive,
                input_fingerprint=preflight.request_digest,
                display_name="构建 FOMOD 安装包",
                config_digest=preflight.draft.config_hash,
                capabilities=JobCapabilities(supports_cancel=True),
                metadata=(("output", preflight.draft.output_archive),),
            ),
            owner,
        )
        ref = deferred.ref
        draft = preflight.draft
        spec = FomodRunSpec(
            run_id=ref.run_id,
            new_archive=draft.new_archive,
            new_archive_hash=preflight.new_archive_hash,
            output_archive=draft.output_archive,
            target_locale=draft.target_locale,
            config_hash=draft.config_hash,
            old_archive=draft.old_archive,
            old_archive_hash=preflight.old_archive_hash,
            output_format=draft.output_format,
            workspace_root=draft.workspace_root or tempfile.mkdtemp(prefix="tb_fomod_runtime_"),
            ai_enabled=draft.ai_enabled,
            expected_output_hash=None if preflight.target_revision == "missing" else preflight.target_revision,
            expected_output_missing=preflight.target_revision == "missing",
        )
        run_guard = TaskRuntimeRunGuard(self._runtime, ref, owner)
        commit_guard = TaskRuntimeCommitGuard(self._runtime, ref, owner)
        engine = self._engine_factory(spec, run_guard, commit_guard)
        workload = FomodPipelineWorkload(spec, engine, on_report=on_report)
        with self._lock:
            self._workloads[ref.run_id] = workload
            while len(self._workloads) > 100:
                self._workloads.pop(next(iter(self._workloads)))
        self._runtime.schedule(ref, owner, workload)
        return ref

    def report(self, ref: JobRef, actor: OwnerRef) -> PipelineResult | None:
        self._runtime.get(ref, actor)
        with self._lock:
            workload = self._workloads.get(ref.run_id)
        return None if workload is None else workload.last_report

    @staticmethod
    def _is_stale(preflight: FomodTaskPreflight) -> bool:
        if preflight.new_archive_hash != _hash_path(Path(preflight.draft.new_archive)):
            return True
        if preflight.draft.old_archive is not None:
            if preflight.old_archive_hash != _hash_path(Path(preflight.draft.old_archive)):
                return True
        output = Path(preflight.draft.output_archive)
        current = _hash_path(output) if output.is_file() else "missing"
        return current != preflight.target_revision


def _preflight_archive(
    path: Path,
    policy: ArchivePolicy | None,
    prefix: str,
    diagnostics: list[str],
) -> str | None:
    if not path.is_file() or not os.access(path, os.R_OK):
        diagnostics.append(f"FOMOD_{prefix}_ARCHIVE_UNREADABLE")
        return None
    try:
        manifest = inspect_archive(str(path), policy=policy)
        if not manifest.files:
            diagnostics.append(f"FOMOD_{prefix}_ARCHIVE_EMPTY")
    except (ArchivePolicyError, OSError, ValueError):
        diagnostics.append(f"FOMOD_{prefix}_ARCHIVE_POLICY_REJECTED")
    return _hash_path(path)


def _fomod_request_digest(
    draft: FomodTaskDraft,
    new_hash: str | None,
    old_hash: str | None,
    target_revision: str,
) -> str:
    material = "\0".join((
        str(Path(draft.new_archive).resolve(strict=False)),
        new_hash or "invalid",
        str(Path(draft.old_archive).resolve(strict=False)) if draft.old_archive else "",
        old_hash or "",
        str(Path(draft.output_archive).resolve(strict=False)),
        target_revision,
        draft.target_locale,
        draft.config_hash,
        draft.output_format,
        str(draft.ai_enabled),
        str(draft.overwrite_confirmed),
    ))
    return hashlib.sha256(material.encode()).hexdigest()


def _hash_path(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
