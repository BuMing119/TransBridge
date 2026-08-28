"""Typed contracts for long-running terminology workloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Protocol

from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef


class TerminologyWorkloadType(StrEnum):
    BUILD = "terminology.build"
    PUBLISH = "terminology.publish"
    REPORT_RENDER = "terminology.report.render"
    CHANGELOG_RENDER = "terminology.changelog.render"
    HISTORY_COMPARE = "terminology.history.compare"


class TerminologyPhase(StrEnum):
    CAPTURE = "capture"
    PARSE = "parse"
    ASSEMBLE = "assemble"
    EXTRACT = "extract"
    REDUCE = "reduce"
    PERSIST = "persist"
    VALIDATE = "validate"
    PUBLISH = "publish"
    RENDER = "render"
    FINALIZE = "finalize"


class BuildCompleteness(StrEnum):
    FULL = "full"
    PARTIAL = "partial"


class BuildFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class BuildLlmStatus(StrEnum):
    PERFORMED = "performed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class TerminologyExpectedState:
    project_revision: int
    variant_revision: int
    source_graph_digest: str
    source_fingerprint_digest: str
    effective_version_id: str | None = None
    base_version_id: str | None = None
    draft_id: str = "no-draft"
    draft_revision: int = 0
    build_freshness_digest: str = "current"
    effective_content_digest: str = "no-effective"
    base_content_digest: str = "no-base"
    decision_set_digest: str = "no-draft"

    def __post_init__(self) -> None:
        if min(self.project_revision, self.variant_revision, self.draft_revision) < 0:
            raise ValueError("terminology expected revisions must not be negative")
        for value, label in (
            (self.source_graph_digest, "source graph digest"),
            (self.source_fingerprint_digest, "source fingerprint digest"),
            (self.draft_id, "draft identity"),
            (self.build_freshness_digest, "build freshness digest"),
            (self.effective_content_digest, "effective content digest"),
            (self.base_content_digest, "base content digest"),
            (self.decision_set_digest, "decision set digest"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")

    @property
    def digest(self) -> str:
        payload = {
            "project_revision": self.project_revision,
            "variant_revision": self.variant_revision,
            "source_graph_digest": self.source_graph_digest,
            "source_fingerprint_digest": self.source_fingerprint_digest,
            "effective_version_id": self.effective_version_id,
            "base_version_id": self.base_version_id,
            "draft_id": self.draft_id,
            "draft_revision": self.draft_revision,
            "build_freshness_digest": self.build_freshness_digest,
            "effective_content_digest": self.effective_content_digest,
            "base_content_digest": self.base_content_digest,
            "decision_set_digest": self.decision_set_digest,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class TerminologyWorkloadRequest(Protocol):
    project_id: str
    variant_id: str
    expected: TerminologyExpectedState
    config_digest: str | None

    @property
    def workload_type(self) -> TerminologyWorkloadType: ...

    @property
    def input_ref(self) -> str: ...

    @property
    def input_fingerprint(self) -> str: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class _RequestBase:
    project_id: str
    variant_id: str
    expected: TerminologyExpectedState
    config_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip() or not self.variant_id.strip():
            raise ValueError("terminology workloads require Project and Variant identities")
        if self.config_digest is not None and not self.config_digest.strip():
            raise ValueError("config_digest must be absent or non-empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildWorkloadRequest(_RequestBase):
    build_key: str

    def __post_init__(self) -> None:
        super(BuildWorkloadRequest, self).__post_init__()
        _require_text(self.build_key, "build_key")

    @property
    def workload_type(self) -> TerminologyWorkloadType:
        return TerminologyWorkloadType.BUILD

    @property
    def input_ref(self) -> str:
        return f"{self.project_id}:{self.variant_id}:build-input"

    @property
    def input_fingerprint(self) -> str:
        return self.build_key


@dataclass(frozen=True, slots=True, kw_only=True)
class PublishWorkloadRequest(_RequestBase):
    build_ref: str
    publish_digest: str

    def __post_init__(self) -> None:
        super(PublishWorkloadRequest, self).__post_init__()
        _require_text(self.build_ref, "build_ref")
        _require_text(self.publish_digest, "publish_digest")

    @property
    def workload_type(self) -> TerminologyWorkloadType:
        return TerminologyWorkloadType.PUBLISH

    @property
    def input_ref(self) -> str:
        return self.build_ref

    @property
    def input_fingerprint(self) -> str:
        return self.publish_digest


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportRenderWorkloadRequest(_RequestBase):
    report_snapshot_ref: str
    report_snapshot_digest: str

    def __post_init__(self) -> None:
        super(ReportRenderWorkloadRequest, self).__post_init__()
        _require_text(self.report_snapshot_ref, "report_snapshot_ref")
        _require_text(self.report_snapshot_digest, "report_snapshot_digest")

    @property
    def workload_type(self) -> TerminologyWorkloadType:
        return TerminologyWorkloadType.REPORT_RENDER

    @property
    def input_ref(self) -> str:
        return self.report_snapshot_ref

    @property
    def input_fingerprint(self) -> str:
        return self.report_snapshot_digest


@dataclass(frozen=True, slots=True, kw_only=True)
class ChangelogRenderWorkloadRequest(_RequestBase):
    changelog_document_ref: str
    changelog_document_digest: str

    def __post_init__(self) -> None:
        super(ChangelogRenderWorkloadRequest, self).__post_init__()
        _require_text(self.changelog_document_ref, "changelog_document_ref")
        _require_text(self.changelog_document_digest, "changelog_document_digest")

    @property
    def workload_type(self) -> TerminologyWorkloadType:
        return TerminologyWorkloadType.CHANGELOG_RENDER

    @property
    def input_ref(self) -> str:
        return self.changelog_document_ref

    @property
    def input_fingerprint(self) -> str:
        return self.changelog_document_digest


@dataclass(frozen=True, slots=True, kw_only=True)
class HistoryCompareWorkloadRequest(_RequestBase):
    version_ref: str
    compare_digest: str

    def __post_init__(self) -> None:
        super(HistoryCompareWorkloadRequest, self).__post_init__()
        _require_text(self.version_ref, "version_ref")
        _require_text(self.compare_digest, "compare_digest")

    @property
    def workload_type(self) -> TerminologyWorkloadType:
        return TerminologyWorkloadType.HISTORY_COMPARE

    @property
    def input_ref(self) -> str:
        return self.version_ref

    @property
    def input_fingerprint(self) -> str:
        return self.compare_digest


AnyTerminologyWorkloadRequest = (
    BuildWorkloadRequest
    | PublishWorkloadRequest
    | ReportRenderWorkloadRequest
    | ChangelogRenderWorkloadRequest
    | HistoryCompareWorkloadRequest
)


@dataclass(frozen=True, slots=True)
class TerminologyProgress:
    phase: TerminologyPhase
    completed: int = 0
    total: int = 0
    current_object: str = ""
    reused: int = 0
    recomputed: int = 0
    llm_submitted: int = 0
    llm_completed: int = 0
    llm_waiting: int = 0
    llm_retries: int = 0
    llm_elapsed_ms: int = 0

    def __post_init__(self) -> None:
        values = (
            self.completed,
            self.total,
            self.reused,
            self.recomputed,
            self.llm_submitted,
            self.llm_completed,
            self.llm_waiting,
            self.llm_retries,
            self.llm_elapsed_ms,
        )
        if min(values) < 0:
            raise ValueError("terminology progress counters must not be negative")
        if self.total and self.completed > self.total:
            raise ValueError("completed terminology units must not exceed total units")

    @property
    def count_signature(self) -> tuple[int, ...]:
        return (
            self.completed,
            self.total,
            self.reused,
            self.recomputed,
            self.llm_submitted,
            self.llm_completed,
            self.llm_waiting,
            self.llm_retries,
            self.llm_elapsed_ms,
        )

    def to_payload(self, *, heartbeat_sequence: int = 0) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "completed": self.completed,
            "total": self.total,
            "current_object": self.current_object,
            "reused": self.reused,
            "recomputed": self.recomputed,
            "llm_submitted": self.llm_submitted,
            "llm_completed": self.llm_completed,
            "llm_waiting": self.llm_waiting,
            "llm_retries": self.llm_retries,
            "llm_elapsed_ms": self.llm_elapsed_ms,
            "heartbeat_sequence": heartbeat_sequence,
        }


@dataclass(frozen=True, slots=True)
class TerminologyWorkloadResult:
    workload_type: TerminologyWorkloadType
    output_ref: str | None = None
    completeness: BuildCompleteness = BuildCompleteness.FULL
    freshness: BuildFreshness = BuildFreshness.CURRENT
    llm_status: BuildLlmStatus = BuildLlmStatus.SKIPPED
    committed: bool = False
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.output_ref is not None and not self.output_ref.strip():
            raise ValueError("output_ref must be absent or non-empty")
        if any(not item.strip() for item in self.diagnostics):
            raise ValueError("terminology result diagnostic codes must not be empty")
        if self.freshness is BuildFreshness.STALE and self.committed:
            raise ValueError("stale terminology results cannot be committed")

    def stale(self, diagnostic: str) -> TerminologyWorkloadResult:
        return TerminologyWorkloadResult(
            workload_type=self.workload_type,
            output_ref=self.output_ref,
            completeness=self.completeness,
            freshness=BuildFreshness.STALE,
            llm_status=self.llm_status,
            committed=False,
            diagnostics=(*self.diagnostics, diagnostic),
        )

    def published(self) -> TerminologyWorkloadResult:
        return TerminologyWorkloadResult(
            workload_type=self.workload_type,
            output_ref=self.output_ref,
            completeness=self.completeness,
            freshness=self.freshness,
            llm_status=self.llm_status,
            committed=True,
            diagnostics=self.diagnostics,
        )


def terminology_owner(
    request: TerminologyWorkloadRequest,
    *,
    owner_id: str,
    entrypoint: str,
    permissions: frozenset[str] = frozenset(),
) -> OwnerRef:
    return OwnerRef(
        owner_id=owner_id,
        entrypoint=entrypoint,
        project_id=request.project_id,
        variant_id=request.variant_id,
        permissions=permissions,
    )


def terminology_job_spec(request: TerminologyWorkloadRequest) -> JobSpec:
    titles = {
        TerminologyWorkloadType.BUILD: "构建项目术语库",
        TerminologyWorkloadType.PUBLISH: "发布项目术语库版本",
        TerminologyWorkloadType.REPORT_RENDER: "导出术语质量报告",
        TerminologyWorkloadType.CHANGELOG_RENDER: "导出术语更新日志",
        TerminologyWorkloadType.HISTORY_COMPARE: "比较术语库历史版本",
    }
    return JobSpec(
        job_type=request.workload_type.value,
        input_ref=request.input_ref,
        input_fingerprint=request.input_fingerprint,
        display_name=titles[request.workload_type],
        config_digest=request.config_digest,
        capabilities=JobCapabilities(supports_cancel=True),
        metadata=(
            ("expected_state", request.expected.digest),
            ("project_id", request.project_id),
            ("variant_id", request.variant_id),
        ),
    )


def _require_text(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must not be empty")


__all__ = [
    "AnyTerminologyWorkloadRequest",
    "BuildCompleteness",
    "BuildFreshness",
    "BuildLlmStatus",
    "BuildWorkloadRequest",
    "ChangelogRenderWorkloadRequest",
    "HistoryCompareWorkloadRequest",
    "PublishWorkloadRequest",
    "ReportRenderWorkloadRequest",
    "TerminologyExpectedState",
    "TerminologyPhase",
    "TerminologyProgress",
    "TerminologyWorkloadRequest",
    "TerminologyWorkloadResult",
    "TerminologyWorkloadType",
    "terminology_job_spec",
    "terminology_owner",
]
