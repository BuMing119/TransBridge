"""Immutable read-model contracts for GUI and other entrypoint projections."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from transbridge.application.contracts import Diagnostic


@dataclass(frozen=True, slots=True)
class DirtyState:
    aggregate_revision: int
    persisted_revision: int

    def __post_init__(self) -> None:
        if min(self.aggregate_revision, self.persisted_revision) < 0:
            raise ValueError("projection revisions must not be negative")
        if self.persisted_revision > self.aggregate_revision:
            raise ValueError("persisted revision cannot exceed aggregate revision")

    @property
    def dirty(self) -> bool:
        return self.aggregate_revision != self.persisted_revision


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    stream_id: str
    revision: int
    persisted_revision: int
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.stream_id.strip():
            raise ValueError("projection stream_id must not be empty")
        DirtyState(self.revision, self.persisted_revision)
        object.__setattr__(self, "values", _freeze_mapping(self.values))

    @property
    def dirty(self) -> bool:
        return self.revision != self.persisted_revision

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "revision": self.revision,
            "persisted_revision": self.persisted_revision,
            "dirty": self.dirty,
            "values": _thaw_mapping(self.values),
        }


@dataclass(frozen=True, slots=True)
class ProjectionEvent:
    stream_id: str
    revision: int
    persisted_revision: int
    values: Mapping[str, Any]
    event_id: str

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("projection event_id must not be empty")
        ProjectionSnapshot(self.stream_id, self.revision, self.persisted_revision, self.values)

    def snapshot(self) -> ProjectionSnapshot:
        return ProjectionSnapshot(
            self.stream_id,
            self.revision,
            self.persisted_revision,
            self.values,
        )


@dataclass(frozen=True, slots=True)
class ProjectionDecision:
    applied: bool
    snapshot: ProjectionSnapshot | None
    diagnostics: tuple[Diagnostic, ...] = ()


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    copied = deepcopy(dict(values))
    try:
        json.dumps(copied, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("projection values must contain finite JSON values") from exc
    return _FrozenMapping(tuple(sorted((str(key), _freeze_value(value)) for key, value in copied.items())))


@dataclass(frozen=True, slots=True)
class _FrozenMapping(Mapping[str, Any]):
    pairs: tuple[tuple[str, Any], ...]

    def __getitem__(self, key: str) -> Any:
        for candidate, value in self.pairs:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _ in self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)


@dataclass(frozen=True, slots=True)
class _FrozenArray:
    values: tuple[Any, ...]


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return _FrozenArray(tuple(_freeze_value(item) for item in value))
    return deepcopy(value)


def _thaw_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _thaw_value(value) for key, value in values.items()}


def _thaw_value(value: Any) -> Any:
    if isinstance(value, _FrozenMapping):
        return _thaw_mapping(value)
    if isinstance(value, _FrozenArray):
        return [_thaw_value(item) for item in value.values]
    return deepcopy(value)


__all__ = [
    "DirtyState",
    "ProjectionDecision",
    "ProjectionEvent",
    "ProjectionSnapshot",
]
