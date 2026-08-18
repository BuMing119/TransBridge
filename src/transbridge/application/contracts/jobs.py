"""Minimal deferred-operation identity contracts.

The task runtime adds snapshots and controls later; S02 only freezes the value
returned by application use cases that choose background execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class JobRef:
    job_id: str
    owner_id: str
    run_id: str | None = None

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        if not self.job_id or not self.job_id.strip():
            raise ValueError("job_id must not be empty")
        if not self.owner_id or not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRef:
        version = int(data.get("schema_version", 1))
        if version > cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported JobRef schema version: {version}")
        return cls(
            job_id=str(data["job_id"]),
            owner_id=str(data["owner_id"]),
            run_id=None if data.get("run_id") is None else str(data["run_id"]),
        )


@dataclass(frozen=True, slots=True)
class Deferred[TRef]:
    """Explicit marker for a result that will complete through a task runtime."""

    ref: TRef

    SCHEMA_VERSION = 1

    def to_dict(self) -> dict[str, Any]:
        encoder = getattr(self.ref, "to_dict", None)
        if encoder is None:
            raise TypeError("Deferred ref must provide to_dict()")
        return {
            "schema_version": self.SCHEMA_VERSION,
            "kind": "deferred",
            "ref": encoder(),
        }

    @classmethod
    def job_from_dict(cls, data: dict[str, Any]) -> Deferred[JobRef]:
        version = int(data.get("schema_version", 1))
        if version > cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported Deferred schema version: {version}")
        if data.get("kind", "deferred") != "deferred":
            raise ValueError("Invalid Deferred kind")
        return cls(JobRef.from_dict(data["ref"]))
