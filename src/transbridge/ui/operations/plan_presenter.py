"""Side-effect-free plan editing followed by one request-bound confirmation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import secrets
from threading import RLock
from typing import Protocol

from transbridge.application.security.hitl import ConfirmationAuthority, ConfirmationToken

from .plan_view import OperationKind, OperationPlanViewState
from .preflight_view import OperationPreflightResult


class OperationPlanMapper(Protocol):
    kind: OperationKind

    def present(self, session_id: str, revision: int, draft: object) -> OperationPlanViewState: ...

    def preflight(self, draft: object) -> OperationPreflightResult: ...


class OperationPlanSubmitter(Protocol):
    def submit(
        self,
        kind: OperationKind,
        draft: object,
        preflight: OperationPreflightResult,
        owner_id: str,
    ) -> object: ...


class OperationPlanError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class _Session:
    owner_id: str
    mapper: OperationPlanMapper
    revision: int
    draft: object
    plan: OperationPlanViewState
    preflight: OperationPreflightResult | None = None


class OperationPlanPresenter:
    """Owns UI lifecycle only; feature mappers retain each business request."""

    def __init__(
        self,
        mappers: tuple[OperationPlanMapper, ...],
        submitter: OperationPlanSubmitter,
        *,
        confirmations: ConfirmationAuthority | None = None,
    ) -> None:
        self._mappers = {mapper.kind: mapper for mapper in mappers}
        if len(self._mappers) != len(mappers):
            raise ValueError("one mapper is allowed for each operation kind")
        self._submitter = submitter
        self._confirmations = confirmations or ConfirmationAuthority()
        self._sessions: dict[str, _Session] = {}
        self._lock = RLock()

    def open(self, kind: OperationKind, draft: object, *, owner_id: str) -> OperationPlanViewState:
        if not owner_id.strip():
            raise ValueError("operation owner_id must not be empty")
        mapper = self._mappers.get(OperationKind(kind))
        if mapper is None:
            raise OperationPlanError("MAPPER_UNAVAILABLE", f"no plan mapper is registered for {kind}")
        session_id = secrets.token_urlsafe(18)
        plan = mapper.present(session_id, 1, draft)
        self._validate_plan(mapper, plan)
        with self._lock:
            self._sessions[session_id] = _Session(owner_id, mapper, 1, draft, plan)
        return plan

    def edit(self, session_id: str, draft: object, *, owner_id: str) -> OperationPlanViewState:
        with self._lock:
            session = self._resolve(session_id, owner_id)
            session.revision += 1
            session.draft = draft
            session.preflight = None
            session.plan = session.mapper.present(session_id, session.revision, draft)
            self._validate_plan(session.mapper, session.plan)
            return session.plan

    def preflight(self, session_id: str, *, owner_id: str) -> OperationPreflightResult:
        with self._lock:
            session = self._resolve(session_id, owner_id)
            result = session.mapper.preflight(session.draft)
            if result.kind is not session.mapper.kind or result.request_digest != session.plan.request_digest:
                raise OperationPlanError("PREFLIGHT_IDENTITY_MISMATCH", "preflight does not match the edited plan")
            token = None
            if result.ready:
                token = self._confirmations.issue(owner_id=owner_id, request_hash=self._request_hash(session, result))
            result = replace(result, confirmation_token=token)
            session.preflight = result
            session.plan = replace(
                session.plan,
                submit_enabled=result.ready,
                submit_disabled_reason="" if result.ready else "预检存在阻塞项",
            )
            return result

    def confirm(self, session_id: str, token: ConfirmationToken | None, *, owner_id: str) -> object:
        with self._lock:
            session = self._resolve(session_id, owner_id)
            preflight = session.preflight
            if preflight is None or not preflight.ready:
                raise OperationPlanError("PREFLIGHT_REQUIRED", "operation must pass preflight before confirmation")
            decision = self._confirmations.consume(
                token,
                owner_id=owner_id,
                request_hash=self._request_hash(session, preflight),
            )
            if not decision.allowed:
                raise OperationPlanError(decision.code, decision.reason)
            # Remove before invoking the feature submitter: duplicate UI events
            # cannot create a second network/file task even if submit raises.
            self._sessions.pop(session_id, None)
        return self._submitter.submit(session.mapper.kind, session.draft, preflight, owner_id)

    def cancel(self, session_id: str, *, owner_id: str) -> None:
        """Discard UI state only; it deliberately invokes no feature port."""
        with self._lock:
            self._resolve(session_id, owner_id)
            self._sessions.pop(session_id, None)

    def state(self, session_id: str, *, owner_id: str) -> OperationPlanViewState:
        with self._lock:
            return self._resolve(session_id, owner_id).plan

    def _resolve(self, session_id: str, owner_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise OperationPlanError("PLAN_SESSION_INVALID", "operation plan is closed or unknown")
        if session.owner_id != owner_id:
            raise OperationPlanError("PLAN_OWNER_MISMATCH", "operation plan belongs to another owner")
        return session

    @staticmethod
    def _validate_plan(mapper: OperationPlanMapper, plan: OperationPlanViewState) -> None:
        if plan.kind is not mapper.kind:
            raise OperationPlanError("PLAN_KIND_MISMATCH", "mapper returned a different operation kind")
        if len(plan.request_digest) != 64:
            raise OperationPlanError("PLAN_DIGEST_REQUIRED", "plan mapper must freeze a request digest")

    @staticmethod
    def _request_hash(session: _Session, result: OperationPreflightResult) -> str:
        return (
            f"operation:{session.mapper.kind.value}:{session.revision}:{result.request_digest}:{result.target_revision}"
        )
