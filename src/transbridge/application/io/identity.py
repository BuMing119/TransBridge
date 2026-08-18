"""Stable translation entry identity and provenance contracts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import Any

_WINDOWS_ABSOLUTE = re.compile(r"(?:^|:)[A-Za-z]:[\\/]")
_WINDOWS_DRIVE = re.compile(r"(?:^|:)[A-Za-z]:(?:$|[:\\/])")


@dataclass(frozen=True, order=True, slots=True)
class SourceNamespace:
    """Stable source identity that intentionally excludes absolute paths."""

    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not value:
            raise ValueError("source namespace must not be empty")
        lowered = value.lower()
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or ":/" in value
            or _WINDOWS_ABSOLUTE.search(value)
            or _WINDOWS_DRIVE.search(value)
            or "file://" in lowered
        ):
            raise ValueError("source namespace must not contain an absolute path")
        object.__setattr__(self, "value", value)

    @classmethod
    def legacy(cls) -> SourceNamespace:
        return cls("legacy:v1")

    @classmethod
    def from_fingerprint(cls, source_kind: str, sha256: str, *, scope: str | None = None) -> SourceNamespace:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("source fingerprint must be a lowercase SHA-256 digest")
        kind = _stable_token(source_kind, "source kind").lower()
        scope_part = f":{_stable_token(scope, 'source scope')}" if scope is not None else ""
        return cls(f"source:{kind}{scope_part}:sha256:{sha256}")


def _stable_token(value: str, label: str) -> str:
    token = value.strip()
    if not token or not re.fullmatch(r"[A-Za-z0-9_.:-]+", token):
        raise ValueError(f"{label} must be a stable non-path token")
    if _WINDOWS_ABSOLUTE.search(token) or _WINDOWS_DRIVE.search(token) or "file:" in token.lower():
        raise ValueError(f"{label} must not contain an absolute path")
    return token


@dataclass(frozen=True, order=True, slots=True)
class EntryKey:
    namespace: SourceNamespace
    local_key: str

    def __post_init__(self) -> None:
        if not self.local_key or not self.local_key.strip():
            raise ValueError("entry local key must not be empty")

    def serialize(self) -> str:
        return json.dumps([self.namespace.value, self.local_key], ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def deserialize(cls, value: str) -> EntryKey:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid serialized EntryKey") from exc
        if not isinstance(payload, list) or len(payload) != 2 or not all(isinstance(item, str) for item in payload):
            raise ValueError("invalid serialized EntryKey")
        return cls(SourceNamespace(payload[0]), payload[1])

    def to_dict(self) -> dict[str, str]:
        return {"namespace": self.namespace.value, "local_key": self.local_key}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntryKey:
        return cls(SourceNamespace(str(data["namespace"])), str(data["local_key"]))


@dataclass(frozen=True, slots=True)
class ExternalEntryRef:
    system: str
    scope: str
    opaque_id: str | int | float | bool | None
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.system or not self.system.strip():
            raise ValueError("external reference system must not be empty")
        if not self.scope or not self.scope.strip():
            raise ValueError("external reference scope must not be empty")
        if not isinstance(self.opaque_id, (str, int, float, bool, type(None))):
            raise TypeError("external opaque id must be a JSON scalar")
        if isinstance(self.opaque_id, float) and not math.isfinite(self.opaque_id):
            raise ValueError("external opaque id must be a finite JSON number")

    @property
    def provider(self) -> str:
        return self.system

    @property
    def index_key(self) -> tuple[str, str, str, str | int | float | bool | None]:
        return (self.system, self.scope, type(self.opaque_id).__name__, self.opaque_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "scope": self.scope,
            "opaque_id": self.opaque_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalEntryRef:
        metadata = data.get("metadata") or {}
        return cls(
            system=str(data.get("system", data.get("provider", ""))),
            scope=str(data["scope"]),
            opaque_id=data["opaque_id"],
            metadata=tuple(sorted(metadata.items())),
        )


@dataclass(frozen=True, order=True, slots=True)
class EntryRevision:
    value: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            raise ValueError("entry revision must be a non-negative integer")

    def next(self) -> EntryRevision:
        return EntryRevision(self.value + 1)


@dataclass(frozen=True, slots=True)
class Provenance:
    run_id: str
    actor: str
    source: str
    recorded_at: str | None = None
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.run_id or not self.run_id.strip():
            raise ValueError("provenance run_id must not be empty")
        if not self.actor or not self.actor.strip():
            raise ValueError("provenance actor must not be empty")
        if not self.source or not self.source.strip():
            raise ValueError("provenance source must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "actor": self.actor,
            "source": self.source,
            "recorded_at": self.recorded_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        metadata = data.get("metadata") or {}
        return cls(
            run_id=str(data["run_id"]),
            actor=str(data["actor"]),
            source=str(data["source"]),
            recorded_at=None if data.get("recorded_at") is None else str(data["recorded_at"]),
            metadata=tuple(sorted(metadata.items())),
        )
