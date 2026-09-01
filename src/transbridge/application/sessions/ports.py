"""Ports for Session persistence, reconciliation, and active-pointer commit."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from transbridge.application.contracts import RequestContext
from transbridge.persistence.v2.ids import SessionRef

from .models import SessionSnapshot


@runtime_checkable
class SessionSnapshotRepositoryPort(Protocol):
    def load(self, ref: SessionRef, context: RequestContext) -> SessionSnapshot: ...

    def save(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
        context: RequestContext,
    ) -> SessionSnapshot: ...

    def delete(self, ref: SessionRef, *, expected_revision: int, context: RequestContext) -> None: ...


@runtime_checkable
class SessionReconcilerPort(Protocol):
    def reconcile(self, snapshot: SessionSnapshot, context: RequestContext) -> SessionSnapshot: ...


@runtime_checkable
class SessionUnitOfWorkPort(Protocol):
    def stage_activate(self, old: SessionRef | None, candidate: SessionSnapshot | None) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class SessionUnitOfWorkFactoryPort(Protocol):
    def begin(self) -> SessionUnitOfWorkPort: ...


class IdentitySessionReconciler:
    def reconcile(self, snapshot: SessionSnapshot, context: RequestContext) -> SessionSnapshot:
        return snapshot


__all__ = [
    "IdentitySessionReconciler",
    "SessionReconcilerPort",
    "SessionSnapshotRepositoryPort",
    "SessionUnitOfWorkFactoryPort",
    "SessionUnitOfWorkPort",
]
