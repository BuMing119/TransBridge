"""Two-phase Session switching and late-event owner isolation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import secrets
from threading import RLock
from typing import Any, TypedDict

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.persistence.v2.ids import SessionRef

from .aggregate import EventApplication, SessionAggregate, SessionRuntimeEvent
from .models import SessionSnapshot
from .ports import (
    IdentitySessionReconciler,
    SessionReconcilerPort,
    SessionSnapshotRepositoryPort,
    SessionUnitOfWorkFactoryPort,
)


@dataclass(frozen=True, slots=True)
class ActiveSession:
    aggregate: SessionAggregate
    persisted_revision: int

    @property
    def dirty(self) -> bool:
        return self.aggregate.revision != self.persisted_revision


class PreparedSessionTransition(TypedDict):
    token: str
    owner_id: str
    expected_generation: int
    old_session_id: str | None
    target_session_id: str | None
    target_revision: int | None
    recovery: str | None
    degradation_reasons: list[str]


@dataclass(frozen=True, slots=True)
class _Prepared:
    public: PreparedSessionTransition
    candidate: SessionAggregate | None
    old_signature: tuple[str, int] | None


class SessionLifecycleService:
    def __init__(
        self,
        repository: SessionSnapshotRepositoryPort,
        unit_of_work: SessionUnitOfWorkFactoryPort,
        *,
        active: ActiveSession | None = None,
        reconciler: SessionReconcilerPort | None = None,
        token_factory: Callable[[], str] | None = None,
        projection: Callable[[SessionSnapshot | None], None] | None = None,
        event_sink: Callable[[SessionSnapshot, EventApplication], None] | None = None,
    ) -> None:
        self._repository = repository
        self._unit_of_work = unit_of_work
        self._active = active
        self._reconciler = reconciler or IdentitySessionReconciler()
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(24))
        self._projection = projection
        self._event_sink = event_sink
        self._generation = 0
        self._prepared: dict[str, _Prepared] = {}
        self._issued_tokens: set[str] = set()
        self._sessions: dict[str, SessionAggregate] = {}
        if active is not None:
            self._sessions[active.aggregate.ref.identity.value] = active.aggregate
        self._lock = RLock()

    @property
    def active(self) -> ActiveSession | None:
        with self._lock:
            return self._active

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def prepare_switch(
        self,
        target: SessionRef | None,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any]]:
        with self._lock:
            if context.session_id is not None and target is not None and context.session_id != target.identity.value:
                return _failed(
                    "SESSION_CONTEXT_MISMATCH",
                    "The request context targets a different Session.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            if self._active is not None and self._active.dirty:
                saved = self.save_active(context)
                if saved.outcome is not OperationOutcome.COMPLETED:
                    return saved
            try:
                candidate = None
                if target is not None:
                    snapshot = self._repository.load(target, context)
                    snapshot = self._reconciler.reconcile(snapshot, context)
                    if snapshot.ref != target:
                        raise DomainError(
                            ErrorCategory.CONFLICT,
                            "SESSION_REFERENCE_MISMATCH",
                            "The loaded Session identity does not match its target reference.",
                        )
                    if snapshot.owner.owner_id != context.owner_id:
                        raise DomainError(
                            ErrorCategory.PERMISSION,
                            "SESSION_OWNER_MISMATCH",
                            "The target Session belongs to another owner.",
                        )
                    if (context.project_id is not None and snapshot.owner.project_id != context.project_id) or (
                        context.variant_id is not None and snapshot.owner.variant_id != context.variant_id
                    ):
                        raise DomainError(
                            ErrorCategory.PERMISSION,
                            "SESSION_CONTEXT_SCOPE_MISMATCH",
                            "The target Session is outside the request Project or Variant scope.",
                        )
                    candidate = SessionAggregate(snapshot)
                token = self._new_token()
                candidate_snapshot = None if candidate is None else candidate.snapshot()
                public: PreparedSessionTransition = {
                    "token": token,
                    "owner_id": context.owner_id,
                    "expected_generation": self._generation,
                    "old_session_id": None if self._active is None else self._active.aggregate.ref.identity.value,
                    "target_session_id": None if target is None else target.identity.value,
                    "target_revision": None if candidate_snapshot is None else candidate_snapshot.revision,
                    "recovery": None if candidate_snapshot is None else candidate_snapshot.recovery.value,
                    "degradation_reasons": (
                        [] if candidate_snapshot is None else list(candidate_snapshot.degradation_reasons)
                    ),
                }
                self._prepared[token] = _Prepared(public, candidate, _signature(self._active))
                return OperationResult.completed(public, run_id=context.run_id)
            except Exception as exc:  # noqa: BLE001
                return _from_exception(exc, "SESSION_PREPARE_FAILED", context)

    def commit_switch(self, token: str, context: RequestContext) -> OperationResult[dict[str, Any] | None]:
        with self._lock:
            prepared = self._prepared.get(token)
            if prepared is None:
                return _failed(
                    "PREPARED_SESSION_INVALID",
                    "The prepared Session transition is unknown or consumed.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            if prepared.public["owner_id"] != context.owner_id:
                return _failed(
                    "PREPARED_SESSION_OWNER_MISMATCH",
                    "The prepared Session transition belongs to another owner.",
                    ErrorCategory.PERMISSION,
                    context,
                )
            self._prepared.pop(token)
            if prepared.public["expected_generation"] != self._generation or prepared.old_signature != _signature(
                self._active
            ):
                if prepared.candidate is not None:
                    prepared.candidate.close()
                return _failed(
                    "PREPARED_SESSION_STALE",
                    "The active Session changed after preparation.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            old = self._active
            candidate_snapshot = None if prepared.candidate is None else prepared.candidate.snapshot()
            unit = None
            try:
                unit = self._unit_of_work.begin()
                unit.stage_activate(
                    None if old is None else old.aggregate.ref,
                    candidate_snapshot,
                )
                unit.commit()
            except Exception as exc:  # noqa: BLE001
                _rollback(unit)
                if prepared.candidate is not None:
                    prepared.candidate.close()
                return _from_exception(exc, "SESSION_COMMIT_FAILED", context)

            if prepared.candidate is None:
                self._active = None
            else:
                self._active = ActiveSession(prepared.candidate, prepared.candidate.revision)
                self._sessions[prepared.candidate.ref.identity.value] = prepared.candidate
            self._generation += 1
            diagnostics: list[Diagnostic] = []
            if old is not None:
                old.aggregate.close()
            if self._projection is not None:
                try:
                    self._projection(None if self._active is None else self._active.aggregate.snapshot())
                except Exception:  # noqa: BLE001 - projection cannot roll domain state back
                    diagnostics.append(
                        Diagnostic(
                            "SESSION_PROJECTION_FAILED",
                            "The Session committed, but projection refresh failed.",
                            DiagnosticSeverity.WARNING,
                        )
                    )
            return OperationResult.completed(
                None if self._active is None else _summary(self._active),
                diagnostics=tuple(diagnostics),
                run_id=context.run_id,
            )

    def save_active(self, context: RequestContext) -> OperationResult[dict[str, Any] | None]:
        with self._lock:
            active = self._active
            if active is None or not active.dirty:
                return OperationResult.completed(None if active is None else _summary(active), run_id=context.run_id)
            snapshot = active.aggregate.snapshot()
            try:
                persisted = self._repository.save(
                    snapshot,
                    expected_revision=active.persisted_revision,
                    context=context,
                )
            except Exception as exc:  # noqa: BLE001
                self._publish_projection(active.aggregate.snapshot())
                return _from_exception(exc, "SESSION_SAVE_FAILED", context)
            if persisted.ref != snapshot.ref or persisted.revision != snapshot.revision:
                return _failed(
                    "SESSION_SAVE_ACK_INVALID",
                    "The Session repository returned an inconsistent save acknowledgement.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            self._active = replace(active, persisted_revision=persisted.revision)
            if active.aggregate.revision != persisted.revision:
                return _failed(
                    "SESSION_SAVE_REVISION_CHANGED",
                    "The Session changed while it was being saved; retry is required.",
                    ErrorCategory.CONFLICT,
                    context,
                )
            self._publish_projection(self._active.aggregate.snapshot())
            return OperationResult.completed(_summary(self._active), run_id=context.run_id)

    def route_runtime_event(self, event: SessionRuntimeEvent) -> EventApplication:
        with self._lock:
            session_id = event.owner.session_id
            aggregate = None if session_id is None else self._sessions.get(session_id)
            if aggregate is None:
                return EventApplication(
                    False,
                    0,
                    Diagnostic(
                        "SESSION_EVENT_OWNER_UNKNOWN",
                        "The runtime event belongs to an unknown or destroyed Session.",
                        DiagnosticSeverity.WARNING,
                    ),
                )
            decision = aggregate.apply_runtime_event(event)
            diagnostic = decision.diagnostic
            if decision.applied and self._event_sink is not None:
                try:
                    self._event_sink(aggregate.snapshot(), decision)
                except Exception:
                    diagnostic = Diagnostic(
                        "SESSION_EVENT_SINK_FAILED",
                        "The Session event was applied in memory but could not be persisted.",
                        DiagnosticSeverity.WARNING,
                    )
            if (
                decision.applied
                and self._active is not None
                and aggregate is self._active.aggregate
                and self._projection is not None
            ):
                try:
                    self._projection(aggregate.snapshot())
                except Exception:
                    diagnostic = Diagnostic(
                        "SESSION_EVENT_PROJECTION_FAILED",
                        "The Session event was applied, but its active projection could not be refreshed.",
                        DiagnosticSeverity.WARNING,
                    )
            if diagnostic is decision.diagnostic:
                return decision
            return EventApplication(decision.applied, decision.revision, diagnostic)

    def detach_session(self, ref: SessionRef) -> None:
        with self._lock:
            aggregate = self._sessions.pop(ref.identity.value, None)
            if aggregate is not None:
                aggregate.close()
            if self._active is not None and self._active.aggregate.ref == ref:
                self._active = None
                self._generation += 1

    def rename(self, ref: SessionRef, name: str, context: RequestContext) -> OperationResult[SessionSnapshot]:
        """Persist a metadata change without switching away from the active conversation."""
        with self._lock:
            try:
                active = self._active if self._active is not None and self._active.aggregate.ref == ref else None
                snapshot = active.aggregate.snapshot() if active else self._repository.load(ref, context)
                expected_revision = active.persisted_revision if active else snapshot.revision
                retained = self._sessions.get(ref.identity.value)
                if active is None and retained is not None and retained.revision > snapshot.revision:
                    snapshot = retained.snapshot()
                _require_management_scope(snapshot, context)
                updated = replace(snapshot, name=name.strip(), revision=snapshot.revision + 1)
                persisted = self._repository.save(
                    updated,
                    expected_revision=expected_revision,
                    context=context,
                )
                if active is not None:
                    active.aggregate.replace_snapshot(persisted, expected_revision=snapshot.revision)
                    self._active = replace(active, persisted_revision=persisted.revision)
                    self._publish_projection(persisted)
                elif retained is not None:
                    retained.close()
                    self._sessions[ref.identity.value] = SessionAggregate(persisted)
                return OperationResult.completed(persisted, run_id=context.run_id)
            except Exception as exc:
                return _from_exception(exc, "SESSION_RENAME_FAILED", context)

    def delete(self, ref: SessionRef, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Detach the active pointer before deleting its record; restore it on deletion failure."""
        with self._lock:
            was_active = self._active is not None and self._active.aggregate.ref == ref
            detached = False
            try:
                snapshot = self._repository.load(ref, context)
                _require_management_scope(snapshot, context)
                if was_active:
                    prepared = self.prepare_switch(None, context)
                    if not prepared.is_success or prepared.value is None:
                        return prepared
                    switched = self.commit_switch(prepared.value["token"], context)
                    if not switched.is_success:
                        return switched
                    detached = True
                    # Preparing the switch may have saved dirty conversation data.
                    snapshot = self._repository.load(ref, context)
                self._repository.delete(ref, expected_revision=snapshot.revision, context=context)
                self.detach_session(ref)
                return OperationResult.completed({"deleted_session_id": ref.identity.value}, run_id=context.run_id)
            except Exception as exc:
                if detached:
                    restored = self.prepare_switch(ref, context)
                    if restored.is_success and restored.value is not None:
                        restored = self.commit_switch(restored.value["token"], context)
                    if not restored.is_success:
                        return OperationResult.failed(
                            DomainError(
                                ErrorCategory.INTERNAL,
                                "SESSION_DELETE_RESTORE_FAILED",
                                "删除会话失败，活动会话恢复也失败；请从会话列表重新打开。",
                                cause=exc,
                            ),
                            run_id=context.run_id,
                        )
                return _from_exception(exc, "SESSION_DELETE_FAILED", context)

    @property
    def retained_session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _new_token(self) -> str:
        token = self._token_factory()
        if not token or token in self._issued_tokens:
            raise RuntimeError("Session transition token is empty or duplicated")
        self._issued_tokens.add(token)
        return token

    def _publish_projection(self, snapshot: SessionSnapshot | None) -> None:
        if self._projection is None:
            return
        try:
            self._projection(snapshot)
        except Exception:
            return


