"""Canonical synchronous application operation result."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any

from .errors import DomainError, ErrorCategory, map_exception


class OperationOutcome(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    category: ErrorCategory | None = None
    retryable: bool = False
    details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.code or not self.code.strip():
            raise ValueError("Diagnostic code must not be empty")
        if not self.message or not self.message.strip():
            raise ValueError("Diagnostic message must not be empty")

    @classmethod
    def from_error(cls, error: DomainError) -> Diagnostic:
        return cls(
            code=error.code,
            message=error.message,
            category=error.category,
            retryable=error.retryable,
            details=tuple(sorted(error.details.items())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "category": self.category.value if self.category else None,
            "retryable": self.retryable,
            "details": dict(self.details),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Diagnostic:
        details = data.get("details") or {}
        category = data.get("category")
        return cls(
            code=str(data["code"]),
            message=str(data["message"]),
            severity=DiagnosticSeverity(data.get("severity", DiagnosticSeverity.ERROR.value)),
            category=ErrorCategory(category) if category else None,
            retryable=bool(data.get("retryable", False)),
            details=tuple(sorted(details.items())),
        )


@dataclass(frozen=True, slots=True)
class OperationCounts:
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0

    def __post_init__(self) -> None:
        if min(self.succeeded, self.failed, self.skipped, self.cancelled) < 0:
            raise ValueError("Operation counts must not be negative")

    @property
    def total(self) -> int:
        return self.succeeded + self.failed + self.skipped + self.cancelled

    def to_dict(self) -> dict[str, int]:
        return {
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "cancelled": self.cancelled,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationCounts:
        counts = cls(
            succeeded=int(data.get("succeeded", 0)),
            failed=int(data.get("failed", 0)),
            skipped=int(data.get("skipped", 0)),
            cancelled=int(data.get("cancelled", 0)),
        )
        if "total" in data and int(data["total"]) != counts.total:
            raise ValueError("Operation count total does not match its components")
        return counts


@dataclass(frozen=True, slots=True)
class OperationResult[T]:
    """A mutually exclusive, JSON-serializable application result."""

    outcome: OperationOutcome
    value: T | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    counts: OperationCounts = field(default_factory=OperationCounts)
    artifact_refs: tuple[str, ...] = ()
    run_id: str | None = None

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        error_diagnostics = sum(d.severity is DiagnosticSeverity.ERROR for d in self.diagnostics)
        if self.outcome is OperationOutcome.COMPLETED and (
            self.counts.failed or self.counts.cancelled or error_diagnostics
        ):
            raise ValueError("completed results cannot contain failures")
        if self.outcome is OperationOutcome.PARTIAL:
            if self.counts.succeeded < 1 or self.counts.failed + self.counts.cancelled < 1:
                raise ValueError("partial results require both successful and unsuccessful counts")
            if not error_diagnostics:
                raise ValueError("partial results require an error diagnostic")
        if self.outcome is OperationOutcome.FAILED:
            if self.value is not None or self.artifact_refs:
                raise ValueError("failed results cannot carry committed values or artifacts")
            if self.counts.failed < 1 or not error_diagnostics:
                raise ValueError("failed results require a failure count and error diagnostic")
        if self.outcome is OperationOutcome.CANCELLED:
            if self.value is not None or self.artifact_refs:
                raise ValueError("cancelled results cannot carry committed values or artifacts")
            if self.counts.cancelled < 1:
                raise ValueError("cancelled results require a cancellation count")

    @property
    def is_success(self) -> bool:
        return self.outcome is OperationOutcome.COMPLETED

    @classmethod
    def completed(
        cls,
        value: T | None = None,
        *,
        diagnostics: tuple[Diagnostic, ...] = (),
        counts: OperationCounts | None = None,
        artifact_refs: tuple[str, ...] = (),
        run_id: str | None = None,
    ) -> OperationResult[T]:
        return cls(
            OperationOutcome.COMPLETED,
            value,
            diagnostics,
            counts or OperationCounts(),
            artifact_refs,
            run_id,
        )

    @classmethod
    def partial(
        cls,
        value: T,
        *,
        counts: OperationCounts,
        diagnostics: tuple[Diagnostic, ...],
        artifact_refs: tuple[str, ...] = (),
        run_id: str | None = None,
    ) -> OperationResult[T]:
        return cls(OperationOutcome.PARTIAL, value, diagnostics, counts, artifact_refs, run_id)

    @classmethod
    def failed(cls, error: DomainError, *, run_id: str | None = None) -> OperationResult[T]:
        return cls(
            OperationOutcome.FAILED,
            diagnostics=(Diagnostic.from_error(error),),
            counts=OperationCounts(failed=1),
            run_id=run_id,
        )

    @classmethod
    def cancelled(
        cls,
        diagnostic: Diagnostic | None = None,
        *,
        run_id: str | None = None,
    ) -> OperationResult[T]:
        return cls(
            OperationOutcome.CANCELLED,
            diagnostics=(diagnostic,) if diagnostic else (),
            counts=OperationCounts(cancelled=1),
            run_id=run_id,
        )

    @classmethod
    def from_exception(cls, exc: BaseException, *, run_id: str | None = None) -> OperationResult[T]:
        error = map_exception(exc)
        if error.category is ErrorCategory.CANCELLED:
            return cls.cancelled(Diagnostic.from_error(error), run_id=run_id)
        return cls.failed(error, run_id=run_id)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.SCHEMA_VERSION,
            "outcome": self.outcome.value,
            "value": self.value,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "counts": self.counts.to_dict(),
            "artifact_refs": list(self.artifact_refs),
            "run_id": self.run_id,
        }
        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            raise TypeError("OperationResult contains a non-JSON-serializable value") from exc
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperationResult[Any]:
        version = int(data.get("schema_version", 1))
        if version > cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported OperationResult schema version: {version}")
        return cls(
            outcome=OperationOutcome(data["outcome"]),
            value=data.get("value"),
            diagnostics=tuple(Diagnostic.from_dict(item) for item in data.get("diagnostics", ())),
            counts=OperationCounts.from_dict(data.get("counts") or {}),
            artifact_refs=tuple(str(ref) for ref in data.get("artifact_refs", ())),
            run_id=None if data.get("run_id") is None else str(data["run_id"]),
        )


def operation_result_json_schema() -> dict[str, Any]:
    """Return the transport-neutral JSON Schema for OperationResult v1."""

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://transbridge.local/schemas/operation-result-v1.json",
        "type": "object",
        "required": ["schema_version", "outcome", "diagnostics", "counts", "artifact_refs"],
        "properties": {
            "schema_version": {"const": 1},
            "outcome": {"enum": [outcome.value for outcome in OperationOutcome]},
            "value": {},
            "diagnostics": {"type": "array", "items": {"type": "object"}},
            "counts": {
                "type": "object",
                "required": ["succeeded", "failed", "skipped", "cancelled", "total"],
            },
            "artifact_refs": {"type": "array", "items": {"type": "string"}},
            "run_id": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }
