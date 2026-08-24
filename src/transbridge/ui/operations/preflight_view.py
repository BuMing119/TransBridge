"""Immutable operation preflight and result projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.security.hitl import ConfirmationToken

from .plan_view import OperationKind


class PreflightCheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PreflightCheckState:
    check_id: str
    label: str
    status: PreflightCheckStatus
    reason: str = ""
    repair_intent: str | None = None

    def __post_init__(self) -> None:
        if not self.check_id.strip() or not self.label.strip():
            raise ValueError("preflight checks require an id and label")
        if self.status is not PreflightCheckStatus.PASSED and not self.reason.strip():
            raise ValueError("non-passing preflight checks require a reason")


@dataclass(frozen=True, slots=True)
class OperationPreflightResult:
    kind: OperationKind
    request_digest: str
    target_revision: str
    checks: tuple[PreflightCheckState, ...]
    expected_side_effects: tuple[str, ...]
    confirmation_token: ConfirmationToken | None = None

    def __post_init__(self) -> None:
        if len(self.request_digest) != 64 or not self.target_revision.strip():
            raise ValueError("preflight requires a request digest and target revision")
        if len({item.check_id for item in self.checks}) != len(self.checks):
            raise ValueError("preflight check ids must be unique")

    @property
    def ready(self) -> bool:
        return not any(item.status is PreflightCheckStatus.BLOCKED for item in self.checks)


class OperationObjectStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OperationObjectResult:
    object_ref: str
    label: str
    status: OperationObjectStatus
    code: str = ""
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class OperationResultActionState:
    run_id: str
    kind: OperationKind
    objects: tuple[OperationObjectResult, ...]
    artifact_refs: tuple[str, ...] = ()
    report_ref: str | None = None
    retry_failed_enabled: bool = False
    retry_disabled_reason: str = ""

    @property
    def failed_refs(self) -> tuple[str, ...]:
        return tuple(
            item.object_ref
            for item in self.objects
            if item.status in {OperationObjectStatus.FAILED, OperationObjectStatus.CANCELLED}
        )
