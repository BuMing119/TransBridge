"""Atomic, versioned checkpoints for the post-process candidate chain.

A checkpoint records the run identity, the stable EntryKey scope, the last
completed stage phase and the per-entry candidate hash.  The candidate text is
stored as well so a resume can continue from the exact value a stage produced
without replaying non-deterministic LLM calls; the hash guards integrity.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Protocol

from transbridge.application.io import EntryKey, EntryRevision

from .workload_models import canonical_hash


@dataclass(frozen=True, slots=True)
class PostProcessCheckpointEntry:
    """One resumed post-process candidate inside a checkpoint."""

    entry_key: dict[str, str]
    stage: int
    phase: str
    text: str
    candidate_sha256: str
    accepted: bool
    original: str = ""
    before_text: str = ""
    before_revision: int = 0
    phases: tuple[str, ...] = ()
    context: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.entry_key, dict) or not self.entry_key:
            raise ValueError("post-process checkpoint entry key must be an object")
        if isinstance(self.stage, bool) or not isinstance(self.stage, int) or self.stage < 0:
            raise ValueError("post-process checkpoint stage must be a non-negative integer")
        if not self.phase.strip() or not self.text:
            raise ValueError("post-process checkpoint phase and text must not be empty")
        if not _is_sha256(self.candidate_sha256):
            raise ValueError("post-process checkpoint candidate hash must be a SHA-256 digest")
        if isinstance(self.before_revision, bool) or not isinstance(self.before_revision, int):
            raise TypeError("post-process checkpoint revision must be an integer")

    def candidate_hash(self) -> str:
        return canonical_hash({"entry_key": self.entry_key, "phase": self.phase, "text": self.text})


@dataclass(frozen=True, slots=True)
class PostProcessCheckpoint:
    """Identity-bound, revision-monotonic post-process progress."""

    run_id: str
    owner_id: str
    input_fingerprint: str
    revision: int = 0
    completed_phases: tuple[str, ...] = ()
    entries: tuple[PostProcessCheckpointEntry, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported post-process checkpoint schema version")
        identity = (self.run_id, self.owner_id, self.input_fingerprint)
        if any(not value.strip() for value in identity):
            raise ValueError("post-process checkpoint identity fields must not be empty")
        if not _is_sha256(self.input_fingerprint):
            raise ValueError("post-process checkpoint input fingerprint must be a SHA-256 digest")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("post-process checkpoint revision must be non-negative")
        keys = tuple(
            (entry.entry_key.get("namespace", ""), entry.entry_key.get("local_key", ""))
            for entry in self.entries
        )
        if len(keys) != len(set(keys)):
            raise ValueError("post-process checkpoint entries must have unique EntryKeys")

    def validate(self, *, owner_id: str, input_fingerprint: str) -> None:
        if self.owner_id != owner_id or self.input_fingerprint != input_fingerprint:
            raise ValueError("post-process checkpoint identity does not match this workload")

    def advance(
        self,
        *,
        phase: str,
        entries: tuple[PostProcessCheckpointEntry, ...],
    ) -> PostProcessCheckpoint:
        if phase in self.completed_phases:
            return replace(
                self,
                revision=self.revision + 1,
                entries=entries,
            )
        return replace(
            self,
            revision=self.revision + 1,
            completed_phases=(*self.completed_phases, phase),
            entries=entries,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "owner_id": self.owner_id,
            "input_fingerprint": self.input_fingerprint,
            "revision": self.revision,
            "completed_phases": list(self.completed_phases),
            "entries": [
                {
                    "entry_key": entry.entry_key,
                    "stage": entry.stage,
                    "phase": entry.phase,
                    "text": entry.text,
                    "candidate_sha256": entry.candidate_sha256,
                    "accepted": entry.accepted,
                    "original": entry.original,
                    "before_text": entry.before_text,
                    "before_revision": entry.before_revision,
                    "phases": list(entry.phases),
                    "context": entry.context,
                }
                for entry in self.entries
            ],
        }

    @classmethod
    def from_dict(cls, value: Any) -> PostProcessCheckpoint:
        if not isinstance(value, dict):
            raise ValueError("post-process checkpoint root must be an object")
        version = value.get("schema_version")
        revision = value.get("revision")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (version, revision)):
            raise TypeError("post-process checkpoint schema and revision must be integers")
        if version != 1:
            raise ValueError("unsupported post-process checkpoint schema version")
        entries = tuple(
            PostProcessCheckpointEntry(
                entry_key=_object(item.get("entry_key"), "entry_key"),
                stage=_int(item.get("stage"), "stage"),
                phase=_string(item.get("phase"), "phase"),
                text=_string(item.get("text"), "text"),
                candidate_sha256=_string(item.get("candidate_sha256"), "candidate_sha256"),
                accepted=item.get("accepted", True),
                original=_string(item.get("original", ""), "original", allow_empty=True),
                before_text=_string(item.get("before_text", ""), "before_text", allow_empty=True),
                before_revision=_int(item.get("before_revision", 0), "before_revision"),
                phases=tuple(_strings(item.get("phases", ()))),
                context=_string(item.get("context", ""), "context", allow_empty=True),
            )
            for item in _objects(value.get("entries"))
        )
        return cls(
            schema_version=version,
            run_id=_string(value.get("run_id"), "run_id"),
            owner_id=_string(value.get("owner_id"), "owner_id"),
            input_fingerprint=_string(value.get("input_fingerprint"), "input_fingerprint"),
            revision=revision,
            completed_phases=tuple(_strings(value.get("completed_phases"))),
            entries=entries,
        )


class PostProcessCheckpointPort(Protocol):
    def load(self, run_id: str) -> PostProcessCheckpoint | None: ...

    def save(self, checkpoint: PostProcessCheckpoint) -> None: ...


class InMemoryPostProcessCheckpointPort:
    def __init__(self) -> None:
        self._records: dict[str, PostProcessCheckpoint] = {}
        self._lock = threading.Lock()

    def load(self, run_id: str) -> PostProcessCheckpoint | None:
        with self._lock:
            return self._records.get(run_id)

    def save(self, checkpoint: PostProcessCheckpoint) -> None:
        with self._lock:
            current = self._records.get(checkpoint.run_id)
            if current is not None and checkpoint.revision <= current.revision:
                raise ValueError("post-process checkpoint revision did not advance")
            self._records[checkpoint.run_id] = checkpoint


class FilesystemPostProcessCheckpointPort:
    """Atomic same-directory checkpoint storage; legacy unversioned files rejected."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def load(self, run_id: str) -> PostProcessCheckpoint | None:
        target = self._path(run_id)
        if not target.exists():
            return None
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("post-process checkpoint is corrupt") from exc
        checkpoint = PostProcessCheckpoint.from_dict(value)
        if checkpoint.run_id != run_id:
            raise ValueError("post-process checkpoint run_id mismatch")
        return checkpoint

    def save(self, checkpoint: PostProcessCheckpoint) -> None:
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            current = self.load(checkpoint.run_id)
            if current is not None and checkpoint.revision <= current.revision:
                raise ValueError("post-process checkpoint revision did not advance")
            target = self._path(checkpoint.run_id)
            temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
            payload = json.dumps(
                checkpoint.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    descriptor = -1
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                PostProcessCheckpoint.from_dict(json.loads(temporary.read_text(encoding="utf-8")))
                os.replace(temporary, target)
                _fsync_directory(self._root)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)

    def _path(self, run_id: str) -> Path:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._root / f"postprocess-{digest}.json"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _string(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value.strip() and not allow_empty):
        raise ValueError(f"post-process checkpoint {name} must be a non-empty string")
    return value


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError("post-process checkpoint phase lists must contain strings")
    if len(value) != len(set(value)):
        raise ValueError("post-process checkpoint phases must be unique")
    return tuple(value)


