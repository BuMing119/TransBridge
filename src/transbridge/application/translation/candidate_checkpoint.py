"""Atomic, versioned checkpoints for accepted translation candidates."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Any, Protocol

from .workload_models import CandidateTranslation, TranslationBatchOutcome


@dataclass(frozen=True, slots=True)
class TranslationCheckpoint:
    run_id: str
    owner_id: str
    spec_fingerprint: str
    input_fingerprint: str
    revision: int = 0
    completed_batch_ids: frozenset[str] = frozenset()
    committed_candidate_ids: frozenset[str] = frozenset()
    candidates: tuple[CandidateTranslation, ...] = ()
    outcomes: tuple[TranslationBatchOutcome, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported translation checkpoint schema version")
        identity = (
            self.run_id,
            self.owner_id,
            self.spec_fingerprint,
            self.input_fingerprint,
        )
        if any(not value.strip() for value in identity):
            raise ValueError("translation checkpoint identity fields must not be empty")
        if not _is_sha256(self.spec_fingerprint) or not _is_sha256(self.input_fingerprint):
            raise ValueError("translation checkpoint fingerprints must be SHA-256 digests")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("translation checkpoint revision must be non-negative")
        if len(self.completed_batch_ids) != len(set(self.completed_batch_ids)):
            raise ValueError("translation checkpoint completed batches must be unique")
        keys = tuple(candidate.entry_key for candidate in self.candidates)
        if len(keys) != len(set(keys)):
            raise ValueError("translation checkpoint candidates must have unique EntryKeys")
        if any(candidate.run_id != self.run_id for candidate in self.candidates):
            raise ValueError("translation checkpoint candidate run_id mismatch")

    def validate(self, *, owner_id: str, spec_fingerprint: str, input_fingerprint: str) -> None:
        if (
            self.owner_id != owner_id
            or self.spec_fingerprint != spec_fingerprint
            or self.input_fingerprint != input_fingerprint
        ):
            raise ValueError("translation checkpoint identity does not match this workload")

    def accept_batch(
        self,
        outcome: TranslationBatchOutcome,
        candidates: tuple[CandidateTranslation, ...],
    ) -> TranslationCheckpoint:
        if outcome.batch_id in self.completed_batch_ids:
            return self
        by_key = {candidate.entry_key: candidate for candidate in self.candidates}
        by_key.update((candidate.entry_key, candidate) for candidate in candidates)
        return replace(
            self,
            revision=self.revision + 1,
            completed_batch_ids=self.completed_batch_ids | {outcome.batch_id},
            candidates=tuple(sorted(by_key.values(), key=lambda candidate: candidate.entry_key)),
            outcomes=(*self.outcomes, outcome),
        )

    def mark_committed(self, candidate_id: str) -> TranslationCheckpoint:
        if candidate_id in self.committed_candidate_ids:
            return self
        return replace(
            self,
            revision=self.revision + 1,
            committed_candidate_ids=self.committed_candidate_ids | {candidate_id},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "owner_id": self.owner_id,
            "spec_fingerprint": self.spec_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "revision": self.revision,
            "completed_batch_ids": sorted(self.completed_batch_ids),
            "committed_candidate_ids": sorted(self.committed_candidate_ids),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }

    @classmethod
    def from_dict(cls, value: Any) -> TranslationCheckpoint:
        if not isinstance(value, dict):
            raise ValueError("translation checkpoint root must be an object")
        version = value.get("schema_version")
        revision = value.get("revision")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (version, revision)):
            raise TypeError("translation checkpoint schema and revision must be integers")
        if version != 1:
            raise ValueError("unsupported translation checkpoint schema version")
        return cls(
            schema_version=version,
            run_id=_string(value.get("run_id"), "run_id"),
            owner_id=_string(value.get("owner_id"), "owner_id"),
            spec_fingerprint=_string(value.get("spec_fingerprint"), "spec_fingerprint"),
            input_fingerprint=_string(value.get("input_fingerprint"), "input_fingerprint"),
            revision=revision,
            completed_batch_ids=frozenset(_strings(value.get("completed_batch_ids"), "completed_batch_ids")),
            committed_candidate_ids=frozenset(
                _strings(value.get("committed_candidate_ids"), "committed_candidate_ids")
            ),
            candidates=tuple(CandidateTranslation.from_dict(item) for item in _objects(value.get("candidates"))),
            outcomes=tuple(TranslationBatchOutcome.from_dict(item) for item in _objects(value.get("outcomes"))),
        )


class TranslationCheckpointPort(Protocol):
    def load(self, run_id: str) -> TranslationCheckpoint | None: ...

    def save(self, checkpoint: TranslationCheckpoint) -> None: ...


class InMemoryTranslationCheckpointPort:
    def __init__(self) -> None:
        self._records: dict[str, TranslationCheckpoint] = {}
        self._lock = threading.Lock()

    def load(self, run_id: str) -> TranslationCheckpoint | None:
        with self._lock:
            return self._records.get(run_id)

    def save(self, checkpoint: TranslationCheckpoint) -> None:
        with self._lock:
            current = self._records.get(checkpoint.run_id)
            if current is not None and checkpoint.revision <= current.revision:
                raise ValueError("translation checkpoint revision did not advance")
            self._records[checkpoint.run_id] = checkpoint


class FilesystemTranslationCheckpointPort:
    """Atomic same-directory checkpoint storage; legacy unversioned files are rejected."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()

    def load(self, run_id: str) -> TranslationCheckpoint | None:
        target = self._path(run_id)
        if not target.exists():
            return None
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("translation checkpoint is corrupt") from exc
        checkpoint = TranslationCheckpoint.from_dict(value)
        if checkpoint.run_id != run_id:
            raise ValueError("translation checkpoint run_id mismatch")
        return checkpoint

    def save(self, checkpoint: TranslationCheckpoint) -> None:
        with self._lock:
            self._root.mkdir(parents=True, exist_ok=True)
            current = self.load(checkpoint.run_id)
            if current is not None and checkpoint.revision <= current.revision:
                raise ValueError("translation checkpoint revision did not advance")
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
                TranslationCheckpoint.from_dict(json.loads(temporary.read_text(encoding="utf-8")))
                os.replace(temporary, target)
                _fsync_directory(self._root)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)

    def _path(self, run_id: str) -> Path:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return self._root / f"translation-{digest}.json"


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"translation checkpoint {name} must be a non-empty string")
    return value


def _strings(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise TypeError(f"translation checkpoint {name} must be an array of strings")
    if len(value) != len(set(value)):
        raise ValueError(f"translation checkpoint {name} must be unique")
    return tuple(value)


def _objects(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TypeError("translation checkpoint collections must contain objects")
    return tuple(value)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
