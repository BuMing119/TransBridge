"""Authoritative two-phase Project provisioning application service.

Source parsing happens during ``prepare`` and produces an isolated immutable
candidate.  ``commit`` is owner-bound and one-shot; it delegates the atomic
Project/Variant/catalog/active-pointer publication to the existing lifecycle
UnitOfWork while the lifecycle generation lock is held.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import secrets
from threading import RLock
from typing import Any

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import SCHEMA_VERSION, ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import VariantAggregate, VariantSnapshot

from .models import ActiveProject
from .provisioning_models import (
    PreparedProjectSource,
    PreparedSourceHydration,
    ProjectProvisioningCommit,
    ProjectProvisioningHydration,
    ProjectProvisioningHydrationResult,
    ProjectProvisioningIdentityPort,
    ProjectProvisioningLifecyclePort,
    ProjectProvisioningPreview,
    ProjectProvisioningRequest,
    ProjectSourcePreparationPort,
    ProjectSourceRequest,
    _name_key,
)
from .provisioning_sources import prepare_project_sources


@dataclass(frozen=True, slots=True)
class _PreparedProvisioning:
    public: ProjectProvisioningPreview
    owner_id: str
    expected_generation: int
    old_signature: tuple[str, int, str | None, int | None] | None
    commit: ProjectProvisioningCommit
    candidate: ActiveProject
    hydrations: tuple[PreparedSourceHydration, ...] = ()


@dataclass(frozen=True, slots=True)
class _OwnedProvisioningHydration:
    owner_id: str
    request_fingerprint: str
    sources: tuple[PreparedSourceHydration, ...]


class ProjectProvisioningService:
    def __init__(
        self,
        lifecycle: ProjectProvisioningLifecyclePort,
        sources: ProjectSourcePreparationPort,
        identities: ProjectProvisioningIdentityPort,
        *,
        id_factory: Callable[[], str],
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._sources = sources
        self._identities = identities
        self._id_factory = id_factory
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._prepared: dict[str, _PreparedProvisioning] = {}
        self._hydrations: dict[str, _OwnedProvisioningHydration] = {}
        self._issued_tokens: set[str] = set()
        self._lock = RLock()

    def prepare(
        self,
        request: ProjectProvisioningRequest,
        context: RequestContext,
    ) -> OperationResult[ProjectProvisioningPreview]:
        with self._lock:
            try:
                if not isinstance(request, ProjectProvisioningRequest):
                    raise DomainError(ErrorCategory.INPUT, "PROVISIONING_REQUEST_INVALID", "建项请求无效。")
                generation = self._lifecycle.generation
                old_signature = _active_signature(self._lifecycle.active)
                name_key = _name_key(request.project_name)
                if self._identities.project_name_exists(name_key):
                    raise DomainError(ErrorCategory.CONFLICT, "PROJECT_NAME_EXISTS", "已存在同名工程。")
                project_ref, variant_ref = self._allocate_refs()
                prepared_sources = prepare_project_sources(self._sources, request, context)
                baselines = prepared_sources.baselines
                registry = prepared_sources.registry(project_ref.identity.value)
                fingerprints = tuple(item.fingerprint for item in baselines)
                entries = tuple(entry for item in baselines for entry in item.entries)
                snapshot = VariantSnapshot(variant_ref, fingerprints, entries)
                project = ProjectDto(
                    SchemaEnvelope(
                        SCHEMA_VERSION,
                        project_ref.kind,
                        project_ref.identity.value,
                        0,
                        {
                            "name": request.project_name,
                            **registry.to_project_data(),
                            "variant_ids": [variant_ref.identity.value],
                            "active_variant_id": variant_ref.identity.value,
                            "variant_names": {variant_ref.identity.value: request.default_variant_name},
                        },
                    )
                )
                candidate = ActiveProject(
                    project,
                    VariantAggregate(snapshot),
                    variant_ref,
                    0,
                    0,
                )
                commit = ProjectProvisioningCommit(project, snapshot, baselines, name_key)
                token = self._new_token()
                diagnostics = (
                    *(item for source in prepared_sources.registrations for item in source.diagnostics),
                    *(
                        Diagnostic(
                            code,
                            "工程来源关系需要显式配置。",
                            DiagnosticSeverity.WARNING,
                            ErrorCategory.PREREQUISITE,
                            details=(("source_id", source_id),),
                        )
                        for code, source_id in registry.diagnostics
                    ),
                )
                preview = ProjectProvisioningPreview(
                    token,
                    request.request_fingerprint,
                    project_ref.identity.value,
                    variant_ref.identity.value,
                    request.project_name,
                    request.default_variant_name,
                    len(prepared_sources.registrations),
                    len(entries),
                    diagnostics,
                )
                self._prepared[token] = _PreparedProvisioning(
                    preview,
                    context.owner_id,
                    generation,
                    old_signature,
                    commit,
                    candidate,
                    prepared_sources.hydrations,
                )
                return OperationResult.completed(preview, run_id=context.run_id)
            except Exception as exc:  # noqa: BLE001 - application boundary maps adapter failures
                return _from_exception(exc, "PROJECT_PROVISIONING_PREPARE_FAILED", context)

    def commit(
        self,
        token: str,
        context: RequestContext,
        *,
        request_fingerprint: str | None = None,
    ) -> OperationResult[dict[str, Any]]:
        with self._lock:
            prepared = self._prepared.get(token)
            if prepared is None:
                return _failed(
                    "PROJECT_PROVISIONING_TOKEN_INVALID",
                    "建项预览已失效、未知或已经提交。",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if prepared.owner_id != context.owner_id:
                return _failed(
                    "PROJECT_PROVISIONING_OWNER_MISMATCH",
                    "建项预览属于另一个操作所有者。",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if request_fingerprint is not None and request_fingerprint != prepared.public.request_fingerprint:
                return _failed(
                    "PROJECT_PROVISIONING_REQUEST_CHANGED",
                    "建项输入已发生变化，请重新生成预览。",
                    ErrorCategory.CONFLICT,
                    context,
                )
            self._prepared.pop(token)
            result = self._lifecycle.commit_provisioning(
                prepared.commit,
                prepared.candidate,
                prepared.expected_generation,
                prepared.old_signature,
                context,
            )
            if result.is_success and prepared.hydrations:
                project_id = prepared.public.project_id
                self._hydrations[project_id] = _OwnedProvisioningHydration(
                    prepared.owner_id,
                    prepared.public.request_fingerprint,
                    prepared.hydrations,
                )
                while len(self._hydrations) > 8:
                    self._hydrations.pop(next(iter(self._hydrations)))
            return result

    def consume_hydration(
        self,
        project_id: str,
        context: RequestContext,
    ) -> ProjectProvisioningHydrationResult:
        """Consume a committed source hydration exactly once for its owner."""

        with self._lock:
            hydration = self._hydrations.get(project_id)
            if hydration is None:
                return ProjectProvisioningHydrationResult(
                    diagnostics=(
                        Diagnostic(
                            "PROJECT_HYDRATION_UNAVAILABLE",
                            "工程的临时界面数据不存在、已消费或已清理。",
                            category=ErrorCategory.PREREQUISITE,
                        ),
                    ),
                )
            if hydration.owner_id != context.owner_id:
                return ProjectProvisioningHydrationResult(
                    diagnostics=(
                        Diagnostic(
                            "PROJECT_HYDRATION_OWNER_MISMATCH",
                            "工程的临时界面数据属于另一个操作所有者。",
                            category=ErrorCategory.PERMISSION,
                        ),
                    ),
                )
            self._hydrations.pop(project_id)
            return ProjectProvisioningHydrationResult(
                ProjectProvisioningHydration(
                    project_id,
                    hydration.owner_id,
                    hydration.request_fingerprint,
                    hydration.sources[0],
                    hydration.sources[1:],
                )
            )

    def discard(self, token: str, context: RequestContext) -> OperationResult[None]:
        with self._lock:
            prepared = self._prepared.get(token)
            if prepared is None:
                return _failed(
                    "PROJECT_PROVISIONING_TOKEN_INVALID",
                    "建项预览已失效、未知或已经提交。",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if prepared.owner_id != context.owner_id:
                return _failed(
                    "PROJECT_PROVISIONING_OWNER_MISMATCH",
                    "建项预览属于另一个操作所有者。",
                    ErrorCategory.PERMISSION,
                    context,
                )
            self._prepared.pop(token)
            return OperationResult.completed(run_id=context.run_id)

    def _allocate_refs(self) -> tuple[ProjectRef, VariantRef]:
        for _ in range(8):
            project_ref = ProjectRef(ProjectId(_opaque_candidate("project", self._id_factory())))
            if self._identities.project_exists(project_ref):
                continue
            for _ in range(8):
                variant_ref = VariantRef(
                    VariantId(_opaque_candidate("variant", self._id_factory())),
                    project_ref.identity,
                )
                if not self._identities.variant_exists(variant_ref):
                    return project_ref, variant_ref
        raise DomainError(ErrorCategory.CONFLICT, "PROJECT_ID_ALLOCATION_FAILED", "无法分配唯一工程身份。")

    def _new_token(self) -> str:
        token = self._token_factory()
        if not token or token in self._prepared or token in self._issued_tokens:
            raise RuntimeError("token factory returned an empty or duplicate token")
        self._issued_tokens.add(token)
        return token


def _opaque_candidate(prefix: str, raw: str) -> str:
    token = str(raw).strip()
    candidate = f"{prefix}-{token}"
    if len(candidate) > 64 or not token:
        candidate = f"{prefix}-{hashlib.sha256(token.encode()).hexdigest()[:24]}"
    return candidate


def _active_signature(active: ActiveProject | None) -> tuple[str, int, str | None, int | None] | None:
    if active is None:
        return None
    return (
        active.project_ref.identity.value,
        active.project.envelope.revision,
        None if active.formal_variant_ref is None else active.formal_variant_ref.identity.value,
        None if active.variant is None else active.variant.revision,
    )


def _failed[T](
    code: str,
    message: str,
    category: ErrorCategory,
    context: RequestContext,
) -> OperationResult[T]:
    return OperationResult.failed(DomainError(category, code, message), run_id=context.run_id)


def _from_exception[T](
    exc: Exception,
    fallback_code: str,
    context: RequestContext,
) -> OperationResult[T]:
    if isinstance(exc, DomainError):
        return OperationResult.failed(exc, run_id=context.run_id)
    if isinstance(exc, (TypeError, ValueError)):
        return OperationResult.failed(
            DomainError(
                ErrorCategory.INPUT,
                "PROJECT_PROVISIONING_INPUT_INVALID",
                "建项输入无效。",
                cause=exc,
            ),
            run_id=context.run_id,
        )
    return OperationResult.failed(
        DomainError(
            ErrorCategory.INTERNAL,
            fallback_code,
            "建项操作在提交前失败。",
            cause=exc,
        ),
        run_id=context.run_id,
    )


__all__ = [
    "PreparedProjectSource",
    "PreparedSourceHydration",
    "ProjectProvisioningCommit",
    "ProjectProvisioningHydration",
    "ProjectProvisioningHydrationResult",
    "ProjectProvisioningIdentityPort",
    "ProjectProvisioningLifecyclePort",
    "ProjectProvisioningPreview",
    "ProjectProvisioningRequest",
    "ProjectProvisioningService",
    "ProjectSourcePreparationPort",
    "ProjectSourceRequest",
]
