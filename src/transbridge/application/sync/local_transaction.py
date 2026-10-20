"""Atomic local aggregate publication ports and callback adapter."""

from collections.abc import Callable
from typing import Protocol

from transbridge.application.io.publish import CommitDecision, PublishCommitGuard

from .models import LocalEntrySnapshot
from .planner import SyncPlanner


class LocalSyncTransactionPort(Protocol):
    def stage(self, entries: tuple[LocalEntrySnapshot, ...]) -> None: ...

    def commit(self, run_id: str, guard: PublishCommitGuard) -> CommitDecision: ...

    def rollback(self) -> None: ...


class LocalSyncUnitOfWorkPort(Protocol):
    def begin(self, *, expected_snapshot_hash: str) -> LocalSyncTransactionPort: ...


class CallbackLocalSyncUnitOfWork:
    """Adapt repositories exposing atomic aggregate load/replace callbacks."""

    def __init__(
        self,
        load: Callable[[], tuple[LocalEntrySnapshot, ...]],
        replace: Callable[[tuple[LocalEntrySnapshot, ...]], None],
    ) -> None:
        self._load = load
        self._replace = replace

    def begin(self, *, expected_snapshot_hash: str) -> LocalSyncTransactionPort:
        return _CallbackTransaction(self._load, self._replace, expected_snapshot_hash)


class _CallbackTransaction:
    def __init__(self, load, replace, expected_snapshot_hash: str) -> None:
        self._load = load
        self._replace = replace
        self._expected_snapshot_hash = expected_snapshot_hash
        self._candidate: tuple[LocalEntrySnapshot, ...] | None = None
        self._closed = False

    def stage(self, entries: tuple[LocalEntrySnapshot, ...]) -> None:
        if self._closed or self._candidate is not None:
            raise RuntimeError("local sync transaction is not stageable")
        self._candidate = tuple(entries)

    def commit(self, run_id: str, guard: PublishCommitGuard) -> CommitDecision:
        if self._closed or self._candidate is None:
            raise RuntimeError("local sync transaction has no staged candidate")

        def mutation() -> None:
            current_hash, _ = SyncPlanner().snapshot_hashes(self._load(), ())
            if current_hash != self._expected_snapshot_hash:
                raise RuntimeError("local aggregate changed before commit")
            self._replace(self._candidate or ())

        try:
            return guard.commit(run_id, mutation)
        finally:
            self._closed = True

    def rollback(self) -> None:
        self._candidate = None
        self._closed = True
