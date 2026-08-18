"""GUI command facade over the authoritative Session aggregate/lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.tasks.models import OwnerRef
from transbridge.persistence.v2.ids import ProjectId, SessionId, SessionRef, VariantId
from transbridge.persistence.v2.session_catalog import SessionCatalogEntry, SessionCatalogRepository

from .lifecycle import SessionLifecycleService
from .models import ControllerSnapshot, SessionSnapshot


class SessionCreateRepositoryPort(Protocol):
    def save(self, ref: SessionRef, value): ...


class GuiSessionCommandFacade:
    def __init__(
        self,
        lifecycle: SessionLifecycleService,
        repository: SessionCreateRepositoryPort,
        catalog: SessionCatalogRepository,
        *,
        id_factory: Callable[[], str],
        timestamp_factory: Callable[[], str],
    ) -> None:
        self._lifecycle = lifecycle
        self._repository = repository
        self._catalog = catalog
        self._id_factory = id_factory
        self._timestamp_factory = timestamp_factory

    def create_and_activate(
        self,
        name: str,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any] | None]:
        try:
            ref = SessionRef(SessionId(self._id_factory()))
            project_id = None if context.project_id is None else ProjectId(context.project_id)
            variant_id = None if context.variant_id is None else VariantId(context.variant_id)
            now = self._timestamp_factory()
            owner = OwnerRef(
                context.owner_id,
                "gui",
                context.project_id,
                context.variant_id,
                ref.identity.value,
                context.permissions,
            )
            snapshot = SessionSnapshot(
                ref=ref,
                name=name.strip() or "New conversation",
                owner=owner,
                messages=(),
                backend_history=(),
                backend_summary=None,
                controller=ControllerSnapshot(),
                project_id=project_id,
                variant_id=variant_id,
                approvals=(),
                jobs=(),
                revision=0,
                created_at=now,
                last_active_at=now,
            )
            self._repository.save(ref, snapshot.to_dto())
            self._catalog.upsert(
                SessionCatalogEntry(
                    ref.identity.value,
                    snapshot.name,
                    now,
                    0,
                    snapshot.recovery.value,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)
        return self.switch(ref, context)

    def switch(
        self,
        ref: SessionRef,
        context: RequestContext,
    ) -> OperationResult[dict[str, Any] | None]:
        prepared = self._lifecycle.prepare_switch(ref, replace(context, session_id=ref.identity.value))
        if prepared.outcome is not OperationOutcome.COMPLETED or prepared.value is None:
            return prepared
        result = self._lifecycle.commit_switch(
            prepared.value["token"],
            replace(context, session_id=ref.identity.value),
        )
        if result.is_success:
            self._upsert_active_catalog()
        return result

    def save_conversation(
        self,
        ref: SessionRef,
        visible_messages: list[dict[str, Any]],
        backend_history: list[dict[str, Any]],
        context: RequestContext,
        *,
        backend_summary: str | None = None,
        controller: ControllerSnapshot | None = None,
    ) -> OperationResult[dict[str, Any] | None]:
        active = self._lifecycle.active
        if active is None or active.aggregate.ref != ref:
            return OperationResult.failed(
                DomainError(
                    ErrorCategory.CONFLICT,
                    "SESSION_NOT_ACTIVE",
                    "The Session must be active before its conversation can be saved.",
                ),
                run_id=context.run_id,
            )
        snapshot = active.aggregate.snapshot()
        try:
            active.aggregate.replace_snapshot(
                replace(
                    snapshot,
                    messages=tuple(visible_messages),
                    backend_history=tuple(backend_history),
                    backend_summary=backend_summary,
                    controller=controller or snapshot.controller,
                    last_active_at=self._timestamp_factory(),
                ),
                expected_revision=snapshot.revision,
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult.from_exception(exc, run_id=context.run_id)
        result = self._lifecycle.save_active(replace(context, session_id=ref.identity.value))
        if result.is_success:
            self._upsert_active_catalog()
        return result

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": item.session_id,
                "name": item.name,
                "last_active_at": item.last_active_at,
                "message_count": item.message_count,
                "recovery": item.recovery,
            }
            for item in self._catalog.list()
        ]

    def _upsert_active_catalog(self) -> None:
        active = self._lifecycle.active
        if active is None:
            return
        snapshot = active.aggregate.snapshot()
        self._catalog.upsert(
            SessionCatalogEntry(
                snapshot.ref.identity.value,
                snapshot.name,
                snapshot.last_active_at,
                len(snapshot.messages),
                snapshot.recovery.value,
            )
        )


__all__ = ["GuiSessionCommandFacade", "SessionCreateRepositoryPort"]