def _objects(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TypeError("post-process checkpoint entries must contain objects")
    return tuple(value)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"post-process checkpoint {name} must be a non-empty object")
    return value


def _int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"post-process checkpoint {name} must be an integer")
    return value


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def checkpoint_entry_from_candidate(candidate: Any, *, phase: str, accepted: bool) -> PostProcessCheckpointEntry:
    """Build a checkpoint entry from a PostProcessCandidate value object."""
    text = candidate.text
    entry_key = candidate.entry_key.to_dict()
    return PostProcessCheckpointEntry(
        entry_key=entry_key,
        stage=candidate.stage,
        phase=phase,
        text=text,
        candidate_sha256=canonical_hash({"entry_key": entry_key, "phase": phase, "text": text}),
        accepted=accepted,
        original=candidate.original,
        before_text=candidate.before_text,
        before_revision=candidate.before_revision.value,
        phases=tuple(candidate.phases),
        context=candidate.context,
    )


def restore_candidates_from_checkpoint(checkpoint: PostProcessCheckpoint) -> tuple[Any, ...]:
    """Rebuild candidate values from a checkpoint without replaying LLM calls."""
    from .postprocess import PostProcessCandidate

    restored = []
    for entry in checkpoint.entries:
        if not entry.accepted:
            continue
        restored.append(
            PostProcessCandidate(
                run_id=checkpoint.run_id,
                entry_key=EntryKey.from_dict(entry.entry_key),
                before_revision=EntryRevision(entry.before_revision),
                original=entry.original,
                before_text=entry.before_text,
                text=entry.text,
                stage=entry.stage,
                phases=entry.phases,
                accepted=entry.accepted,
                context=entry.context,
            )
        )
    return tuple(restored)
