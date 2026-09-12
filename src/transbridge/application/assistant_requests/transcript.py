"""Immutable transcript records and committed attachment references."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Any


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    path: str
    digest: str
    size_bytes: int
    count: int = 0

    def __post_init__(self) -> None:
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or "\\" in self.path
            or ":" in self.path
            or any(part in {".", ".."} for part in self.path.split("/"))
            or str(path) != self.path
        ):
            raise ValueError("attachment path must be a canonical relative reference")
        if not re.fullmatch(r"[a-f0-9]{64}", self.digest):
            raise ValueError("attachment requires a SHA-256 digest")
        if self.size_bytes < 0 or self.count < 0:
            raise ValueError("attachment size and count must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "digest": self.digest, "size_bytes": self.size_bytes, "count": self.count}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> AttachmentRef:
        return cls(str(value["path"]), str(value["digest"]), int(value["size_bytes"]), int(value.get("count", 0)))


@dataclass(frozen=True, slots=True)
class TranscriptMessage:
    message_id: str
    sequence: int
    role: str
    content: Any
    request_id: str | None = None
    request_revision: int | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[Any, ...] = ()
    origin: str = "user"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.message_id or self.sequence < 1:
            raise ValueError("transcript message needs an identity and positive sequence")
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError("unsupported transcript role")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("native tool result requires its call identity")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("native tool calls must belong to an assistant message")
        if self.request_revision is not None and (not self.request_id or self.request_revision < 0):
            raise ValueError("request revision requires a request identity and nonnegative revision")
        object.__setattr__(self, "content", _freeze(self.content))
        object.__setattr__(self, "tool_calls", _freeze(self.tool_calls))
        object.__setattr__(self, "metadata", _freeze(self.metadata))
        json.dumps(self.to_dict(), allow_nan=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sequence": self.sequence,
            "role": self.role,
            "content": _thaw(self.content),
            "request_id": self.request_id,
            "request_revision": self.request_revision,
            "tool_call_id": self.tool_call_id,
            "tool_calls": _thaw(self.tool_calls),
            "origin": self.origin,
            "metadata": _thaw(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TranscriptMessage:
        return cls(
            message_id=str(value["message_id"]),
            sequence=int(value["sequence"]),
            role=str(value["role"]),
            content=value.get("content"),
            request_id=value.get("request_id"),
            request_revision=value.get("request_revision"),
            tool_call_id=value.get("tool_call_id"),
            tool_calls=tuple(value.get("tool_calls", ())),
            origin=str(value.get("origin", "legacy")),
            metadata=value.get("metadata", {}),
        )


@dataclass(frozen=True, slots=True)
class TranscriptManifest:
    segments: tuple[AttachmentRef, ...] = ()
    last_sequence: int = 0
    input_watermark: int = 0
    artifacts: tuple[AttachmentRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "segments", tuple(self.segments))
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        if not 0 <= self.input_watermark <= self.last_sequence:
            raise ValueError("transcript input watermark exceeds its committed sequence")
        if sum(item.count for item in self.segments) != self.last_sequence:
            raise ValueError("transcript manifest count does not match its sequence")
        if len({item.path for item in self.segments}) != len(self.segments):
            raise ValueError("transcript manifest contains duplicate segments")
        if len({item.path for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("transcript manifest contains duplicate artifacts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [item.to_dict() for item in self.segments],
            "last_sequence": self.last_sequence,
            "input_watermark": self.input_watermark,
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TranscriptManifest:
        return cls(
            tuple(AttachmentRef.from_dict(item) for item in value.get("segments", ())),
            int(value.get("last_sequence", 0)),
            int(value.get("input_watermark", 0)),
            tuple(AttachmentRef.from_dict(item) for item in value.get("artifacts", ())),
        )


def validate_message_order(messages: tuple[TranscriptMessage, ...]) -> None:
    """Validate causal tool links, allowing an unfinished final call group."""
    seen: set[str] = set()
    calls: set[str] = set()
    pending: set[str] = set()
    for sequence, message in enumerate(messages, 1):
        if message.sequence != sequence or message.message_id in seen:
            raise ValueError("transcript message sequence or identity is inconsistent")
        seen.add(message.message_id)
        if message.role == "tool":
            if message.tool_call_id not in pending:
                raise ValueError("transcript contains an orphan or duplicate native tool result")
            pending.remove(message.tool_call_id)
        # Ingress can arrive while tools are running. Records are factual history;
        # the context assembler is responsible for selecting complete protocol groups.
        for call in message.tool_calls:
            call_id = call.get("id") if isinstance(call, Mapping) else None
            if not isinstance(call_id, str) or not call_id or call_id in calls:
                raise ValueError("transcript native tool call identity is missing or duplicated")
            calls.add(call_id)
            pending.add(call_id)
