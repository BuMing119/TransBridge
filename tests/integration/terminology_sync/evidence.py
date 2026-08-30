"""Traceable, non-release-claiming evidence manifests for FR5.17 tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

EVIDENCE_SCHEMA_VERSION = 1
RELEASE_GATE_BLOCKED = "blocked_pending_full_s08_and_fr5_16_s12"
_SECRET_KEYS = frozenset({"authorization", "cookie", "token", "api_key", "password"})


@dataclass(frozen=True, slots=True)
class TerminologySyncEvidenceManifest:
    test_node_id: str
    scenario_id: str
    fixture_seed: int
    local_version_id: str
    local_content_digest: str
    remote_snapshot_digest: str
    baseline_revision: int | None
    plan_hash: str
    run_id: str
    outcome_counts: tuple[tuple[str, int], ...]
    request_counts: tuple[tuple[str, int], ...]
    observed_timings_ms: tuple[tuple[str, float], ...] = ()
    functional_observation: str = "observed"
    release_gate: str = RELEASE_GATE_BLOCKED
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, label in (
            (self.test_node_id, "test node ID"),
            (self.scenario_id, "scenario ID"),
            (self.local_version_id, "local version ID"),
            (self.local_content_digest, "local content digest"),
            (self.remote_snapshot_digest, "remote snapshot digest"),
            (self.plan_hash, "plan hash"),
            (self.run_id, "run ID"),
            (self.functional_observation, "functional observation"),
            (self.release_gate, "release gate"),
        ):
            if not value.strip():
                raise ValueError(f"{label} must not be empty")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported terminology sync evidence schema")
        if self.baseline_revision is not None and self.baseline_revision < 0:
            raise ValueError("baseline revision must be absent or non-negative")
        object.__setattr__(self, "outcome_counts", _counts(self.outcome_counts))
        object.__setattr__(self, "request_counts", _counts(self.request_counts))
        timings = tuple(sorted((str(name), float(value)) for name, value in self.observed_timings_ms))
        if any(value < 0 for _name, value in timings):
            raise ValueError("observed timings must not be negative")
        object.__setattr__(self, "observed_timings_ms", timings)
        if self.release_gate != RELEASE_GATE_BLOCKED:
            raise ValueError("controlled evidence cannot claim the formal release gate")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["outcome_counts"] = dict(self.outcome_counts)
        payload["request_counts"] = dict(self.request_counts)
        payload["observed_timings_ms"] = dict(self.observed_timings_ms)
        _assert_secret_free(payload)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))

    def write(self, path: Path) -> Path:
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path


def _counts(values: tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    normalized = tuple(sorted((str(name), int(count)) for name, count in values))
    if any(not name or count < 0 for name, count in normalized):
        raise ValueError("evidence counts require names and non-negative values")
    if len({name for name, _count in normalized}) != len(normalized):
        raise ValueError("evidence count names must be unique")
    return normalized


def _assert_secret_free(value: Any, *, path: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in _SECRET_KEYS:
                raise ValueError(f"secret field is forbidden in evidence: {path}.{key}")
            _assert_secret_free(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_secret_free(child, path=f"{path}[{index}]")


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "RELEASE_GATE_BLOCKED",
    "TerminologySyncEvidenceManifest",
]
