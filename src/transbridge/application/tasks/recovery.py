"""Checkpoint discovery and identity-validated recovery availability."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Protocol

from .activity import TaskOwnerScope
from .checkpoint import CheckpointError, CheckpointExpectation, CheckpointRecord
from .history import TASKS_MANAGE_PERMISSION
from .models import OwnerRef


@dataclass(frozen=True, slots=True)
class CheckpointCatalogItem:
    storage_key: str
    record: CheckpointRecord | None = None
    error_code: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if not self.storage_key.strip():
            raise ValueError("checkpoint storage_key must not be empty")
        if self.record is None and not self.error_code.strip():
            raise ValueError("unreadable checkpoint candidates require an error_code")
        if self.record is not None and self.error_code:
            raise ValueError("valid checkpoint candidates must not carry an error_code")


class CheckpointCatalogPort(Protocol):
    def list_candidates(self) -> tuple[CheckpointCatalogItem, ...]: ...


class FilesystemCheckpointCatalog:
    """Read-only catalog over a ``FilesystemCheckpointPort`` root."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def list_candidates(self) -> tuple[CheckpointCatalogItem, ...]:
        if not self._root.exists():
            return ()
        items: list[CheckpointCatalogItem] = []
        for path in sorted(self._root.rglob("*.checkpoint.json")):
            storage_key = path.name.removesuffix(".checkpoint.json")
            try:
                record = CheckpointRecord.from_json_bytes(path.read_bytes())
            except CheckpointError as exc:
                items.append(
                    CheckpointCatalogItem(
                        storage_key=storage_key,
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                )
            except OSError as exc:
                items.append(
                    CheckpointCatalogItem(
                        storage_key=storage_key,
                        error_code="checkpoint_read_failed",
                        error_message=str(exc),
                    )
                )
            else:
                items.append(CheckpointCatalogItem(storage_key=storage_key, record=record))
        return tuple(items)


@dataclass(frozen=True, slots=True)
class TaskRecoveryDescriptor:
    """Current-context identity required before an old checkpoint is offered."""

    job_type: str
    display_name: str
    expectation: CheckpointExpectation

    def __post_init__(self) -> None:
        if not self.job_type.strip():
            raise ValueError("recovery job_type must not be empty")
        if not self.display_name.strip():
            raise ValueError("recovery display_name must not be empty")


class RecoveryExpectationPort(Protocol):
    def resolve(self, run_id: str) -> TaskRecoveryDescriptor | None: ...


class RecoveryExpectationRegistry:
    """Registry rebuilt by feature compositions from their authoritative state."""

    def __init__(self) -> None:
        self._descriptors: dict[str, TaskRecoveryDescriptor] = {}
        self._lock = threading.RLock()

    def register(self, descriptor: TaskRecoveryDescriptor) -> None:
        run_id = descriptor.expectation.run_id
        if not run_id.strip():
            raise ValueError("recovery run_id must not be empty")
        with self._lock:
            existing = self._descriptors.get(run_id)
            if existing is not None and existing != descriptor:
                raise ValueError(f"recovery descriptor for {run_id!r} is already registered")
            self._descriptors[run_id] = descriptor

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._descriptors.pop(run_id, None)

    def resolve(self, run_id: str) -> TaskRecoveryDescriptor | None:
        with self._lock:
            return self._descriptors.get(run_id)


@dataclass(frozen=True, slots=True)
class TaskRecoveryAvailability:
    storage_key: str
    run_id: str | None
    owner: TaskOwnerScope | None
    job_type: str
    display_name: str
    checkpoint_revision: int | None
    recoverable: bool
    reason_code: str
    reason_message: str = ""


class RecoveryCatalog:
    """Lists recovery availability without resuming or mutating a task."""

    def __init__(
        self,
        checkpoints: CheckpointCatalogPort,
        expectations: RecoveryExpectationPort,
    ) -> None:
        self._checkpoints = checkpoints
        self._expectations = expectations

    def list(self, actor: OwnerRef) -> tuple[TaskRecoveryAvailability, ...]:
        manager = TASKS_MANAGE_PERMISSION in actor.permissions
        output: list[TaskRecoveryAvailability] = []
        for item in self._checkpoints.list_candidates():
            if item.record is None:
                if manager:
                    output.append(
                        TaskRecoveryAvailability(
                            storage_key=item.storage_key,
                            run_id=None,
                            owner=None,
                            job_type="unknown",
                            display_name="Unreadable checkpoint",
                            checkpoint_revision=None,
                            recoverable=False,
                            reason_code=item.error_code,
                            reason_message=item.error_message,
                        )
                    )
                continue

            record = item.record
            if not manager and not record.owner.same_scope(actor):
                continue
            descriptor = self._expectations.resolve(record.run_id)
            if descriptor is None:
                output.append(
                    _availability(
                        item,
                        descriptor=None,
                        recoverable=False,
                        reason_code="recovery_intent_unregistered",
                        reason_message="no current feature can resume this checkpoint",
                    )
                )
                continue
            try:
                record.validate(descriptor.expectation)
            except CheckpointError as exc:
                output.append(
                    _availability(
                        item,
                        descriptor=descriptor,
                        recoverable=False,
                        reason_code=exc.code,
                        reason_message=str(exc),
                    )
                )
                continue
            output.append(
                _availability(
                    item,
                    descriptor=descriptor,
                    recoverable=True,
                    reason_code="recoverable",
                )
            )
        return tuple(output)


def _availability(
    item: CheckpointCatalogItem,
    *,
    descriptor: TaskRecoveryDescriptor | None,
    recoverable: bool,
    reason_code: str,
    reason_message: str = "",
) -> TaskRecoveryAvailability:
    record = item.record
    if record is None:  # pragma: no cover - guarded by caller and type narrowing helper
        raise ValueError("checkpoint record is required")
    return TaskRecoveryAvailability(
        storage_key=item.storage_key,
        run_id=record.run_id,
        owner=TaskOwnerScope.from_owner(record.owner),
        job_type=descriptor.job_type if descriptor is not None else "unknown",
        display_name=descriptor.display_name if descriptor is not None else "Unavailable checkpoint",
        checkpoint_revision=record.revision,
        recoverable=recoverable,
        reason_code=reason_code,
        reason_message=reason_message,
    )