def _signature(active: ActiveSession | None) -> tuple[str, int] | None:
    if active is None:
        return None
    return active.aggregate.ref.identity.value, active.aggregate.revision


def _require_management_scope(snapshot: SessionSnapshot, context: RequestContext) -> None:
    if snapshot.owner.owner_id != context.owner_id:
        raise DomainError(ErrorCategory.PERMISSION, "SESSION_OWNER_MISMATCH", "The Session belongs to another owner.")
    if (
        (context.session_id is not None and context.session_id != snapshot.ref.identity.value)
        or (context.project_id is not None and context.project_id != snapshot.owner.project_id)
        or (context.variant_id is not None and context.variant_id != snapshot.owner.variant_id)
    ):
        raise DomainError(
            ErrorCategory.PERMISSION, "SESSION_CONTEXT_SCOPE_MISMATCH", "The Session is outside the request scope."
        )


def _summary(active: ActiveSession) -> dict[str, Any]:
    snapshot = active.aggregate.snapshot()
    return {
        "session_id": snapshot.ref.identity.value,
        "revision": snapshot.revision,
        "persisted_revision": active.persisted_revision,
        "dirty": active.dirty,
        "recovery": snapshot.recovery.value,
        "degradation_reasons": list(snapshot.degradation_reasons),
    }


def _failed[T](
    code: str,
    message: str,
    category: ErrorCategory,
    context: RequestContext,
) -> OperationResult[T]:
    return OperationResult.failed(DomainError(category, code, message), run_id=context.run_id)


def _from_exception[T](exc: Exception, fallback: str, context: RequestContext) -> OperationResult[T]:
    error = (
        exc
        if isinstance(exc, DomainError)
        else DomainError(
            ErrorCategory.INTERNAL,
            fallback,
            "The Session lifecycle failed before committing active state.",
            cause=exc,
        )
    )
    return OperationResult.failed(error, run_id=context.run_id)


def _rollback(unit: Any | None) -> None:
    if unit is None:
        return
    try:
        unit.rollback()
    except Exception:
        return


__all__ = ["ActiveSession", "PreparedSessionTransition", "SessionLifecycleService"]
