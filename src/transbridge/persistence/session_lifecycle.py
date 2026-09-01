"""V2 Session repository and active-pointer UnitOfWork adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from threading import RLock
from typing import Protocol, runtime_checkable

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext
from transbridge.application.sessions.models import RecoveryStatus, SessionSnapshot
from transbridge.application.tasks.models import OwnerRef

from .v2.ids import SessionRef
from .v2.models import LoadedRecord, SessionDto


class V2SessionSnapshotRepository:
    def __init__(self, repository) -> None:
        self._repository = repository
        self._lock = RLock()

    def load(self, ref: SessionRef, context: RequestContext) -> SessionSnapshot:
        with self._lock:
            result = self._repository.load(ref)
            if not isinstance(result, LoadedRecord) or not isinstance(result.value, SessionDto):
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SESSION_RECORD_UNAVAILABLE",
                    "The Session record is unavailable or read-only.",
                )
            snapshot = SessionSnapshot.from_dto(result.value, ref)
            if "owner_scope_missing" in snapshot.degradation_reasons:
                owner = OwnerRef(
                    context.owner_id,
                    "legacy-session-migration",
                    None if snapshot.project_id is None else snapshot.project_id.value,
                    None if snapshot.variant_id is None else snapshot.variant_id.value,
                    snapshot.ref.identity.value,
                    context.permissions,
                )
                snapshot = replace(
                    snapshot,
                    owner=owner,
                    recovery=RecoveryStatus.DEGRADED,
                    degradation_reasons=tuple(
                        reason for reason in snapshot.degradation_reasons if reason != "owner_scope_missing"
                    )
                    + ("owner_scope_inferred_from_request",),
                )
            return snapshot

    def save(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int,
        context: RequestContext,
    ) -> SessionSnapshot:
        with self._lock:
            if snapshot.owner.owner_id != context.owner_id:
                raise DomainError(
                    ErrorCategory.PERMISSION,
                    "SESSION_OWNER_MISMATCH",
                    "The Session belongs to another owner.",
                )
            current = self._repository.load(snapshot.ref)
            if not isinstance(current, LoadedRecord) or not isinstance(current.value, SessionDto):
                raise DomainError(
                    ErrorCategory.PREREQUISITE,
                    "SESSION_RECORD_UNAVAILABLE",
                    "The Session cannot be saved because its record is unavailable.",
                )
            if current.value.envelope.revision != expected_revision:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "SESSION_REVISION_CONFLICT",
                    "The Session changed since it was loaded.",
                )
            self._repository.save(snapshot.ref, snapshot.to_dto())
            return snapshot

    def delete(self, ref: SessionRef, *, expected_revision: int, context: RequestContext) -> None:
        with self._lock:
            current = self.load(ref, context)
            if current.owner.owner_id != context.owner_id:
                raise DomainError(ErrorCategory.PERMISSION, "SESSION_OWNER_MISMATCH", "The Session has another owner.")
            if current.revision != expected_revision:
                raise DomainError(
                    ErrorCategory.CONFLICT, "SESSION_REVISION_CONFLICT", "The Session changed since it was loaded."
                )
            self._repository.delete(ref)


@runtime_checkable
class SessionTransactionStorePort(Protocol):
    """Stages Session pointer changes and publishes the active ID only at commit."""

    def begin(self, transaction_id: str) -> None: ...

    def stage_activate(
        self,
        transaction_id: str,
        old: SessionRef | None,
        candidate: SessionSnapshot | None,
    ) -> None: ...

    def commit(self, transaction_id: str) -> None: ...

    def rollback(self, transaction_id: str) -> None: ...


class SessionUnitOfWorkFactory:
    def __init__(self, store: SessionTransactionStorePort, token_factory: Callable[[], str]) -> None:
        self._store = store
        self._token_factory = token_factory

    def begin(self) -> SessionUnitOfWork:
        token = self._token_factory()
        if not token:
            raise RuntimeError("Session transaction token must not be empty")
        self._store.begin(token)
        return SessionUnitOfWork(self._store, token)


class SessionUnitOfWork:
    def __init__(self, store: SessionTransactionStorePort, transaction_id: str) -> None:
        self._store = store
        self._transaction_id = transaction_id
        self._staged = False
        self._finished = False

    def stage_activate(self, old: SessionRef | None, candidate: SessionSnapshot | None) -> None:
        self._ensure_open()
        if self._staged:
            raise RuntimeError("Session UnitOfWork already has an activation")
        self._store.stage_activate(self._transaction_id, old, candidate)
        self._staged = True

    def commit(self) -> None:
        self._ensure_open()
        if not self._staged:
            raise RuntimeError("cannot commit an empty Session UnitOfWork")
        self._store.commit(self._transaction_id)
        self._finished = True

    def rollback(self) -> None:
        if self._finished:
            return
        try:
            self._store.rollback(self._transaction_id)
        finally:
            self._finished = True

    def _ensure_open(self) -> None:
        if self._finished:
            raise RuntimeError("Session UnitOfWork is already finished")


__all__ = [
    "SessionTransactionStorePort",
    "SessionUnitOfWork",
    "SessionUnitOfWorkFactory",
    "V2SessionSnapshotRepository",
]
