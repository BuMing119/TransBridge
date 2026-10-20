"""GUI command facade over the authoritative Session aggregate/lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from transbridge.application.assistant_requests.attachments import attachment_snapshot
from transbridge.application.contracts import (
    DomainError,
    ErrorCategory,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.tasks.models import TERMINAL_STATES, OwnerRef
from transbridge.persistence.v2.ids import ProjectId, SessionId, SessionRef, VariantId
from transbridge.persistence.v2.models import SessionDto
from transbridge.persistence.v2.session_catalog import SessionCatalogEntry, SessionCatalogRepository

from .legacy_owner import session_snapshot_for_context
from .lifecycle import SessionLifecycleService
from .models import ControllerSnapshot, RecoveryStatus, SessionJobRef, SessionSnapshot
from .scope_cache import SessionScopeCache


class SessionCreateRepositoryPort(Protocol):
    def save(self, ref: SessionRef, value): ...

    def read_snapshot(self, ref: SessionRef) -> SessionDto: ...


class GuiSessionCommandFacade:
    def __init__(
        self,
        lifecycle: SessionLifecycleService,
        repository: SessionCreateRepositoryPort,
        catalog: SessionCatalogRepository,
        *,
        id_factory: Callable[[], str],
        timestamp_factory: Callable[[], str],
        assistant_requests=None,
    ) -> None:
        self._lifecycle = lifecycle
        self._repository = repository
        self._catalog = catalog
        self._id_factory = id_factory
        self._timestamp_factory = timestamp_factory
        self._scope_cache = SessionScopeCache(repository, self._read_snapshot)
        self.assistant_requests = assistant_requests

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
                    context.project_id,
                    context.variant_id,
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

    def read_conversation(self, ref: SessionRef, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Preview owned history without activating, reconciling or writing its Session.

        The workspace scope deliberately does not constrain browsing. All displayed
        scope and execution state come from the target record; callers must activate
        the matching workspace and Session before using any execution controls.
        """
        from transbridge.application.projections.adapters import session_projection

        try:
            snapshot = self._read_snapshot(ref, context)
            if snapshot.owner.owner_id != context.owner_id:
                raise DomainError(
                    ErrorCategory.PERMISSION, "SESSION_OWNER_MISMATCH", "The Session belongs to another owner."
                )
            projection = session_projection(snapshot)
            values = projection.to_dict()["values"]
            if values["assistant_state"].get("request_archives"):
                from transbridge.application.assistant_requests.archival import hydrate_request_state

                values["assistant_state"] = hydrate_request_state(
                    values["assistant_state"],
                    ref.identity.value,
                    getattr(self.assistant_requests, "transcript_store", None),
                )
            return OperationResult.completed(values, run_id=context.run_id)
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def save_conversation(
        self,
        ref: SessionRef,
        visible_messages: list[dict[str, Any]],
        backend_history: list[dict[str, Any]],
        context: RequestContext,
        *,
        backend_summary: str | None = None,
        controller: ControllerSnapshot | None = None,
        jobs: tuple[SessionJobRef, ...] | None = None,
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

        def update(snapshot):
            changes = dict(
                name=_conversation_name(snapshot.name, visible_messages),
                messages=tuple(visible_messages),
                backend_summary=backend_summary,
                controller=controller or snapshot.controller,
                jobs=snapshot.jobs if jobs is None else _merge_job_refs(snapshot.jobs, jobs),
                recovery=RecoveryStatus.COMPLETE,
                last_active_at=self._timestamp_factory(),
            )
            if self.assistant_requests is not None:
                if visible_messages == backend_history and all(m.get("message_id") for m in backend_history):
                    changes["messages"] = backend_history
                return self.assistant_requests.with_transcript(snapshot, backend_history, **changes)
            return replace(snapshot, backend_history=tuple(backend_history), **changes)

        result = self._lifecycle.transact(
            ref,
            replace(context, session_id=ref.identity.value),
            update,
        )
        if result.is_success:
            self._upsert_active_catalog()
        return result

    @property
    def lifecycle(self) -> SessionLifecycleService:
        return self._lifecycle

    def list_sessions(self) -> list[dict[str, Any]]:
        rows = []
        entries = self._catalog.list()
        self._scope_cache.retain({item.session_id for item in entries})
        for item in entries:
            row = {
                "session_id": item.session_id,
                "name": item.name,
                "last_active_at": item.last_active_at,
                "message_count": item.message_count,
                "recovery": item.recovery,
                "project_id": None,
                "variant_id": None,
                "available": True,
            }
            try:
                project_id, variant_id = self._scope_cache.read(SessionRef(SessionId(item.session_id)))
                row.update({"project_id": project_id, "variant_id": variant_id})
            except DomainError as exc:
                row.update({"available": False, "read_error": str(exc)})
            rows.append(row)
        return rows

    def _read_snapshot(self, ref: SessionRef, context: RequestContext | None = None) -> SessionSnapshot:
        try:
            # load() may migrate or quarantine a record. Browsing must never write.
            dto = self._repository.read_snapshot(ref)
            return (
                SessionSnapshot.from_dto(dto, ref)
                if context is None
                else session_snapshot_for_context(dto, ref, context)
            )
        except Exception as exc:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "SESSION_RECORD_UNAVAILABLE",
                "The Session record cannot be read. Restore or repair its saved conversation before continuing.",
                cause=exc,
            ) from exc

    def rename(self, ref: SessionRef, name: str, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Keep the list metadata and authoritative conversation name consistent."""
        try:
            previous = self._catalog_entry(ref)
            replacement = replace(previous, name=name.strip())
            self._catalog.upsert(replacement)
            result = self._lifecycle.rename(ref, replacement.name, replace(context, session_id=ref.identity.value))
            if not result.is_success:
                self._catalog.upsert(previous)
                return result
            return OperationResult.completed(
                {"session_id": ref.identity.value, "name": replacement.name}, run_id=context.run_id
            )
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def delete(self, ref: SessionRef, context: RequestContext) -> OperationResult[dict[str, Any]]:
        """Remove discoverability first, restoring it if the record cannot be deleted."""
        try:
            previous = self._catalog_entry(ref)
            self._catalog.remove(ref.identity.value)
            result = self._lifecycle.delete(ref, replace(context, session_id=ref.identity.value))
            if not result.is_success:
                self._catalog.upsert(previous)
            return result
        except Exception as exc:
            return OperationResult.from_exception(exc, run_id=context.run_id)

    def _catalog_entry(self, ref: SessionRef) -> SessionCatalogEntry:
        entry = next((item for item in self._catalog.list() if item.session_id == ref.identity.value), None)
        if entry is None:
            raise DomainError(ErrorCategory.PREREQUISITE, "SESSION_NOT_LISTED", "会话已不存在，请刷新会话列表。")
        return entry

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
                None if snapshot.project_id is None else snapshot.project_id.value,
                None if snapshot.variant_id is None else snapshot.variant_id.value,
            )
        )


__all__ = ["GuiSessionCommandFacade", "SessionCreateRepositoryPort"]


def _conversation_name(current: str, messages: list[dict[str, Any]]) -> str:
    """Name an untouched conversation from its first real user message."""
    if current not in {"", "新对话", "New conversation"}:
        return current
    for message in messages:
        content = message.get("content", "")
        if message.get("role") == "user" and isinstance(content, str) and not content.startswith("【"):
            snapshot = attachment_snapshot(content)
            if snapshot is not None:
                content = snapshot["user_request"]
            candidate = " ".join(content.split())[:30]
            if candidate:
                return candidate
    return current


def _merge_job_refs(
    previous: tuple[SessionJobRef, ...], current: tuple[SessionJobRef, ...]
) -> tuple[SessionJobRef, ...]:
    """Retain lifecycle evidence when a runtime handle was cleaned up or lost."""
    merged = {job.ref.job_id: job for job in current}
    for job in previous:
        if job.ref.job_id not in merged:
            merged[job.ref.job_id] = (
                job
                if job.state in TERMINAL_STATES
                else replace(job, recoverable=False, reason="task_runtime_job_unavailable")
            )
    return tuple(merged.values())
