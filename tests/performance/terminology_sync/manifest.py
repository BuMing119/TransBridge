"""Schema for observed FR5.17 measurements that cannot claim a release pass."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from .dataset import TerminologySyncPerformanceProfile

PERFORMANCE_MANIFEST_SCHEMA_VERSION = 1
DIAGNOSTIC_ONLY = "diagnostic_only_no_formal_thresholds"


@dataclass(frozen=True, slots=True)
class TerminologySyncPerformanceManifest:
    profile: TerminologySyncPerformanceProfile
    environment: tuple[tuple[str, str], ...]
    phase_timings_ms: tuple[tuple[str, float], ...]
    peak_rss_bytes: int | None
    recovered_rss_bytes: int | None
    cancellation_feedback_ms: float | None = None
    evidence_kind: str = DIAGNOSTIC_ONLY
    release_gate_eligible: bool = False
    schema_version: int = PERFORMANCE_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PERFORMANCE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("unsupported terminology sync performance manifest schema")
        if self.evidence_kind != DIAGNOSTIC_ONLY or self.release_gate_eligible:
            raise ValueError("diagnostic measurements cannot claim a formal release gate")
        environment = tuple(sorted((str(key), str(value)) for key, value in self.environment))
        if any(not key or not value for key, value in environment):
            raise ValueError("performance environment entries must be non-empty")
        if len({key for key, _value in environment}) != len(environment):
            raise ValueError("performance environment keys must be unique")
        object.__setattr__(self, "environment", environment)
        timings = tuple(sorted((str(name), float(value)) for name, value in self.phase_timings_ms))
        if any(not name or value < 0 for name, value in timings):
            raise ValueError("phase timings require names and non-negative observations")
        if len({name for name, _value in timings}) != len(timings):
            raise ValueError("phase timing names must be unique")
        object.__setattr__(self, "phase_timings_ms", timings)
        for value in (self.peak_rss_bytes, self.recovered_rss_bytes):
            if value is not None and value < 0:
                raise ValueError("RSS observations must be absent or non-negative")
        if self.cancellation_feedback_ms is not None and self.cancellation_feedback_ms < 0:
            raise ValueError("cancellation feedback must be absent or non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "release_gate_eligible": self.release_gate_eligible,
            "profile": {**asdict(self.profile), "dataset_digest": self.profile.dataset_digest},
            "environment": dict(self.environment),
            "phase_timings_ms": dict(self.phase_timings_ms),
            "peak_rss_bytes": self.peak_rss_bytes,
            "recovered_rss_bytes": self.recovered_rss_bytes,
            "cancellation_feedback_ms": self.cancellation_feedback_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "DIAGNOSTIC_ONLY",
    "PERFORMANCE_MANIFEST_SCHEMA_VERSION",
    "TerminologySyncPerformanceManifest",
]
