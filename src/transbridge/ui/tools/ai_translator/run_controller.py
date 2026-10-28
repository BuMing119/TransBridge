"""Run ownership and late-result guards for AI translator modes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import count
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from transbridge.application.contracts import JobRef
from transbridge.application.tasks import OwnerRef, TaskRuntime, TransitionError
from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef

from .run_spec import AiRunSpec, FrozenExecutionConfig, build_run_spec

EntryT = TypeVar("EntryT")
RunMode = Literal["translate", "mixed", "polish", "batch"]


@dataclass(frozen=True, slots=True)
class TranslationRunRequest[EntryT]:
    spec: AiRunSpec
    config_snapshot: FrozenExecutionConfig
    entries: tuple[EntryT, ...]
    request_budget: AiRequestBudget
    terminology_binding: object
    runtime_ref: JobRef | None = None
    reuse_proofread: bool = False
    recovery_task_id: str | None = None
    recovery_record: dict | None = None
    recovery_directory: Path | None = None

    @property
    def mode(self) -> RunMode:
        return self.spec.mode  # type: ignore[return-value]

    @property
    def owner_id(self) -> str:
        return self.spec.owner.owner_id

    @property
    def run_id(self) -> str:
        return self.spec.run_id

    @property
    def generation(self) -> int:
        return self.spec.generation

    @property
    def config(self) -> object:
        """Return a private copy; preference edits cannot mutate this run."""

        return self.config_snapshot.copy()


class RunAlreadyActiveError(RuntimeError):
    """Raised when a second run is requested for the same window owner."""


@dataclass(slots=True)
class _ActiveRun:
    request: TranslationRunRequest
    activity: object | None = None


class RunController:
    """Admit one task launch; AiTaskRun owns workers after publication."""

    def __init__(self, owner_id: str | None = None, *, task_runtime: TaskRuntime | None = None) -> None:
        self._owner_id = owner_id or uuid4().hex
        self._task_runtime = task_runtime
        self._ids = count(1)
        self._active: _ActiveRun | None = None
        self._closed = False

    @property
    def owner_id(self) -> str:
        """Capture this scalar before preparing a request in a worker."""
        return self._owner_id

    def begin(
        self,
        mode: RunMode,
        config: object,
        entries: list[EntryT],
        *,
        overwrite: bool = False,
        esp_path: str | None = None,
        project_id: str | None = None,
        variant_id: str | None = None,
        session_id: str | None = None,
        project_revision: int | None = None,
        run_id: str | None = None,
        terminology_owner: object | None = None,
        terminology_snapshot_ref: TerminologyRunSnapshotRef | None = None,
        prepared=None,
    ) -> TranslationRunRequest[EntryT]:
        if self._closed:
            raise RuntimeError("AI translator run owner is closed")
        if self._active is not None:
            raise RunAlreadyActiveError("AI translator already has an active run")
        if self._task_runtime is None:
            raise RuntimeError("AI task execution requires the application TaskRuntime")
        owner = OwnerRef(
            owner_id=self._owner_id,
            entrypoint="ui.ai-translator",
            project_id=project_id,
            variant_id=variant_id,
            session_id=session_id,
        )
        if prepared is not None:
            from .run_request_preparation import PreparedRun

            if not isinstance(prepared, PreparedRun):
                raise TypeError("prepared must be a PreparedRun")
            prepared.validate_admission(
                owner=owner,
                mode=mode,
                entries=entries,
                overwrite=overwrite,
                esp_path=esp_path,
                project_revision=project_revision,
                run_id=run_id,
                terminology_snapshot_ref=terminology_snapshot_ref,
            )
            config_snapshot = prepared.config_snapshot
            terminology_binding = prepared.terminology_binding
            request_budget = AiRequestBudget(prepared.max_concurrent)
            spec = replace(prepared.spec, generation=next(self._ids))
        else:
            config_snapshot = FrozenExecutionConfig(config)
            execution_config = config_snapshot.copy()
            try:
                request_budget = AiRequestBudget(getattr(execution_config, "max_concurrent", None))
            except ValueError as exc:
                raise ValueError("config.max_concurrent must be a positive integer") from exc
            from transbridge.ai_translator.project_terminology_runtime import (
                ProjectTerminologyBinding,
                freeze_project_terminology,
            )

            terminology_binding = (
                ProjectTerminologyBinding()
                if terminology_owner is None
                else freeze_project_terminology(terminology_owner, terminology_snapshot_ref)
            )
            spec = build_run_spec(
                run_id=run_id or f"ai-{uuid4().hex}",
                generation=next(self._ids),
                mode=mode,
                owner=owner,
                config=execution_config,
                entries=entries,
                esp_path=esp_path,
                overwrite=overwrite,
                project_revision=project_revision,
                terminology_snapshot=terminology_binding.snapshot_ref,
            )
        from .task_adapter import runtime_job_spec

        runtime_ref = self._task_runtime.submit(runtime_job_spec(spec), owner).ref
        spec = replace(spec, run_id=runtime_ref.run_id or runtime_ref.job_id)
        self._task_runtime.start(runtime_ref, owner)
        request = TranslationRunRequest(
            spec=spec,
            config_snapshot=config_snapshot,
            entries=tuple(entries),
            request_budget=request_budget,
            terminology_binding=terminology_binding,
            runtime_ref=runtime_ref,
        )
        self._active = _ActiveRun(request)
        return request

    def create_activity(self, request: TranslationRunRequest):
        """Return the one activity bridge owned by this run."""
        if self._active is not None and self._active.activity is not None:
            return self._active.activity
        from .task_adapter import TaskRuntimeAiTaskAdapter

        if self._task_runtime is None or request.runtime_ref is None:
            raise RuntimeError("AI activity requires an admitted TaskRuntime job")
        activity = TaskRuntimeAiTaskAdapter(self._task_runtime, request.runtime_ref, request.spec.owner, request.spec)
        if self._active is not None and self.accepts(request.run_id):
            self._active.activity = activity
        return activity

    @property
    def active_request(self) -> TranslationRunRequest | None:
        return None if self._active is None else self._active.request

    @property
    def is_running(self) -> bool:
        return self._active is not None

    def accepts(self, run_id: str) -> bool:
        return not self._closed and self._active is not None and self._active.request.run_id == run_id

    def finish(self, run_id: str) -> None:
        if not self.accepts(run_id):
            return
        self._active = None

    def cancel(self, run_id: str) -> None:
        if not self.accepts(run_id):
            return
        active = self._active
        self._active = None
        runtime, request = self._task_runtime, active.request
        if not runtime.get(request.runtime_ref, request.spec.owner).is_terminal:
            try:
                runtime.cancel(request.runtime_ref, request.spec.owner)
                runtime.finish_cancelled(request.runtime_ref, request.spec.owner)
            except TransitionError:
                if not runtime.get(request.runtime_ref, request.spec.owner).is_terminal:
                    raise
        if active.activity is not None:
            active.activity.close()

    def close(self) -> None:
        if self._active is not None:
            self.cancel(self._active.request.run_id)
        self._closed = True
