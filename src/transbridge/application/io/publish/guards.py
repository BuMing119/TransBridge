"""Run-scoped commit arbitration for the final atomic replace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from transbridge.application.tasks import CommitPermit, TaskRuntime


@dataclass(frozen=True, slots=True)
class CommitDecision:
    accepted: bool
    reason: str | None = None


class PublishCommitGuard(Protocol):
    def commit(self, run_id: str, mutation: Callable[[], None]) -> CommitDecision: ...


class ImmediateCommitGuard:
    """Synchronous guard for non-TaskRuntime use cases with an explicit run id."""

    def __init__(self, run_id: str, *, active: Callable[[], bool] | None = None) -> None:
        if not run_id.strip():
            raise ValueError("commit guard run_id must not be empty")
        self._run_id = run_id
        self._active = active or (lambda: True)
        self._consumed = False

    def commit(self, run_id: str, mutation: Callable[[], None]) -> CommitDecision:
        if self._consumed:
            return CommitDecision(False, "guard_consumed")
        self._consumed = True
        if run_id != self._run_id:
            return CommitDecision(False, "run_id_mismatch")
        if not self._active():
            return CommitDecision(False, "run_inactive")
        mutation()
        return CommitDecision(True)


class TaskRuntimeCommitGuard:
    """Adapter that executes replace under TaskRuntime cancellation arbitration."""

    def __init__(self, runtime: TaskRuntime, permit: CommitPermit) -> None:
        self._runtime = runtime
        self._permit = permit

    def commit(self, run_id: str, mutation: Callable[[], None]) -> CommitDecision:
        if run_id != self._permit.run_id:
            return CommitDecision(False, "run_id_mismatch")
        result = self._runtime.try_commit(self._permit, mutation)
        return CommitDecision(result.accepted, result.reason)
