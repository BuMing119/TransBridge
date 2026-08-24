"""Authoritative two-phase Project provisioning application service.

Source parsing happens during ``prepare`` and produces an isolated immutable
candidate.  ``commit`` is owner-bound and one-shot; it delegates the atomic
Project/Variant/catalog/active-pointer publication to the existing lifecycle
UnitOfWork while the lifecycle generation lock is held.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import secrets
from threading import RLock
from typing import Any, Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import EntrySnapshot, FormatId, SourceSnapshot
from transbridge.persistence.v2.ids import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope
from transbridge.persistence.v2.variant import (
    SourceBaseline,
    VariantAggregate,
    VariantSnapshot,
)

from .models import ActiveProject


@dataclass(frozen=True, slots=True)
class ProjectSourceRequest:
    """One source to validate and parse without retaining writable objects."""

    location: str
    format_hint: FormatId | None = None
    expected_fingerprint: str | None = None
    options: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        location = self.location.strip()
        if not location:
            raise ValueError("Project source location must not be empty")
        if self.expected_fingerprint is not None and not _is_sha256(self.expected_fingerprint):
            raise ValueError("expected source fingerprint must be a lowercase SHA-256 digest")
        options = _normalize_options(self.options)
        format_hint = self.format_hint
        if format_hint is not None and not isinstance(format_hint, FormatId):
            format_hint = FormatId(format_hint)
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "format_hint", format_hint)
        object.__setattr__(self, "options", options)

    def fingerprint_data(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "format_hint": None if self.format_hint is None else self.format_hint.value,
            "expected_fingerprint": self.expected_fingerprint,
            "options": dict(self.options),
        }


@dataclass(frozen=True, slots=True)
class ProjectProvisioningRequest:
    project_name: str
    default_variant_name: str = "默认"
    source: ProjectSourceRequest | None = None
    migration_sources: tuple[ProjectSourceRequest, ...] = ()
    parse_options: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        project_name = _display_name(self.project_name, "Project name")
        variant_name = _display_name(self.default_variant_name, "Variant name")
        migration_fingerprints = tuple(
            hashlib.sha256(_canonical_json(item.fingerprint_data())).hexdigest() for item in self.migration_sources
        )
        if len(migration_fingerprints) != len(set(migration_fingerprints)):
            raise ValueError("migration sources must not contain duplicates")
        if self.source is not None and not isinstance(self.source, ProjectSourceRequest):
            raise TypeError("source must be a ProjectSourceRequest or None")
        if any(not isinstance(item, ProjectSourceRequest) for item in self.migration_sources):
            raise TypeError("migration_sources must contain ProjectSourceRequest values")
        parse_options = _normalize_options(self.parse_options)
        object.__setattr__(self, "project_name", project_name)
        object.__setattr__(self, "default_variant_name", variant_name)
        object.__setattr__(self, "migration_sources", tuple(self.migration_sources))
        object.__setattr__(self, "parse_options", parse_options)

    @property
    def request_fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.fingerprint_data())).hexdigest()

    def fingerprint_data(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "default_variant_name": self.default_variant_name,
            "source": None if self.source is None else self.source.fingerprint_data(),
            "migration_sources": [item.fingerprint_data() for item in self.migration_sources],
            "parse_options": dict(self.parse_options),
        }


@dataclass(frozen=True, slots=True)
class PreparedProjectSource:
    """Read-only source candidate returned by a parser/migration adapter."""

    descriptor: tuple[tuple[str, Any], ...]
    baseline: SourceBaseline
    diagnostics: tuple[Diagnostic, ...] = ()
    hydration: PreparedSourceHydration | None = None

    def __post_init__(self) -> None:
        descriptor = dict(self.descriptor)
        _canonical_json(descriptor)
        if len(descriptor) != len(self.descriptor):
            raise ValueError("prepared source descriptor contains duplicate keys")
        if not str(descriptor.get("location", "")).strip():
            raise ValueError("prepared source descriptor requires a normalized location")
        if descriptor.get("fingerprint") != self.baseline.fingerprint.sha256:
            raise ValueError("prepared source descriptor and baseline fingerprint differ")
        object.__setattr__(self, "descriptor", tuple(sorted(self.descriptor)))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.descriptor)


@dataclass(frozen=True, slots=True)
class PreparedSourceHydration:
    """Ephemeral primary-source read model retained across prepare/commit."""

    location: str
    fingerprint: str
    format_id: FormatId
    source_snapshot: SourceSnapshot
    entries: tuple[EntrySnapshot, ...]

    def __post_init__(self) -> None:
        if not self.location.strip():
            raise ValueError("hydration location must not be empty")
        if not _is_sha256(self.fingerprint):
            raise ValueError("hydration fingerprint must be a lowercase SHA-256 digest")
        if self.source_snapshot.sha256 != self.fingerprint:
            raise ValueError("hydration snapshot fingerprint mismatch")
        if any(not isinstance(entry, EntrySnapshot) for entry in self.entries):
            raise TypeError("hydration entries must be immutable EntrySnapshot values")


@dataclass(frozen=True, slots=True)
class ProjectProvisioningHydration:
    project_id: str
    owner_id: str
    request_fingerprint: str
    source: PreparedSourceHydration


@dataclass(frozen=True, slots=True)
class ProjectProvisioningHydrationResult:
    """In-process result for a deliberately non-serializable UI read model."""

    value: ProjectProvisioningHydration | None = None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if (self.value is None) == (not self.diagnostics):
            raise ValueError("hydration result must contain either a value or diagnostics")

    @property
    def is_success(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class ProjectProvisioningCommit:
    project: ProjectDto
    variant: VariantSnapshot
    baselines: tuple[SourceBaseline, ...]
    project_name_key: str

    def __post_init__(self) -> None:
        if self.variant.ref.project_id.value != self.project.envelope.identity:
            raise ValueError("provisioned Variant must belong to its Project")
        if self.variant.ref.identity.value not in self.project.envelope.data["variant_ids"]:
            raise ValueError("provisioned Variant must be declared by its Project")
        if self.project.envelope.data.get("active_variant_id") != self.variant.ref.identity.value:
            raise ValueError("provisioned Variant must be the Project active Variant")
        expected = tuple(item.fingerprint for item in self.baselines)
        if self.variant.source_fingerprints != tuple(sorted(expected, key=lambda item: item.namespace)):
            raise ValueError("provisioned baseline and Variant source fingerprints differ")
        if self.project_name_key != _name_key(str(self.project.envelope.data["name"])):
            raise ValueError("provisioned Project catalog key is not canonical")

    @property
    def project_ref(self) -> ProjectRef:
        return ProjectRef(ProjectId(self.project.envelope.identity))

    @property
    def variant_ref(self) -> VariantRef:
        return self.variant.ref


@dataclass(frozen=True, slots=True)
class ProjectProvisioningPreview:
    token: str
    request_fingerprint: str
    project_id: str
    variant_id: str
    project_name: str
    variant_name: str
    source_count: int
    entry_count: int
    diagnostics: tuple[Diagnostic, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "request_fingerprint": self.request_fingerprint,
            "project_id": self.project_id,
            "variant_id": self.variant_id,
            "project_name": self.project_name,
            "variant_name": self.variant_name,
            "source_count": self.source_count,
            "entry_count": self.entry_count,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


class ProjectSourcePreparationPort(Protocol):
    def prepare_source(
        self,
        request: ProjectSourceRequest,
        context: RequestContext,
        *,
        role: str,
        common_options: tuple[tuple[str, Any], ...],
    ) -> PreparedProjectSource: ...


class ProjectProvisioningIdentityPort(Protocol):
    def project_exists(self, ref: ProjectRef) -> bool: ...

    def variant_exists(self, ref: VariantRef) -> bool: ...

    def project_name_exists(self, name_key: str) -> bool: ...


class ProjectProvisioningLifecyclePort(Protocol):
    @property
    def generation(self) -> int: ...

    @property
    def active(self) -> ActiveProject | None: ...

    def commit_provisioning(
        self,
        provisioning: ProjectProvisioningCommit,
        candidate: ActiveProject,
        expected_generation: int,
        expected_active_signature: tuple[str, int, str | None, int | None] | None,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class _PreparedProvisioning:
    public: ProjectProvisioningPreview
    owner_id: str
    expected_generation: int
    old_signature: tuple[str, int, str | None, int | None] | None
    commit: ProjectProvisioningCommit
    candidate: ActiveProject
    hydration: PreparedSourceHydration | None = None


@dataclass(frozen=True, slots=True)
class _OwnedProvisioningHydration:
    owner_id: str
    request_fingerprint: str
    source: PreparedSourceHydration


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
                prepared_sources = self._prepare_sources(request, context)
                baselines = tuple(item.baseline for item in prepared_sources)
                descriptors = tuple(item.to_dict() for item in prepared_sources)
                fingerprints = tuple(item.fingerprint for item in baselines)
                entries = tuple(entry for item in baselines for entry in item.entries)
                snapshot = VariantSnapshot(variant_ref, fingerprints, entries)
                project = ProjectDto(
                    SchemaEnvelope(
                        2,
                        project_ref.kind,
                        project_ref.identity.value,
                        0,
                        {
                            "name": request.project_name,
                            "sources": list(descriptors),
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
                diagnostics = tuple(item for source in prepared_sources for item in source.diagnostics)
                preview = ProjectProvisioningPreview(
                    token,
                    request.request_fingerprint,
                    project_ref.identity.value,
                    variant_ref.identity.value,
                    request.project_name,
                    request.default_variant_name,
                    len(prepared_sources),
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
                    prepared_sources[0].hydration if request.source is not None else None,
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
            if result.is_success and prepared.hydration is not None:
                project_id = prepared.public.project_id
                self._hydrations[project_id] = _OwnedProvisioningHydration(
                    prepared.owner_id,
                    prepared.public.request_fingerprint,
                    prepared.hydration,
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
                    hydration.source,
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

    def _prepare_sources(
        self,
        request: ProjectProvisioningRequest,
        context: RequestContext,
    ) -> tuple[PreparedProjectSource, ...]:
        candidates: list[tuple[ProjectSourceRequest, str]] = []
        if request.source is not None:
            candidates.append((request.source, "primary"))
        candidates.extend((source, "migration") for source in request.migration_sources)
        prepared = tuple(
            self._sources.prepare_source(
                source,
                context,
                role=role,
                common_options=request.parse_options,
            )
            for source, role in candidates
        )
        locations = [str(item.to_dict().get("location", "")) for item in prepared]
        namespaces = [item.baseline.fingerprint.namespace for item in prepared]
        if len(set(map(str.casefold, locations))) != len(locations):
            raise DomainError(ErrorCategory.CONFLICT, "PROJECT_SOURCE_PATH_DUPLICATE", "工程来源路径重复。")
        if len(set(namespaces)) != len(namespaces):
            raise DomainError(ErrorCategory.CONFLICT, "PROJECT_SOURCE_IDENTITY_DUPLICATE", "工程来源身份重复。")
        return prepared

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


def _display_name(value: str, label: str) -> str:
    name = value.strip()
    if not name or len(name) > 80 or any(character in name for character in "\r\n\t"):
        raise ValueError(f"{label} must be 1-80 printable characters")
    return name


def _name_key(value: str) -> str:
    return value.strip().casefold()


def _opaque_candidate(prefix: str, raw: str) -> str:
    token = str(raw).strip()
    candidate = f"{prefix}-{token}"
    if len(candidate) > 64 or not token:
        candidate = f"{prefix}-{hashlib.sha256(token.encode()).hexdigest()[:24]}"
    return candidate


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("provisioning options must contain finite JSON values") from exc


def _normalize_options(values: tuple[tuple[str, Any], ...]) -> tuple[tuple[str, Any], ...]:
    if any(not isinstance(key, str) or not key.strip() for key, _ in values):
        raise ValueError("provisioning option keys must be non-empty strings")
    if len({key for key, _ in values}) != len(values):
        raise ValueError("provisioning option keys must be unique")
    normalized = tuple(sorted(values, key=lambda item: item[0]))
    _canonical_json(dict(normalized))
    return normalized


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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
    "ProjectProvisioningCommit",
    "ProjectProvisioningIdentityPort",
    "ProjectProvisioningLifecyclePort",
    "ProjectProvisioningPreview",
    "ProjectProvisioningRequest",
    "ProjectProvisioningService",
    "ProjectSourcePreparationPort",
    "ProjectSourceRequest",
]
