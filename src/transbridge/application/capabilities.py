"""Explicit capability availability registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import Any


@dataclass(frozen=True, order=True, slots=True)
class CapabilityId:
    value: str

    def __post_init__(self) -> None:
        if not self.value or not self.value.strip():
            raise ValueError("CapabilityId value must not be empty")

    def __str__(self) -> str:
        return self.value


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    capability: CapabilityId
    state: CapabilityState
    reasons: tuple[str, ...] = ()
    missing_prerequisites: tuple[str, ...] = ()
    metadata: tuple[tuple[str, Any], ...] = ()

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        if self.state is CapabilityState.UNAVAILABLE and not (self.reasons or self.missing_prerequisites):
            raise ValueError("unavailable capabilities require a reason or missing prerequisite")
        if self.state is CapabilityState.AVAILABLE and self.missing_prerequisites:
            raise ValueError("available capabilities cannot have missing prerequisites")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "capability": self.capability.value,
            "state": self.state.value,
            "reasons": list(self.reasons),
            "missing_prerequisites": list(self.missing_prerequisites),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityReport:
        version = int(data.get("schema_version", 1))
        if version > cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported CapabilityReport schema version: {version}")
        metadata = data.get("metadata") or {}
        return cls(
            capability=CapabilityId(str(data["capability"])),
            state=CapabilityState(data["state"]),
            reasons=tuple(str(reason) for reason in data.get("reasons", ())),
            missing_prerequisites=tuple(str(item) for item in data.get("missing_prerequisites", ())),
            metadata=tuple(sorted(metadata.items())),
        )


class CapabilityRegistry:
    """Thread-safe registry populated by real adapters and context probes."""

    def __init__(self, reports: Iterable[CapabilityReport] = ()) -> None:
        self._lock = RLock()
        self._reports: dict[CapabilityId, CapabilityReport] = {}
        for report in reports:
            self.register(report)

    def register(self, report: CapabilityReport) -> None:
        with self._lock:
            self._reports[report.capability] = report

    def unregister(self, capability: CapabilityId | str) -> None:
        capability_id = _capability_id(capability)
        with self._lock:
            self._reports.pop(capability_id, None)

    def report(self, capability: CapabilityId | str) -> CapabilityReport:
        capability_id = _capability_id(capability)
        with self._lock:
            report = self._reports.get(capability_id)
        if report is not None:
            return report
        return CapabilityReport(
            capability_id,
            CapabilityState.UNAVAILABLE,
            reasons=("Capability is not registered.",),
            missing_prerequisites=("registration",),
        )

    def snapshot(self) -> tuple[CapabilityReport, ...]:
        with self._lock:
            return tuple(self._reports[key] for key in sorted(self._reports))

    def is_available(self, capability: CapabilityId | str) -> bool:
        return self.report(capability).state is CapabilityState.AVAILABLE


def capability_report_json_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://transbridge.local/schemas/capability-report-v1.json",
        "type": "object",
        "required": [
            "schema_version",
            "capability",
            "state",
            "reasons",
            "missing_prerequisites",
            "metadata",
        ],
        "properties": {
            "schema_version": {"const": 1},
            "capability": {"type": "string", "minLength": 1},
            "state": {"enum": [state.value for state in CapabilityState]},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "missing_prerequisites": {"type": "array", "items": {"type": "string"}},
            "metadata": {"type": "object"},
        },
        "additionalProperties": False,
    }


def _capability_id(value: CapabilityId | str) -> CapabilityId:
    return value if isinstance(value, CapabilityId) else CapabilityId(value)


__all__ = [
    "CapabilityId",
    "CapabilityRegistry",
    "CapabilityReport",
    "CapabilityState",
    "capability_report_json_schema",
]
