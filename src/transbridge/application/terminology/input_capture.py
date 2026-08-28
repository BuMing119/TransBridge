"""Authoritative, immutable input capture for Project terminology builds."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import CapabilityLevel, FormatCapabilitySnapshot, FormatId, SourceSnapshot
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceRegistration,
    SourceRegistrySnapshot,
    SourceRelation,
)
from transbridge.persistence.v2.models import ProjectDto
from transbridge.persistence.v2.variant import VariantSnapshot

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TerminologyBaseline:
    effective_version_id: str | None = None
    effective_content_digest: str = hashlib.sha256(b"").hexdigest()
    draft_id: str = "no-draft"
    draft_base_version_id: str | None = None
    draft_base_content_digest: str = hashlib.sha256(b"").hexdigest()
    draft_revision: int = 0
    decision_digest: str = hashlib.sha256(b"[]").hexdigest()

    def __post_init__(self) -> None:
        if not self.draft_id.strip():
            raise ValueError("draft identity must be explicit")
        if self.draft_revision < 0:
            raise ValueError("draft revision must not be negative")
        for value, label in (
            (self.effective_content_digest, "effective content digest"),
            (self.draft_base_content_digest, "draft base content digest"),
            (self.decision_digest, "decision digest"),
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ProjectVariantCapture:
    """One Project/Variant clone produced under a lifecycle/repository lock."""

    project: ProjectDto
    variant: VariantSnapshot

    def __post_init__(self) -> None:
        project_id = self.project.envelope.identity
        if self.variant.ref.project_id.value != project_id:
            raise ValueError("captured Variant belongs to a different Project")
        active_id = self.project.envelope.data.get("active_variant_id")
        if active_id != self.variant.ref.identity.value:
            raise ValueError("captured Variant is not the Project's active Variant")


@dataclass(frozen=True, slots=True)
class SourceLease:
    source_id: str
    snapshot: SourceSnapshot
    actual_fingerprint: str

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source lease requires a source identity")
        if self.snapshot.sha256 != self.actual_fingerprint or not _SHA256.fullmatch(self.actual_fingerprint):
            raise ValueError("source lease fingerprint does not match its immutable snapshot")


@dataclass(frozen=True, slots=True)
class CapturedSource:
    registration: SourceRegistration
    lease: SourceLease
    adapter_id: str
    adapter_version: str
    capability: FormatCapabilitySnapshot
    parse_options: tuple[tuple[str, Any], ...]

    def __post_init__(self) -> None:
        if self.registration.source_id != self.lease.source_id:
            raise ValueError("captured source registration and lease identities differ")
        if self.capability.format_id is not self.registration.format_id:
            raise ValueError("captured source adapter capability has a different format")
        options = tuple(sorted(self.parse_options))
        json.dumps(dict(options), ensure_ascii=False, allow_nan=False, sort_keys=True)
        object.__setattr__(self, "parse_options", options)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.registration.source_id,
            "format_id": self.registration.format_id.value,
            "actual_fingerprint": self.lease.actual_fingerprint,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "parse_options": dict(self.parse_options),
        }


@dataclass(frozen=True, slots=True)
class BuildInputSnapshot:
    project_id: str
    project_revision: int
    variant_id: str
    variant_revision: int
    variant_snapshot: VariantSnapshot
    variant_content_digest: str
    sources: tuple[CapturedSource, ...]
    relations: tuple[SourceRelation, ...]
    config_digest: str
    effective_version_id: str | None
    draft_id: str
    draft_base_version_id: str | None
    draft_revision: int
    decision_digest: str
    effective_content_digest: str = hashlib.sha256(b"").hexdigest()
    draft_base_content_digest: str = hashlib.sha256(b"").hexdigest()

    def __post_init__(self) -> None:
        if self.project_revision < 0 or self.variant_revision < 0 or self.draft_revision < 0:
            raise ValueError("captured revisions must not be negative")
        for value, label in (
            (self.variant_content_digest, "variant content digest"),
            (self.config_digest, "configuration digest"),
            (self.decision_digest, "decision digest"),
            (self.effective_content_digest, "effective content digest"),
            (self.draft_base_content_digest, "draft base content digest"),
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        if self.variant_snapshot.ref.project_id.value != self.project_id:
            raise ValueError("BuildInputSnapshot Variant belongs to a different Project")
        if self.variant_snapshot.ref.identity.value != self.variant_id:
            raise ValueError("BuildInputSnapshot Variant identity mismatch")
        sources = tuple(sorted(self.sources, key=lambda item: item.registration.source_id))
        relations = tuple(sorted(self.relations, key=lambda item: item.relation_id))
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "relations", relations)

    def canonical_payload(self) -> dict[str, Any]:
        """Return the stable business payload consumed by build-key identity."""

        return {
            "schema": "terminology-build-input-v1",
            "project_id": self.project_id,
            "project_revision": self.project_revision,
            "variant_id": self.variant_id,
            "variant_revision": self.variant_revision,
            "variant_content_digest": self.variant_content_digest,
            "sources": [item.canonical_payload() for item in self.sources],
            "relations": [item.to_dict() for item in self.relations],
            "config_digest": self.config_digest,
            "effective_version_id": self.effective_version_id,
            "effective_content_digest": self.effective_content_digest,
            "draft": {
                "draft_id": self.draft_id,
                "base_version_id": self.draft_base_version_id,
                "base_content_digest": self.draft_base_content_digest,
                "revision": self.draft_revision,
                "decision_digest": self.decision_digest,
            },
        }


class ProjectVariantCapturePort(Protocol):
    def capture_project_variant(self) -> ProjectVariantCapture | None:
        """Clone Project and complete active Variant under one consistency lock."""


class SourceLeasePort(Protocol):
    def acquire(self, registration: SourceRegistration) -> SourceLease: ...

    def current_fingerprint(self, lease: SourceLease) -> str: ...


class FormatCapabilityPort(Protocol):
    def capability_for(self, format_id: FormatId) -> FormatCapabilitySnapshot | None: ...


class TerminologyBaselinePort(Protocol):
    def capture_baseline(self, project_id: str, variant_id: str) -> TerminologyBaseline: ...


class TerminologyBuildInputPort(Protocol):
    def capture_build_input(
        self,
        context: RequestContext,
        *,
        config: Mapping[str, Any],
    ) -> OperationResult[BuildInputSnapshot]: ...


class BuildInputCaptureService:
    def __init__(
        self,
        projects: ProjectVariantCapturePort,
        leases: SourceLeasePort,
        capabilities: FormatCapabilityPort,
        baselines: TerminologyBaselinePort,
        *,
        max_unstreamed_source_count: int | None = None,
        max_unstreamed_source_bytes: int | None = None,
        max_unstreamed_total_bytes: int | None = None,
    ) -> None:
        for value, label in (
            (max_unstreamed_source_count, "source-count capture limit"),
            (max_unstreamed_source_bytes, "per-source capture limit"),
            (max_unstreamed_total_bytes, "total capture limit"),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError(f"{label} must be a positive integer or absent")
        self._projects = projects
        self._leases = leases
        self._capabilities = capabilities
        self._baselines = baselines
        self._max_unstreamed_source_count = max_unstreamed_source_count
        self._max_unstreamed_source_bytes = max_unstreamed_source_bytes
        self._max_unstreamed_total_bytes = max_unstreamed_total_bytes

    def capture_build_input(
        self,
        context: RequestContext,
        *,
        config: Mapping[str, Any],
    ) -> OperationResult[BuildInputSnapshot]:
        try:
            state = self._projects.capture_project_variant()
            if state is None:
                return _failure(context, "TERMINOLOGY_PROJECT_REQUIRED", "术语构建需要已打开的工程。")
            project_id = state.project.envelope.identity
            variant_id = state.variant.ref.identity.value
            if context.project_id is not None and context.project_id != project_id:
                return _failure(context, "TERMINOLOGY_PROJECT_CONTEXT_MISMATCH", "请求上下文指向另一个工程。")
            if context.variant_id is not None and context.variant_id != variant_id:
                return _failure(context, "TERMINOLOGY_VARIANT_CONTEXT_MISMATCH", "请求上下文指向另一个版本。")
            registry = SourceRegistrySnapshot.from_project_data(state.project.envelope.data)
            enabled = tuple(item for item in registry.sources if item.enabled)
            if not enabled:
                return _failure(context, "TERMINOLOGY_SOURCE_REQUIRED", "术语构建需要至少一个已启用来源。")
            if self._max_unstreamed_source_count is not None and len(enabled) > self._max_unstreamed_source_count:
                return _failure(
                    context,
                    "TERMINOLOGY_STREAMING_REQUIRED",
                    "已登记来源数量超过当前完整载入构建器的安全边界。",
                    source_count=len(enabled),
                    limit_count=self._max_unstreamed_source_count,
                    recovery="减少本次启用来源，或在流式构建能力可用后重试。",
                )
            relation_error = _relation_error(registry, enabled)
            if relation_error is not None:
                return _failed_diagnostics(context, (relation_error,))

            resolved_capabilities: dict[str, FormatCapabilitySnapshot] = {}
            for registration in enabled:
                capability = self._capabilities.capability_for(registration.format_id)
                if (
                    capability is None
                    or capability.adapter_id is None
                    or capability.adapter_version is None
                    or capability.capability.read is not CapabilityLevel.SUPPORTED
                ):
                    return _failure(
                        context,
                        "TERMINOLOGY_SOURCE_CAPABILITY_UNAVAILABLE",
                        "工程来源缺少受支持的读取适配器。",
                        source_id=registration.source_id,
                        format_id=registration.format_id.value,
                    )
                resolved_capabilities[registration.source_id] = capability

            source_size = getattr(self._leases, "source_size", None)
            if callable(source_size):
                preflight_total = 0
                for registration in enabled:
                    source_bytes = source_size(registration)
                    if isinstance(source_bytes, bool) or not isinstance(source_bytes, int) or source_bytes < 0:
                        raise ValueError("source-size preflight returned an invalid byte count")
                    if (
                        self._max_unstreamed_source_bytes is not None
                        and source_bytes > self._max_unstreamed_source_bytes
                    ):
                        return _source_size_failure(
                            context,
                            registration.source_id,
                            source_bytes,
                            self._max_unstreamed_source_bytes,
                        )
                    preflight_total += source_bytes
                    if (
                        self._max_unstreamed_total_bytes is not None
                        and preflight_total > self._max_unstreamed_total_bytes
                    ):
                        return _total_size_failure(
                            context,
                            registration.source_id,
                            preflight_total,
                            self._max_unstreamed_total_bytes,
                        )

            captured: list[CapturedSource] = []
            captured_bytes = 0
            for registration in enabled:
                capability = resolved_capabilities[registration.source_id]
                acquire_bounded = getattr(self._leases, "acquire_bounded", None)
                remaining_total = (
                    None
                    if self._max_unstreamed_total_bytes is None
                    else self._max_unstreamed_total_bytes - captured_bytes
                )
                capture_limit = _minimum_limit(self._max_unstreamed_source_bytes, remaining_total)
                lease = (
                    acquire_bounded(registration, max_bytes=capture_limit)
                    if callable(acquire_bounded)
                    else self._leases.acquire(registration)
                )
                source_bytes = lease.snapshot.size_bytes
                if self._max_unstreamed_source_bytes is not None and source_bytes > self._max_unstreamed_source_bytes:
                    return _source_size_failure(
                        context,
                        registration.source_id,
                        source_bytes,
                        self._max_unstreamed_source_bytes,
                    )
                captured_bytes += source_bytes
                if self._max_unstreamed_total_bytes is not None and captured_bytes > self._max_unstreamed_total_bytes:
                    return _total_size_failure(
                        context,
                        registration.source_id,
                        captured_bytes,
                        self._max_unstreamed_total_bytes,
                    )
                options = dict(registration.format_options)
                if registration.format_id is FormatId.PLUGIN_SSE:
                    options["discover_sibling_strings"] = False
                captured.append(
                    CapturedSource(
                        registration,
                        lease,
                        capability.adapter_id,
                        capability.adapter_version,
                        capability,
                        tuple(options.items()),
                    )
                )

            stale: list[Diagnostic] = []
            for source in captured:
                current = self._leases.current_fingerprint(source.lease)
                if current != source.lease.actual_fingerprint:
                    stale.append(
                        _diagnostic(
                            "TERMINOLOGY_SOURCE_CHANGED_DURING_CAPTURE",
                            "工程来源在捕获期间发生变化，构建输入已失效。",
                            source_id=source.registration.source_id,
                        )
                    )
            if stale:
                return _failed_diagnostics(context, tuple(stale))

            baseline = self._baselines.capture_baseline(project_id, variant_id)
            snapshot = BuildInputSnapshot(
                project_id=project_id,
                project_revision=state.project.envelope.revision,
                variant_id=variant_id,
                variant_revision=state.variant.revision,
                variant_snapshot=state.variant,
                variant_content_digest=_variant_digest(state.variant),
                sources=tuple(captured),
                relations=registry.relations,
                config_digest=_canonical_digest(config),
                effective_version_id=baseline.effective_version_id,
                draft_id=baseline.draft_id,
                draft_base_version_id=baseline.draft_base_version_id,
                draft_revision=baseline.draft_revision,
                decision_digest=baseline.decision_digest,
                effective_content_digest=baseline.effective_content_digest,
                draft_base_content_digest=baseline.draft_base_content_digest,
            )
            return OperationResult.completed(
                snapshot,
                counts=OperationCounts(succeeded=len(captured)),
                run_id=context.run_id,
            )
        except DomainError as exc:
            return OperationResult.failed(exc, run_id=context.run_id)
        except (KeyError, TypeError, ValueError) as exc:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.INPUT,
                    "TERMINOLOGY_BUILD_INPUT_INVALID",
                    "术语构建输入无效。",
                    cause=exc,
                ),
                run_id=context.run_id,
            )
        except Exception as exc:  # noqa: BLE001 - adapter boundary maps failure without leaking paths
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.INTERNAL,
                    "TERMINOLOGY_BUILD_INPUT_CAPTURE_FAILED",
                    "术语构建输入捕获失败。",
                    cause=exc,
                ),
                run_id=context.run_id,
            )


def _relation_error(
    registry: SourceRegistrySnapshot,
    enabled: tuple[SourceRegistration, ...],
) -> Diagnostic | None:
    enabled_ids = {item.source_id for item in enabled}
    for code, source_id in registry.diagnostics:
        if source_id in enabled_ids and code in {"SOURCE_RELATION_REQUIRED", "SOURCE_RELATION_AMBIGUOUS"}:
            return _diagnostic(code, "工程来源关系尚未完成配置。", source_id=source_id)
    related = {item.from_source_id for item in registry.relations if item.to_source_id in enabled_ids}
    for registration in enabled:
        if (
            registration.bilingual_capability is BilingualCapability.REQUIRES_RELATION
            and registration.source_id not in related
        ):
            return _diagnostic(
                "SOURCE_RELATION_REQUIRED",
                "该来源需要显式关联到已登记的目标来源。",
                source_id=registration.source_id,
            )
    return None


def _variant_digest(snapshot: VariantSnapshot) -> str:
    data = snapshot.to_dto().envelope.data
    return _canonical_digest({
        "source_fingerprints": [item.to_dict() for item in snapshot.source_fingerprints],
        "entries": [item.to_dict() for item in snapshot.entries],
        "label_library": data["label_library"],
    })


def _canonical_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _failure(
    context: RequestContext,
    code: str,
    message: str,
    **details: Any,
) -> OperationResult[BuildInputSnapshot]:
    return _failed_diagnostics(context, (_diagnostic(code, message, **details),))


def _source_size_failure(
    context: RequestContext,
    source_id: str,
    source_bytes: int,
    limit_bytes: int,
) -> OperationResult[BuildInputSnapshot]:
    return _failure(
        context,
        "TERMINOLOGY_STREAMING_REQUIRED",
        "该来源超过当前完整载入适配器的安全边界，需要流式读取能力后才能构建。",
        source_id=source_id,
        source_bytes=source_bytes,
        limit_bytes=limit_bytes,
        recovery="拆分来源或使用支持流式读取的适配器后重试。",
    )


def _total_size_failure(
    context: RequestContext,
    source_id: str,
    captured_bytes: int,
    limit_bytes: int,
) -> OperationResult[BuildInputSnapshot]:
    return _failure(
        context,
        "TERMINOLOGY_STREAMING_REQUIRED",
        "已登记来源的总大小超过当前完整载入构建器的安全边界。",
        source_id=source_id,
        captured_bytes=captured_bytes,
        limit_bytes=limit_bytes,
        recovery="减少本次启用来源，或在流式构建能力可用后重试。",
    )


def _minimum_limit(*values: int | None) -> int | None:
    present = tuple(value for value in values if value is not None)
    return min(present) if present else None


def _diagnostic(code: str, message: str, **details: Any) -> Diagnostic:
    return Diagnostic(
        code,
        message,
        category=ErrorCategory.PREREQUISITE,
        details=tuple(sorted(details.items())),
    )


def _failed_diagnostics(
    context: RequestContext,
    diagnostics: tuple[Diagnostic, ...],
) -> OperationResult[BuildInputSnapshot]:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=diagnostics,
        counts=OperationCounts(failed=max(1, len(diagnostics))),
        run_id=context.run_id,
    )


__all__ = [
    "BuildInputCaptureService",
    "BuildInputSnapshot",
    "CapturedSource",
    "FormatCapabilityPort",
    "ProjectVariantCapture",
    "ProjectVariantCapturePort",
    "SourceLease",
    "SourceLeasePort",
    "TerminologyBaseline",
    "TerminologyBaselinePort",
    "TerminologyBuildInputPort",
]
