"""Project-local task identities and explicit continuation metadata, without credentials."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
from threading import RLock
import uuid

from transbridge.application.io.identity import EntryKey

from .task_history import atomic_json

logger = logging.getLogger(__name__)


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
    strict = record.get("recovery_contract") == 2
    if "recovery_contract" in record and not strict:
        raise ValueError("unsupported task recovery contract")
    if strict:
        runs = record.get("execution_runs")
        if (
            type(record.get("overwrite")) is not bool
            or not isinstance(runs, list)
            or not runs
            or any(not isinstance(run, str) or not run.strip() for run in runs)
            or len(runs) != len(set(runs))
        ):
            raise ValueError("task recovery execution chain is invalid")
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("task recovery source must be an object")
        key = _nonempty(source.get("key"), "source key")
        _nonempty(source.get("label"), "source label")
        if key in seen_sources:
            raise ValueError("task recovery source keys must be unique")
        seen_sources.add(key)
        source_entries = set()
        for field in ("translate_keys", "polish_keys"):
            if not isinstance(source.get(field), list):
                raise ValueError(f"task recovery {field} must be a list")
            for value in source[field]:
                if not isinstance(value, dict):
                    raise ValueError("task recovery entry identity must be an object")
                _nonempty(value.get("namespace"), "entry namespace")
                _nonempty(value.get("local_key"), "entry local_key")
                if value.get("original") is not None and not isinstance(value["original"], str):
                    raise ValueError("task recovery entry original must be a string")
                identity = EntryKey.from_dict(value)
                if identity in seen_entries:
                    raise ValueError("task recovery entry identities must be unique")
                seen_entries.add(identity)
                source_entries.add(identity)
        if strict:
            inputs = source.get("inputs")
            if not isinstance(inputs, list):
                raise ValueError("task recovery source inputs must be a list")
            input_keys = set()
            for item in inputs:
                if not isinstance(item, dict):
                    raise ValueError("task recovery input must be an object")
                try:
                    identity = EntryKey.from_dict(item["entry_key"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("task recovery input identity is invalid") from exc
                if identity in input_keys:
                    raise ValueError("task recovery inputs must be unique")
                input_keys.add(identity)
                if (
                    any(not isinstance(item.get(field), str) for field in ("original", "translation", "context"))
                    or type(item.get("stage")) is not int
                    or type(item.get("revision")) is not int
                    or item["revision"] < 0
                ):
                    raise ValueError("task recovery input content is invalid")
            if input_keys != source_entries:
                raise ValueError("task recovery inputs do not match source scope")
    if "checkpoint_settings" in record and not isinstance(record["checkpoint_settings"], dict):
        raise ValueError("task recovery checkpoint_settings must be an object")
    applied = record.get("applied_entries", [])
    if not isinstance(applied, list):
        raise ValueError("task recovery applied_entries must be a list")
    seen_applied = set()
    for row in applied:
        if not isinstance(row, dict) or not isinstance(row.get("entry_key"), dict):
            raise ValueError("task recovery applied entry must include an entry identity")
        try:
            key = EntryKey.from_dict(row["entry_key"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("task recovery applied entry identity is invalid") from exc
        if key not in seen_entries or key in seen_applied:
            raise ValueError("task recovery applied entries must be unique and belong to its scope")
        seen_applied.add(key)
        if (
            not all(isinstance(row.get(field), str) for field in ("original", "text"))
            or type(row.get("stage")) is not int
        ):
            raise ValueError("task recovery applied entry result is invalid")
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
        self._lease = None

    def acquire(self, task_id: str):
        """Hold execution ownership until the run and all persistence workers stop."""
        from .task_recovery_lease import TaskRecoveryLease

        _nonempty(task_id, "task_id")
        if self._lease is not None:
            if self._lease.task_id != task_id:
                raise RuntimeError("恢复记录已属于其他执行任务")
            return
        self._lease = TaskRecoveryLease(self.root, task_id)

    def release(self):
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def save_results(self, task_id, rows):
        from .task_recovery_results import TaskRecoveryResults

        with self._lock:
            TaskRecoveryResults(self.root).merge(task_id, rows)

    def load_results(self, task_id):
        from .task_recovery_results import TaskRecoveryResults

        with self._lock:
            return TaskRecoveryResults(self.root).load(task_id)

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
                logger.warning("Skipping unreadable task recovery %s; original retained: %s", path, exc, exc_info=True)
                continue
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
