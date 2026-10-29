"""Immutable operation preflight and result projection contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from transbridge.application.preflight.operation import (
    OperationPreflightResult as OperationPreflightResult,
    PreflightCheckState as PreflightCheckState,
    PreflightCheckStatus as PreflightCheckStatus,
)

from .plan_view import OperationKind


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
