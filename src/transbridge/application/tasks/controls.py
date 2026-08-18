"""Cooperative controls and immutable commit/shutdown contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import threading

from transbridge.application.contracts import JobRef

from .models import JobSnapshot, OwnerRef


class CancellationToken:
    """Thread-safe cooperative cancellation signal owned by ``TaskRuntime``."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise TaskCancelled(self.reason or "task cancellation requested")

    def _cancel(self, reason: str) -> bool:
        with self._lock:
            if self._event.is_set():
                return False
            self._reason = reason
            self._event.set()
            return True


class TaskCancelled(RuntimeError):
    """Raised by cooperative workloads at a cancellation safe point."""


@dataclass(frozen=True, slots=True)
class CommitPermit:
    """One revision-scoped authority to publish a workload result."""

    run_id: str
    owner: OwnerRef
    revision: int
    nonce: str = field(repr=False)

    @property
    def owner_id(self) -> str:
        return self.owner.owner_id


@dataclass(frozen=True, slots=True)
class CommitResult:
    accepted: bool
    snapshot: JobSnapshot
    reason: str | None = None


class StopPolicy(StrEnum):
    DISCARD_CHECKPOINT = "discard-checkpoint"
    PRESERVE_CHECKPOINT = "preserve-checkpoint"


class ShutdownPolicy(StrEnum):
    WAIT = "wait"
    CANCEL = "cancel"
    CHECKPOINT_AND_CANCEL = "checkpoint-and-cancel"


@dataclass(frozen=True, slots=True)
class StopResult:
    snapshot: JobSnapshot
    policy: StopPolicy
    checkpoint_requested: bool


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    policy: ShutdownPolicy
    admission_closed: bool
    requested: tuple[JobRef, ...]
    joined: tuple[JobRef, ...]
    timed_out: tuple[JobRef, ...]
    backend_released: bool


@dataclass(frozen=True, slots=True)
class ControlProjection:
    """Read-only controls for Task Monitor and other entrypoint projections."""

    pause_visible: bool
    pause_enabled: bool
    resume_visible: bool
    resume_enabled: bool
    cancel_visible: bool
    cancel_enabled: bool
    stop_visible: bool
    stop_enabled: bool
