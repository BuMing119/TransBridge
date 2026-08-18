"""Repository-backed lifecycle UnitOfWork adapter seam.

The transaction store owns staging and atomic pointer exchange.  This wrapper
ensures application code cannot call the final commit more than once or stage
mutations after completion.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from transbridge.application.projects.models import (
    LifecycleActivation,
    LifecycleSave,
    LifecycleSnapshot,
)


class LifecycleMutationKind(StrEnum):
    SAVE = "save"
    ACTIVATE = "activate"
    SNAPSHOT = "snapshot"


@runtime_checkable
class LifecycleTransactionStorePort(Protocol):
    """Staging backend whose ``commit`` publishes the active pointer last.

    Stage methods may write isolated transaction artifacts but must not mutate
    repository current/active references.  A failed commit remains rollbackable.
    """

    def begin(self, transaction_id: str) -> None: ...

    def stage_save(self, transaction_id: str, save: LifecycleSave) -> None: ...

    def stage_activate(self, transaction_id: str, activation: LifecycleActivation) -> None: ...

    def stage_snapshot(self, transaction_id: str, snapshot: LifecycleSnapshot) -> None: ...

    def commit(self, transaction_id: str) -> None: ...

    def rollback(self, transaction_id: str) -> None: ...


class RepositoryLifecycleUnitOfWorkFactory:
    def __init__(self, store: LifecycleTransactionStorePort, token_factory: Callable[[], str]) -> None:
        self._store = store
        self._token_factory = token_factory

    def begin(self) -> RepositoryLifecycleUnitOfWork:
        token = self._token_factory()
        if not token:
            raise RuntimeError("lifecycle transaction token must not be empty")
        self._store.begin(token)
        return RepositoryLifecycleUnitOfWork(self._store, token)


class RepositoryLifecycleUnitOfWork:
    def __init__(self, store: LifecycleTransactionStorePort, transaction_id: str) -> None:
        self._store = store
        self._transaction_id = transaction_id
        self._finished = False
        self._mutation: LifecycleMutationKind | None = None

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    def stage_save(self, save: LifecycleSave) -> None:
        self._stage(LifecycleMutationKind.SAVE, self._store.stage_save, save)

    def stage_activate(self, activation: LifecycleActivation) -> None:
        self._stage(LifecycleMutationKind.ACTIVATE, self._store.stage_activate, activation)

    def stage_snapshot(self, snapshot: LifecycleSnapshot) -> None:
        self._stage(LifecycleMutationKind.SNAPSHOT, self._store.stage_snapshot, snapshot)

    def commit(self) -> None:
        self._ensure_open()
        if self._mutation is None:
            raise RuntimeError("cannot commit an empty lifecycle UnitOfWork")
        self._store.commit(self._transaction_id)
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        try:
            self._store.rollback(self._transaction_id)
        finally:
            self._finished = True

    def _stage(self, kind: LifecycleMutationKind, callback: Callable[..., Any], *args: Any) -> None:
        self._ensure_open()
        if self._mutation is not None:
            raise RuntimeError("a lifecycle UnitOfWork accepts exactly one aggregate mutation")
        callback(self._transaction_id, *args)
        self._mutation = kind

    def _ensure_open(self) -> None:
        if self._finished:
            raise RuntimeError("lifecycle UnitOfWork is already finished")


__all__ = [
    "LifecycleMutationKind",
    "LifecycleTransactionStorePort",
    "RepositoryLifecycleUnitOfWork",
    "RepositoryLifecycleUnitOfWorkFactory",
]
