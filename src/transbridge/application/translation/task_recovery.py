"""Project-local task identities and explicit continuation metadata, without credentials."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
import uuid

from transbridge.application.io.identity import EntryKey

from .task_history import atomic_json


def _nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task recovery {name} must be a non-empty string")
    return value


def _validate_fields(value):
    """Reject credential-shaped fields at any depth before writing extensible metadata."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("task recovery field names must be strings")
            normalized = key.lower().replace("-", "_")
            if any(
                part in normalized for part in ("password", "secret", "credential", "api_key", "apikey")
            ) or normalized in {"token", "access_token", "refresh_token", "authorization", "cookie", "headers"}:
                raise ValueError(f"task recovery must not store credential field {key!r}")
            _validate_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_fields(child)


def _validate(record):
    if not isinstance(record, dict):
        raise ValueError("task recovery record must be an object")
    _validate_fields(record)
    for field in ("task_id", "project_id", "variant_id", "mode", "config_digest", "state", "created_at"):
        _nonempty(record.get(field), field)
    try:
        created = datetime.fromisoformat(record["created_at"])
    except ValueError as exc:
        raise ValueError("task recovery created_at must be an ISO timestamp") from exc
    if created.tzinfo is None:
        raise ValueError("task recovery created_at must include a timezone")
    sources = record.get("sources")
    if not isinstance(sources, list):
        raise ValueError("task recovery sources must be a list")
    seen_sources, seen_entries = set(), set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("task recovery source must be an object")
        key = _nonempty(source.get("key"), "source key")
        _nonempty(source.get("label"), "source label")
        if key in seen_sources:
            raise ValueError("task recovery source keys must be unique")
        seen_sources.add(key)
        for field in ("translate_keys", "polish_keys"):
            if not isinstance(source.get(field), list):
                raise ValueError(f"task recovery {field} must be a list")
            for value in source[field]:
                if not isinstance(value, dict):
                    raise ValueError("task recovery entry identity must be an object")
                _nonempty(value.get("namespace"), "entry namespace")
                _nonempty(value.get("local_key"), "entry local_key")
                identity = EntryKey.from_dict(value)
                if identity in seen_entries:
                    raise ValueError("task recovery entry identities must be unique")
                seen_entries.add(identity)
    # Normalize to detached JSON values and reject non-JSON objects/non-finite numbers.
    try:
        return json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("task recovery fields must contain valid JSON values") from exc


class TaskRecoveryStore:
    """Atomic metadata per task; creating a new task never overwrites an old task."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = RLock()

    def _path(self, task_id):
        _nonempty(task_id, "task_id")
        return self.root / f"{hashlib.sha256(task_id.encode('utf-8')).hexdigest()}.json"

    def create(self, record: dict) -> dict:
        record = _validate(record)
        with self._lock:
            path = self._path(record["task_id"])
            if path.exists():
                raise FileExistsError(f"Task recovery record already exists: {record['task_id']}")
            temporary = path.parent / f".create-{uuid.uuid4().hex}.tmp"
            try:
                atomic_json(temporary, record)
                # Publish without replacing another process's record of the same task.
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return record

    def load(self, task_id: str) -> dict:
        path = self._path(task_id)
        try:
            record = _validate(json.loads(path.read_text(encoding="utf-8")))
            if record["task_id"] != task_id:
                raise ValueError("task identity does not match filename")
            return record
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read task recovery {path}: {exc}") from exc

    def list_records(self) -> tuple[dict, ...]:
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = _validate(json.loads(path.read_text(encoding="utf-8")))
                if self._path(record["task_id"]) != path:
                    raise ValueError("task identity does not match filename")
            except (OSError, ValueError) as exc:
                raise ValueError(f"Cannot read task recovery {path}: {exc}") from exc
            records.append(record)
        return tuple(sorted(records, key=lambda item: item["created_at"], reverse=True))

    def update(self, task_id: str, **fields) -> dict:
        with self._lock:
            record = self.load(task_id)
            record.update(fields)
            record["updated_at"] = datetime.now(UTC).isoformat()
            record = _validate(record)
            atomic_json(self._path(task_id), record)
            return record
