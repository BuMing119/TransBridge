"""Immutable contracts for the transactional FOMOD pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import re
from typing import Any

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, OperationOutcome


class FomodStageId(StrEnum):
    DISCOVER = "discover"
    EXTRACT = "extract"
    DIFF = "diff"
    MIGRATE = "migrate"
    TRANSLATE = "translate"
    XML = "xml"
    FILTER = "filter"
    BUILD = "build"
    PUBLISH = "publish"


FOMOD_STAGE_ORDER = tuple(FomodStageId)


@dataclass(frozen=True, slots=True)
class FomodPolicies:
    archive: str = "archive-policy-v2"
    translation_memory: str = "tm-key-then-text-v2"
    ai: str = "auto-translator-v1"
    filtering: str = "fomod-filter-v1"
    publishing: str = "guarded-pack-v1"

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in self.as_tuple()):
            raise ValueError("FOMOD policy identifiers must not be empty")

    def as_tuple(self) -> tuple[str, ...]:
        return (
            self.archive,
            self.translation_memory,
            self.ai,
            self.filtering,
            self.publishing,
        )


@dataclass(frozen=True, slots=True)
class FomodRunSpec:
    run_id: str
    new_archive: str
    new_archive_hash: str
    output_archive: str
    target_locale: str
    config_hash: str
    policies: FomodPolicies = field(default_factory=FomodPolicies)
    old_archive: str | None = None
    old_archive_hash: str | None = None
    output_format: str = "zip"
    workspace_root: str | None = None
    ai_enabled: bool = True
    required_stages: frozenset[FomodStageId] = field(default_factory=lambda: frozenset(FOMOD_STAGE_ORDER))

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "new_archive",
            "new_archive_hash",
            "output_archive",
            "target_locale",
            "config_hash",
            "output_format",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if (self.old_archive is None) != (self.old_archive_hash is None):
            raise ValueError("old_archive and old_archive_hash must be provided together")
        if self.run_id in {".", ".."} or re.search(r"[\\/:]", self.run_id):
            raise ValueError("run_id must be one safe path segment")
        if self.old_archive is not None and not self.old_archive.strip():
            raise ValueError("old_archive must not be empty")
        if self.old_archive_hash is not None and not self.old_archive_hash.strip():
            raise ValueError("old_archive_hash must not be empty")
        if self.workspace_root is not None and not self.workspace_root.strip():
            raise ValueError("workspace_root must not be empty when provided")
        if not isinstance(self.policies, FomodPolicies):
            raise TypeError("policies must be FomodPolicies")
        if self.output_format not in {"zip", "7z"}:
            raise ValueError("output_format must be zip or 7z")
        if not isinstance(self.required_stages, frozenset) or any(
            not isinstance(stage, FomodStageId) for stage in self.required_stages
        ):
            raise TypeError("required_stages must be a frozenset of FomodStageId")
        if FomodStageId.PUBLISH not in self.required_stages:
            raise ValueError("publish must remain a required FOMOD stage")
        if not self.required_stages.issubset(set(FOMOD_STAGE_ORDER)):
            raise ValueError("required_stages contains an unknown stage")

    @property
    def workspace(self) -> Path:
        if self.workspace_root is None:
            raise ValueError("workspace_root must be bound before pipeline execution")
        return Path(self.workspace_root) / self.run_id


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    kind: str
    location: str
    fingerprint: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()
    verified: bool = True

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip() for value in (self.artifact_id, self.kind, self.location)
        ):
            raise ValueError("artifact identity, kind, and location must not be empty")
        if not isinstance(self.attributes, tuple):
            raise TypeError("artifact attributes must be an immutable tuple")
        if any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) for key, value in self.attributes
        ):
            raise ValueError("artifact attributes must contain non-empty string keys and string values")
        if len({key for key, _ in self.attributes}) != len(self.attributes):
            raise ValueError("artifact attribute keys must be unique")

    def attribute(self, key: str) -> str | None:
        return dict(self.attributes).get(key)


MetricValue = int | float | str | bool


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: FomodStageId
    outcome: OperationOutcome
    artifacts: tuple[ArtifactRef, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    metrics: tuple[tuple[str, MetricValue], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stage, FomodStageId) or not isinstance(self.outcome, OperationOutcome):
            raise TypeError("stage and outcome must use their canonical enums")
        if not isinstance(self.artifacts, tuple) or not isinstance(self.diagnostics, tuple):
            raise TypeError("stage artifacts and diagnostics must be immutable tuples")
        if not isinstance(self.metrics, tuple) or any(
            not isinstance(value, (int, float, str, bool)) for _, value in self.metrics
        ):
            raise TypeError("stage metrics must be immutable scalar pairs")
        if len({artifact.artifact_id for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("stage artifact ids must be unique")
        if any(not artifact.verified for artifact in self.artifacts):
            raise ValueError("stage results may expose only verified artifacts")
        if any(not isinstance(key, str) or not key.strip() for key, _ in self.metrics):
            raise ValueError("stage metric keys must be non-empty strings")
        if len({key for key, _ in self.metrics}) != len(self.metrics):
            raise ValueError("stage metric keys must be unique")
        error_count = sum(diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in self.diagnostics)
        if self.outcome is OperationOutcome.COMPLETED and error_count:
            raise ValueError("completed stage results cannot contain error diagnostics")
        if self.outcome is OperationOutcome.PARTIAL and not error_count:
            raise ValueError("partial stage results require an error diagnostic")
        if self.outcome in {OperationOutcome.FAILED, OperationOutcome.CANCELLED} and self.artifacts:
            raise ValueError("failed/cancelled stage results cannot expose artifacts")
        if self.outcome is OperationOutcome.FAILED and not error_count:
            raise ValueError("failed stage results require an error diagnostic")

    @classmethod
    def completed(
        cls,
        stage: FomodStageId,
        *,
        artifacts: tuple[ArtifactRef, ...] = (),
        diagnostics: tuple[Diagnostic, ...] = (),
        metrics: tuple[tuple[str, MetricValue], ...] = (),
    ) -> StageResult:
        return cls(stage, OperationOutcome.COMPLETED, artifacts, diagnostics, metrics)

    @classmethod
    def failed(cls, stage: FomodStageId, code: str, message: str) -> StageResult:
        return cls(
            stage,
            OperationOutcome.FAILED,
            diagnostics=(Diagnostic(code, message),),
        )

    @classmethod
    def cancelled(cls, stage: FomodStageId, message: str) -> StageResult:
        return cls(
            stage,
            OperationOutcome.CANCELLED,
            diagnostics=(
                Diagnostic(
                    "FOMOD_STAGE_CANCELLED",
                    message,
                    severity=DiagnosticSeverity.WARNING,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    run_id: str
    target_locale: str
    config_hash: str
    outcome: OperationOutcome
    stages: tuple[StageResult, ...]
    artifacts: tuple[ArtifactRef, ...]
    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.target_locale.strip() or not self.config_hash.strip():
            raise ValueError("pipeline identity fields must not be empty")
        terminal_stages = tuple(result.stage for result in self.stages)
        if not all(isinstance(value, tuple) for value in (self.stages, self.artifacts, self.diagnostics)):
            raise TypeError("pipeline collections must be immutable tuples")
        if not isinstance(self.outcome, OperationOutcome):
            raise TypeError("pipeline outcome must be OperationOutcome")
        if len(set(terminal_stages)) != len(terminal_stages):
            raise ValueError("pipeline stages must be unique")
        if len({artifact.artifact_id for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("pipeline artifact ids must be unique")
        if self.outcome is OperationOutcome.COMPLETED and any(
            result.outcome is not OperationOutcome.COMPLETED for result in self.stages
        ):
            raise ValueError("completed pipeline cannot contain non-completed stages")
        if self.outcome is OperationOutcome.PARTIAL and not any(
            result.outcome is not OperationOutcome.COMPLETED for result in self.stages
        ):
            raise ValueError("partial pipeline requires an incomplete optional stage")
        error_count = sum(diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in self.diagnostics)
        if self.outcome is OperationOutcome.COMPLETED and error_count:
            raise ValueError("completed pipeline cannot contain error diagnostics")
        if self.outcome in {OperationOutcome.PARTIAL, OperationOutcome.FAILED} and not error_count:
            raise ValueError("partial/failed pipeline requires an error diagnostic")
        if self.outcome in {OperationOutcome.FAILED, OperationOutcome.CANCELLED} and any(
            artifact.kind == "published-archive" for artifact in self.artifacts
        ):
            raise ValueError("failed/cancelled pipeline cannot expose a published artifact")

    def stage(self, stage: FomodStageId) -> StageResult | None:
        return next((result for result in self.stages if result.stage is stage), None)

    def metric(self, stage: FomodStageId, key: str, default: Any = None) -> Any:
        result = self.stage(stage)
        return default if result is None else dict(result.metrics).get(key, default)


class StageEventType(StrEnum):
    STARTED = "started"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class StageEvent:
    run_id: str
    stage: FomodStageId
    event_type: StageEventType
    result: StageResult | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("stage event run_id must not be empty")
        if self.event_type is StageEventType.STARTED and self.result is not None:
            raise ValueError("started stage events cannot contain a result")
        if self.event_type is StageEventType.FINISHED and self.result is None:
            raise ValueError("finished stage events require a result")
        if self.result is not None and self.result.stage is not self.stage:
            raise ValueError("stage event/result stage ids must match")
