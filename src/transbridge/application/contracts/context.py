"""Immutable request identity and authorization context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Scope attached to one application use-case invocation."""

    owner_id: str
    run_id: str | None = None
    project_id: str | None = None
    variant_id: str | None = None
    session_id: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    authorized_roots: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        if not self.owner_id or not self.owner_id.strip():
            raise ValueError("owner_id must not be empty")
        if any(not permission for permission in self.permissions):
            raise ValueError("permissions must not contain empty values")
        if any(not root for root in self.authorized_roots):
            raise ValueError("authorized_roots must not contain empty values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "owner_id": self.owner_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "variant_id": self.variant_id,
            "session_id": self.session_id,
            "permissions": sorted(self.permissions),
            "authorized_roots": list(self.authorized_roots),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestContext:
        version = int(data.get("schema_version", 1))
        if version > cls.SCHEMA_VERSION:
            raise ValueError(f"Unsupported RequestContext schema version: {version}")
        metadata = data.get("metadata") or {}
        return cls(
            owner_id=str(data["owner_id"]),
            run_id=_optional_str(data.get("run_id")),
            project_id=_optional_str(data.get("project_id")),
            variant_id=_optional_str(data.get("variant_id")),
            session_id=_optional_str(data.get("session_id")),
            permissions=frozenset(str(value) for value in data.get("permissions", ())),
            authorized_roots=tuple(str(value) for value in data.get("authorized_roots", ())),
            metadata=tuple(sorted((str(key), str(value)) for key, value in metadata.items())),
        )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
