"""Project-owned remote-service bindings and ParaTranz target resolution."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.v2.models import ProjectDto, SchemaEnvelope

from .lifecycle import ProjectLifecycleService


class ParaTranzTargetSource(StrEnum):
    EXPLICIT = "explicit"
    PROJECT_BINDING = "project_binding"
    UNBOUND = "unbound"


class ParaTranzTargetStatus(StrEnum):
    UNBOUND = "unbound"
    UNVERIFIED = "unverified"
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    NOT_MEMBER = "not_member"
    ACCOUNT_MISMATCH = "account_mismatch"
    ENDPOINT_MISMATCH = "endpoint_mismatch"
    AUTHENTICATION_FAILED = "authentication_failed"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"


@dataclass(frozen=True, slots=True)
class ParaTranzProjectBinding:
    project_id: int
    project_name: str
    endpoint: str
    account_user_id: int | None = None
    bound_at: str | None = None
    validated_at: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.project_id, bool) or self.project_id <= 0:
            raise ValueError("ParaTranz project_id must be a positive integer")
        normalized = normalize_paratranz_endpoint(self.endpoint)
        object.__setattr__(self, "endpoint", normalized)
        object.__setattr__(self, "project_name", self.project_name.strip())
        if self.account_user_id is not None and (isinstance(self.account_user_id, bool) or self.account_user_id <= 0):
            raise ValueError("ParaTranz account_user_id must be a positive integer")
        for field_name in ("bound_at", "validated_at"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_iso_timestamp(value, field_name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "endpoint": self.endpoint,
            "account_user_id": self.account_user_id,
            "bound_at": self.bound_at,
            "validated_at": self.validated_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ParaTranzProjectBinding:
        return cls(
            project_id=_required_int(value, "project_id"),
            project_name=str(value.get("project_name", "")),
            endpoint=str(value.get("endpoint", "")),
            account_user_id=_optional_int(value, "account_user_id"),
            bound_at=_optional_string(value, "bound_at"),
            validated_at=_optional_string(value, "validated_at"),
        )


@dataclass(frozen=True, slots=True)
class ResolvedParaTranzTarget:
    project_id: int | None
    project_name: str
    endpoint: str
    account_user_id: int | None
    source: ParaTranzTargetSource
    status: ParaTranzTargetStatus
    binding_revision: int | None = None
    reason: str | None = None

    @property
    def is_executable(self) -> bool:
        return self.project_id is not None and self.status is ParaTranzTargetStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "endpoint": self.endpoint,
            "account_user_id": self.account_user_id,
            "source": self.source.value,
            "status": self.status.value,
            "binding_revision": self.binding_revision,
            "reason": self.reason,
        }


class ParaTranzTargetResolver:
    """Resolve an operation target without consulting ParaTranz browse state."""

    def resolve(
        self,
        *,
        binding: ParaTranzProjectBinding | Mapping[str, Any] | None,
        binding_revision: int | None = None,
        explicit_project_id: int | None = None,
        explicit_project_name: str = "",
        endpoint: str = "",
        account_user_id: int | None = None,
        explicit_verified: bool = False,
    ) -> ResolvedParaTranzTarget:
        current_endpoint = normalize_paratranz_endpoint(endpoint) if endpoint.strip() else ""
        if explicit_project_id is not None:
            if isinstance(explicit_project_id, bool) or explicit_project_id <= 0:
                raise ValueError("explicit ParaTranz project_id must be a positive integer")
            return ResolvedParaTranzTarget(
                explicit_project_id,
                explicit_project_name.strip(),
                current_endpoint,
                account_user_id,
                ParaTranzTargetSource.EXPLICIT,
                ParaTranzTargetStatus.AVAILABLE if explicit_verified else ParaTranzTargetStatus.UNVERIFIED,
            )

        parsed = _coerce_binding(binding)
        if parsed is None:
            return ResolvedParaTranzTarget(
                None,
                "",
                current_endpoint,
                account_user_id,
                ParaTranzTargetSource.UNBOUND,
                ParaTranzTargetStatus.UNBOUND,
                binding_revision,
                "当前本地工程尚未绑定 ParaTranz 项目。",
            )
        if current_endpoint and parsed.endpoint != current_endpoint:
            return _resolved_binding(
                parsed,
                binding_revision,
                ParaTranzTargetStatus.ENDPOINT_MISMATCH,
                "当前 ParaTranz 服务地址与工程绑定不一致。",
            )
        if (
            account_user_id is not None
            and parsed.account_user_id is not None
            and account_user_id != parsed.account_user_id
        ):
            return _resolved_binding(
                parsed,
                binding_revision,
                ParaTranzTargetStatus.ACCOUNT_MISMATCH,
                "当前 ParaTranz 账号与工程绑定不一致。",
            )
        if parsed.validated_at is not None:
            return _resolved_binding(
                parsed,
                binding_revision,
                ParaTranzTargetStatus.AVAILABLE,
                "该目标已在绑定时验证；执行前仍会重新校验成员权限。",
            )
        return _resolved_binding(
            parsed,
            binding_revision,
            ParaTranzTargetStatus.UNVERIFIED,
            "执行前需要验证 ParaTranz 项目与成员权限。",
        )


class ProjectRemoteBindingService:
    """Application command for atomically changing the active Project binding."""

    def __init__(self, lifecycle: ProjectLifecycleService) -> None:
        self._lifecycle = lifecycle

    def set_paratranz_binding(
        self,
        binding: ParaTranzProjectBinding,
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
    ) -> OperationResult[dict[str, Any]]:
        active = self._lifecycle.active
        if active is None:
            return _binding_failure(
                "ACTIVE_PROJECT_REQUIRED",
                "需要先打开本地工程，才能保存 ParaTranz 同步目标。",
                ErrorCategory.PREREQUISITE,
                context,
            )
        expected = active.project.envelope.revision if expected_project_revision is None else expected_project_revision
        try:
            project = project_with_paratranz_binding(active.project, binding, expected_revision=expected)
        except Exception as exc:  # noqa: BLE001 - return typed application diagnostics
            return OperationResult.from_exception(exc, run_id=context.run_id)
        return self._lifecycle.commit_project_update(project, expected, context)

    def clear_paratranz_binding(
        self,
        context: RequestContext,
        *,
        expected_project_revision: int | None = None,
    ) -> OperationResult[dict[str, Any]]:
        active = self._lifecycle.active
        if active is None:
            return _binding_failure(
                "ACTIVE_PROJECT_REQUIRED",
                "需要先打开本地工程，才能解除 ParaTranz 同步目标。",
                ErrorCategory.PREREQUISITE,
                context,
            )
        expected = active.project.envelope.revision if expected_project_revision is None else expected_project_revision
        try:
            project = project_with_paratranz_binding(active.project, None, expected_revision=expected)
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)
        if project.envelope.revision == active.project.envelope.revision:
            return OperationResult.completed(
                {
                    "project_id": active.project_ref.identity.value,
                    "project_revision": active.project.envelope.revision,
                    "paratranz_binding": None,
                },
                run_id=context.run_id,
            )
        return self._lifecycle.commit_project_update(project, expected, context)


def project_paratranz_binding(project: ProjectDto) -> ParaTranzProjectBinding | None:
    remote = project.envelope.data.get("remote_bindings")
    if remote is None:
        return None
    if not isinstance(remote, Mapping):
        raise ValueError("Project remote_bindings must be an object")
    value = remote.get("paratranz")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Project ParaTranz binding must be an object")
    return ParaTranzProjectBinding.from_mapping(value)


def project_with_paratranz_binding(
    project: ProjectDto,
    binding: ParaTranzProjectBinding | None,
    *,
    expected_revision: int,
) -> ProjectDto:
    envelope = project.envelope
    if envelope.revision != expected_revision:
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PROJECT_BINDING_REVISION_CONFLICT",
            "本地工程在绑定操作前已发生变化，请刷新后重试。",
        )
    data = deepcopy(envelope.data)
    remote = data.get("remote_bindings")
    if remote is None:
        bindings: dict[str, Any] = {}
    elif isinstance(remote, Mapping):
        bindings = deepcopy(dict(remote))
    else:
        raise ValueError("Project remote_bindings must be an object")
    old_value = bindings.get("paratranz")
    new_value = None if binding is None else binding.to_dict()
    if old_value == new_value:
        return project
    if binding is None:
        bindings.pop("paratranz", None)
    else:
        bindings["paratranz"] = new_value
    if bindings:
        data["remote_bindings"] = bindings
    else:
        data.pop("remote_bindings", None)
    return ProjectDto(
        SchemaEnvelope(
            envelope.schema_version,
            envelope.entity_type,
            envelope.identity,
            envelope.revision + 1,
            data,
        )
    )


def normalize_paratranz_endpoint(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("ParaTranz endpoint must not be empty")
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ParaTranz endpoint must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ParaTranz endpoint must not contain user credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("ParaTranz endpoint must not contain a query or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/api"):
        path = path[:-4]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path.rstrip("/"), "", ""))


def _coerce_binding(
    value: ParaTranzProjectBinding | Mapping[str, Any] | None,
) -> ParaTranzProjectBinding | None:
    if value is None or isinstance(value, ParaTranzProjectBinding):
        return value
    return ParaTranzProjectBinding.from_mapping(value)


def _resolved_binding(
    binding: ParaTranzProjectBinding,
    revision: int | None,
    status: ParaTranzTargetStatus,
    reason: str | None,
) -> ResolvedParaTranzTarget:
    return ResolvedParaTranzTarget(
        binding.project_id,
        binding.project_name,
        binding.endpoint,
        binding.account_user_id,
        ParaTranzTargetSource.PROJECT_BINDING,
        status,
        revision,
        reason,
    )


def _required_int(value: Mapping[str, Any], key: str) -> int:
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"ParaTranz {key} must be an integer")
    return raw


def _optional_int(value: Mapping[str, Any], key: str) -> int | None:
    raw = value.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"ParaTranz {key} must be an integer or null")
    return raw


def _optional_string(value: Mapping[str, Any], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"ParaTranz {key} must be a string or null")
    return raw


def _validate_iso_timestamp(value: str, field_name: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"ParaTranz {field_name} must be an ISO 8601 timestamp") from exc


def _binding_failure(
    code: str,
    message: str,
    category: ErrorCategory,
    context: RequestContext,
) -> OperationResult[dict[str, Any]]:
    return OperationResult.failed(DomainError(category, code, message), run_id=context.run_id)


__all__ = [
    "ParaTranzProjectBinding",
    "ParaTranzTargetResolver",
    "ParaTranzTargetSource",
    "ParaTranzTargetStatus",
    "ProjectRemoteBindingService",
    "ResolvedParaTranzTarget",
    "normalize_paratranz_endpoint",
    "project_paratranz_binding",
    "project_with_paratranz_binding",
]
