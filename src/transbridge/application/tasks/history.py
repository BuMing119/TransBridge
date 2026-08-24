"""Bounded immutable terminal-task history and recording lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Protocol

from .activity import TaskArtifactRef, TaskOwnerScope
from .events import JobEvent, JobEventType, Subscription, TaskEventFilter
from .models import TERMINAL_STATES, JobState, OwnerRef

TASKS_MANAGE_PERMISSION = "tasks:manage"
TASK_HISTORY_SCHEMA_VERSION = 1


class TaskHistoryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskHistoryRecord:
    """Safe terminal summary; workload inputs, prompts, secrets and paths are absent."""

    run_id: str
    job_id: str
    owner: TaskOwnerScope
    job_type: str
    display_name: str
    state: JobState
    terminal_revision: int
    terminal_sequence: int
    created_at: datetime
    finished_at: datetime
    diagnostic_code: str | None = None
    artifact_refs: tuple[TaskArtifactRef, ...] = ()
    schema_version: int = TASK_HISTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("run_id", self.run_id),
            ("job_id", self.job_id),
            ("job_type", self.job_type),
            ("display_name", self.display_name),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.state not in TERMINAL_STATES:
            raise ValueError("task history accepts terminal states only")
        if self.terminal_revision < 0 or self.terminal_sequence < 0:
            raise ValueError("terminal revision and sequence must not be negative")
        if self.finished_at < self.created_at:
            raise ValueError("finished_at must not precede created_at")
        if self.schema_version != TASK_HISTORY_SCHEMA_VERSION:
            raise ValueError("TaskHistoryRecord can only create the current schema")

    @classmethod
    def from_terminal_event(
        cls,
        event: JobEvent,
        *,
        artifact_refs: tuple[TaskArtifactRef, ...] = (),
    ) -> TaskHistoryRecord:
        snapshot = event.snapshot
        if event.event_type is not JobEventType.FINISHED or not snapshot.is_terminal:
            raise ValueError("history records require a terminal FINISHED event")
        return cls(
            run_id=snapshot.ref.run_id or snapshot.ref.job_id,
            job_id=snapshot.ref.job_id,
            owner=TaskOwnerScope.from_owner(snapshot.owner),
            job_type=snapshot.specification.job_type,
            display_name=snapshot.specification.display_name.strip() or snapshot.specification.job_type,
            state=snapshot.state,
            terminal_revision=snapshot.revision,
            terminal_sequence=snapshot.sequence,
            created_at=snapshot.created_at,
            finished_at=event.occurred_at,
            diagnostic_code=event.code,
            artifact_refs=artifact_refs,
        )

    def visible_to(self, actor: OwnerRef) -> bool:
        if TASKS_MANAGE_PERMISSION in actor.permissions:
            return True
        return self.owner == TaskOwnerScope.from_owner(actor)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "job_id": self.job_id,
            "owner": asdict(self.owner),
            "job_type": self.job_type,
            "display_name": self.display_name,
            "state": self.state.value,
            "terminal_revision": self.terminal_revision,
            "terminal_sequence": self.terminal_sequence,
            "created_at": self.created_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "diagnostic_code": self.diagnostic_code,
            "artifact_refs": [asdict(reference) for reference in self.artifact_refs],
        }

    @classmethod
    def from_dict(cls, value: Any) -> TaskHistoryRecord:
        if not isinstance(value, dict):
            raise TaskHistoryError("history_invalid_record", "history record must be an object")
        try:
            version = int(value.get("schema_version", 0))
            if version != TASK_HISTORY_SCHEMA_VERSION:
                raise TaskHistoryError(
                    "history_schema_unsupported",
                    f"unsupported history schema version {version}",
                )
            owner_value = value["owner"]
            if not isinstance(owner_value, dict):
                raise TypeError("owner must be an object")
            artifacts_value = value.get("artifact_refs", [])
            if not isinstance(artifacts_value, list):
                raise TypeError("artifact_refs must be an array")
            return cls(
                run_id=str(value["run_id"]),
                job_id=str(value["job_id"]),
                owner=TaskOwnerScope(
                    owner_id=str(owner_value["owner_id"]),
                    entrypoint=str(owner_value["entrypoint"]),
                    project_id=_optional_string(owner_value.get("project_id")),
                    variant_id=_optional_string(owner_value.get("variant_id")),
                    session_id=_optional_string(owner_value.get("session_id")),
                ),
                job_type=str(value["job_type"]),
                display_name=str(value["display_name"]),
                state=JobState(str(value["state"])),
                terminal_revision=int(value["terminal_revision"]),
                terminal_sequence=int(value["terminal_sequence"]),
                created_at=datetime.fromisoformat(str(value["created_at"])),
                finished_at=datetime.fromisoformat(str(value["finished_at"])),
                diagnostic_code=_optional_string(value.get("diagnostic_code")),
                artifact_refs=tuple(
                    TaskArtifactRef(
                        artifact_id=str(item["artifact_id"]),
                        kind=str(item["kind"]),
                        label=str(item.get("label", "")),
                    )
                    for item in artifacts_value
                    if isinstance(item, dict)
                ),
                schema_version=version,
            )
        except TaskHistoryError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskHistoryError("history_invalid_record", str(exc)) from exc


class TaskHistoryPort(Protocol):
    def append(self, record: TaskHistoryRecord) -> None: ...

    def list(self, actor: OwnerRef, *, limit: int | None = None) -> tuple[TaskHistoryRecord, ...]: ...


class InMemoryTaskHistoryPort:
    def __init__(self, *, max_records: int = 500) -> None:
        _validate_limit(max_records)
        self._max_records = max_records
        self._records: list[TaskHistoryRecord] = []
        self._lock = threading.RLock()

    def append(self, record: TaskHistoryRecord) -> None:
        with self._lock:
            _append_immutable(self._records, record, max_records=self._max_records)

    def list(self, actor: OwnerRef, *, limit: int | None = None) -> tuple[TaskHistoryRecord, ...]:
        _validate_optional_limit(limit)
        with self._lock:
            records = tuple(record for record in reversed(self._records) if record.visible_to(actor))
        return records if limit is None else records[:limit]


class FilesystemTaskHistoryPort:
    """Small bounded JSON adapter using atomic replacement.

    The constructor receives its data root from composition; this module never
    chooses a user directory on its own.
    """

    def __init__(self, root: str | Path, *, max_records: int = 500) -> None:
        _validate_limit(max_records)
        self._root = Path(root)
        self._path = self._root / "task-history-v1.json"
        self._max_records = max_records
        self._lock = threading.RLock()

    def append(self, record: TaskHistoryRecord) -> None:
        with self._lock:
            records = list(self._read())
            changed = _append_immutable(records, record, max_records=self._max_records)
            if changed:
                self._write(tuple(records))

    def list(self, actor: OwnerRef, *, limit: int | None = None) -> tuple[TaskHistoryRecord, ...]:
        _validate_optional_limit(limit)
        with self._lock:
            records = tuple(record for record in reversed(self._read()) if record.visible_to(actor))
        return records if limit is None else records[:limit]

    def _read(self) -> tuple[TaskHistoryRecord, ...]:
        if not self._path.exists():
            return ()
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TaskHistoryError("history_corrupt", str(exc)) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != TASK_HISTORY_SCHEMA_VERSION:
            raise TaskHistoryError("history_schema_unsupported", "history root schema is unsupported")
        values = payload.get("records")
        if not isinstance(values, list):
            raise TaskHistoryError("history_corrupt", "history records must be an array")
        return tuple(TaskHistoryRecord.from_dict(value) for value in values)

    def _write(self, records: tuple[TaskHistoryRecord, ...]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "schema_version": TASK_HISTORY_SCHEMA_VERSION,
                "records": [record.to_dict() for record in records],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix=".task-history.", suffix=".tmp", dir=self._root)
            temporary = Path(raw_path)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            temporary = None
        except OSError as exc:
            raise TaskHistoryError("history_write_failed", str(exc)) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class TaskEventSource(Protocol):
    def subscribe(
        self,
        callback: Callable[[JobEvent], None],
        *,
        event_filter: TaskEventFilter | None = None,
    ) -> Subscription: ...


@dataclass(frozen=True, slots=True)
class TaskHistoryFailure:
    run_id: str
    code: str
    message: str


class TaskHistoryRecorder:
    """Records terminal events without participating in task state changes."""

    def __init__(
        self,
        events: TaskEventSource,
        history: TaskHistoryPort,
        *,
        on_failure: Callable[[TaskHistoryFailure], None] | None = None,
    ) -> None:
        self._events = events
        self._history = history
        self._on_failure = on_failure
        self._subscription: Subscription | None = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._subscription is not None:
                return
            self._subscription = self._events.subscribe(
                self._on_event,
                event_filter=TaskEventFilter(event_types=frozenset({JobEventType.FINISHED})),
            )

    def close(self) -> None:
        with self._lock:
            subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()

    def _on_event(self, event: JobEvent) -> None:
        try:
            self._history.append(TaskHistoryRecord.from_terminal_event(event))
        except Exception as exc:  # history failure must never rewrite task state
            if self._on_failure is None:
                return
            code = exc.code if isinstance(exc, TaskHistoryError) else "history_write_failed"
            self._on_failure(
                TaskHistoryFailure(
                    run_id=event.snapshot.ref.run_id or event.snapshot.ref.job_id,
                    code=code,
                    message=str(exc),
                )
            )


def _append_immutable(
    records: list[TaskHistoryRecord],
    record: TaskHistoryRecord,
    *,
    max_records: int,
) -> bool:
    for existing in records:
        if existing.run_id != record.run_id:
            continue
        if existing == record:
            return False
        raise TaskHistoryError(
            "history_immutable_conflict",
            f"terminal history for run {record.run_id!r} already exists",
        )
    records.append(record)
    if len(records) > max_records:
        del records[: len(records) - max_records]
    return True


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _validate_limit(limit: int) -> None:
    if isinstance(limit, bool) or limit <= 0:
        raise ValueError("max_records must be positive")


def _validate_optional_limit(limit: int | None) -> None:
    if limit is not None and (isinstance(limit, bool) or limit < 0):
        raise ValueError("limit must not be negative")
