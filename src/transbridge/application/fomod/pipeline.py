"""Framework-neutral FOMOD stage DAG and publication gate."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from transbridge.application.contracts import Diagnostic, OperationOutcome
from transbridge.application.tasks import TaskCancelled

from .models import (
    FOMOD_STAGE_ORDER,
    ArtifactRef,
    FomodRunSpec,
    FomodStageId,
    PipelineResult,
    StageEvent,
    StageEventType,
    StageResult,
)


class CancellationSignal(Protocol):
    @property
    def is_cancelled(self) -> bool: ...


class StageEventSink(Protocol):
    def publish(self, event: StageEvent) -> None: ...


class RunGuard(Protocol):
    def allows(self, run_id: str) -> bool: ...


class CommitGuard(Protocol):
    def commit(self, run_id: str, mutation: Callable[[], None]) -> bool: ...


@dataclass(frozen=True, slots=True)
class StageContext:
    spec: FomodRunSpec
    workspace: Path
    artifacts: Mapping[str, ArtifactRef]
    cancellation: object | None
    commit_guard: CommitGuard

    def require(self, artifact_id: str) -> ArtifactRef:
        try:
            artifact = self.artifacts[artifact_id]
        except KeyError as exc:
            raise KeyError(f"required artifact is unavailable: {artifact_id}") from exc
        if not artifact.verified:
            raise ValueError(f"artifact is not verified: {artifact_id}")
        return artifact


class PipelineStage(Protocol):
    stage_id: FomodStageId
    required_artifacts: tuple[str, ...]

    def execute(self, context: StageContext) -> StageResult: ...


class StageExecutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AlwaysRunGuard:
    def allows(self, run_id: str) -> bool:
        return bool(run_id)


class DirectCommitGuard:
    def __init__(self, cancellation: object | None = None) -> None:
        self._cancellation = cancellation

    def commit(self, run_id: str, mutation: Callable[[], None]) -> bool:
        if not run_id or _cancelled(self._cancellation):
            return False
        mutation()
        return True


class NullStageEventSink:
    def publish(self, event: StageEvent) -> None:
        del event


class PipelineEngine:
    """Execute the fixed stage order and expose only verified artifacts."""

    def __init__(
        self,
        stages: Sequence[PipelineStage],
        *,
        event_sink: StageEventSink | None = None,
        run_guard: RunGuard | None = None,
        commit_guard: CommitGuard | None = None,
    ) -> None:
        by_id = {stage.stage_id: stage for stage in stages}
        if len(by_id) != len(stages):
            raise ValueError("FOMOD stages must have unique ids")
        if tuple(by_id) != FOMOD_STAGE_ORDER:
            raise ValueError("FOMOD stages must be provided in the canonical order")
        self._stages = tuple(stages)
        self._event_sink = event_sink or NullStageEventSink()
        self._run_guard = run_guard or AlwaysRunGuard()
        self._commit_guard = commit_guard

    def run(self, spec: FomodRunSpec, cancellation: object | None = None) -> PipelineResult:
        try:
            workspace = spec.workspace
            workspace.mkdir(parents=True, exist_ok=False)
        except (OSError, ValueError) as exc:
            result = StageResult.failed(
                FomodStageId.DISCOVER,
                "FOMOD_WORKSPACE_UNAVAILABLE",
                f"{type(exc).__name__}: {exc}",
            )
            self._record_result(spec.run_id, result)
            return _pipeline_result(
                spec,
                OperationOutcome.FAILED,
                [result],
                {},
                list(result.diagnostics),
            )
        commit_guard = self._commit_guard or DirectCommitGuard(cancellation)
        artifacts: dict[str, ArtifactRef] = {}
        results: list[StageResult] = []
        diagnostics: list[Diagnostic] = []
        optional_incomplete = False

        for stage in self._stages:
            try:
                run_allowed = self._run_guard.allows(spec.run_id)
            except Exception as exc:
                result = StageResult.failed(
                    stage.stage_id,
                    "FOMOD_RUN_GUARD_FAILED",
                    f"{type(exc).__name__}: {exc}",
                )
                self._record_result(spec.run_id, result)
                results.append(result)
                diagnostics.extend(result.diagnostics)
                return _pipeline_result(
                    spec,
                    OperationOutcome.FAILED,
                    results,
                    artifacts,
                    diagnostics,
                )
            if _cancelled(cancellation) or not run_allowed:
                result = StageResult.cancelled(stage.stage_id, "run guard rejected further work")
                self._record_result(spec.run_id, result)
                results.append(result)
                diagnostics.extend(result.diagnostics)
                return _pipeline_result(
                    spec,
                    OperationOutcome.CANCELLED,
                    results,
                    artifacts,
                    diagnostics,
                )

            missing = tuple(
                artifact_id
                for artifact_id in stage.required_artifacts
                if artifact_id not in artifacts or not artifacts[artifact_id].verified
            )
            if missing:
                result = StageResult.failed(
                    stage.stage_id,
                    "FOMOD_STAGE_INPUT_MISSING",
                    f"required verified artifacts are missing: {', '.join(missing)}",
                )
            else:
                self._publish(StageEvent(spec.run_id, stage.stage_id, StageEventType.STARTED))
                context = StageContext(
                    spec=spec,
                    workspace=workspace,
                    artifacts=MappingProxyType(dict(artifacts)),
                    cancellation=cancellation,
                    commit_guard=commit_guard,
                )
                try:
                    result = stage.execute(context)
                    if result.stage is not stage.stage_id:
                        raise ValueError("stage returned a result for a different stage id")
                except TaskCancelled as exc:
                    result = StageResult.cancelled(stage.stage_id, str(exc) or "cancelled")
                except StageExecutionError as exc:
                    result = StageResult.failed(stage.stage_id, exc.code, str(exc))
                except BaseException as exc:  # noqa: BLE001 - stage boundary is fail-closed
                    result = StageResult.failed(
                        stage.stage_id,
                        _diagnostic_code(exc, stage.stage_id),
                        f"{type(exc).__name__}: {exc}",
                    )

            if result.outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                duplicate = next(
                    (artifact.artifact_id for artifact in result.artifacts if artifact.artifact_id in artifacts),
                    None,
                )
                if duplicate is not None:
                    result = StageResult.failed(
                        stage.stage_id,
                        "FOMOD_ARTIFACT_DUPLICATE",
                        f"artifact id was already produced: {duplicate}",
                    )
                else:
                    artifacts.update((artifact.artifact_id, artifact) for artifact in result.artifacts)

            self._record_result(spec.run_id, result)
            results.append(result)
            diagnostics.extend(result.diagnostics)

            if result.outcome is OperationOutcome.CANCELLED:
                return _pipeline_result(
                    spec,
                    OperationOutcome.CANCELLED,
                    results,
                    artifacts,
                    diagnostics,
                )
            if result.outcome is not OperationOutcome.COMPLETED:
                if result.outcome is OperationOutcome.PARTIAL and result.artifacts:
                    # A guarded mutation already published verified output.  It
                    # cannot truthfully be downgraded to FAILED merely because
                    # post-commit evidence was incomplete.
                    optional_incomplete = True
                    continue
                if stage.stage_id in spec.required_stages:
                    diagnostics.append(
                        Diagnostic(
                            "FOMOD_REQUIRED_STAGE_INCOMPLETE",
                            f"required stage did not complete: {stage.stage_id.value}",
                        )
                    )
                    return _pipeline_result(
                        spec,
                        OperationOutcome.FAILED,
                        results,
                        artifacts,
                        diagnostics,
                    )
                optional_incomplete = True

        outcome = OperationOutcome.PARTIAL if optional_incomplete else OperationOutcome.COMPLETED
        return _pipeline_result(spec, outcome, results, artifacts, diagnostics)

    def _record_result(self, run_id: str, result: StageResult) -> None:
        self._publish(StageEvent(run_id, result.stage, StageEventType.FINISHED, result))

    def _publish(self, event: StageEvent) -> None:
        try:
            self._event_sink.publish(event)
        except Exception:
            # Projections are observers and cannot own the business outcome.
            return


def _pipeline_result(
    spec: FomodRunSpec,
    outcome: OperationOutcome,
    results: list[StageResult],
    artifacts: dict[str, ArtifactRef],
    diagnostics: list[Diagnostic],
) -> PipelineResult:
    exposed = tuple(artifacts.values())
    if outcome in {OperationOutcome.FAILED, OperationOutcome.CANCELLED}:
        exposed = tuple(artifact for artifact in exposed if artifact.kind != "published-archive")
    return PipelineResult(
        run_id=spec.run_id,
        target_locale=spec.target_locale,
        config_hash=spec.config_hash,
        outcome=outcome,
        stages=tuple(results),
        artifacts=exposed,
        diagnostics=tuple(diagnostics),
    )


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _diagnostic_code(exc: BaseException, stage: FomodStageId) -> str:
    """Preserve a structured stage diagnostic across adapter/module boundaries."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code.startswith("FOMOD_") and code.isascii():
        return code
    return f"FOMOD_{stage.value.upper()}_FAILED"
