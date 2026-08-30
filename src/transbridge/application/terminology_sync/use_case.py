"""Planning use case with deterministic freshness checks and one-use confirmation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Protocol

from transbridge.application.security.hitl import ConfirmationAuthority, ConfirmationToken

from .models import TerminologySyncMode, TerminologySyncTarget
from .plan_models import TerminologySyncPlan
from .planner import TerminologySyncPlanner, TerminologySyncPlannerInput


@dataclass(frozen=True, slots=True)
class CreateTerminologySyncPlanRequest:
    local_project_id: str
    local_variant_id: str
    target: TerminologySyncTarget
    mode: TerminologySyncMode
    binding_revision: int | None = None

    def __post_init__(self) -> None:
        if not self.local_project_id.strip() or not self.local_variant_id.strip():
            raise ValueError("terminology sync planning requires a local Project/Variant")
        object.__setattr__(self, "mode", TerminologySyncMode(self.mode))
        if self.binding_revision is not None and (isinstance(self.binding_revision, bool) or self.binding_revision < 0):
            raise ValueError("binding revision must be absent or non-negative")


@dataclass(frozen=True, slots=True)
class AuthorizeTerminologySyncPlanRequest:
    plan: TerminologySyncPlan
    owner_id: str
    context: CreateTerminologySyncPlanRequest
    confirmation_token: ConfirmationToken | None = None

    def __post_init__(self) -> None:
        if not self.owner_id.strip():
            raise ValueError("terminology sync confirmation owner must not be empty")


@dataclass(frozen=True, slots=True)
class AuthorizedTerminologySyncPlan:
    plan: TerminologySyncPlan
    owner_id: str
    confirmation_code: str


class TerminologySyncPlanningInputPort(Protocol):
    def load(self, request: CreateTerminologySyncPlanRequest) -> TerminologySyncPlannerInput: ...


class TerminologySyncPlanStaleError(RuntimeError):
    code = "STALE_TERMINOLOGY_SYNC_PLAN"


class TerminologySyncPlanAuthorizationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TerminologySyncPlanningUseCase:
    def __init__(
        self,
        inputs: TerminologySyncPlanningInputPort,
        *,
        planner: TerminologySyncPlanner | None = None,
        confirmations: ConfirmationAuthority | None = None,
    ) -> None:
        self._inputs = inputs
        self._planner = planner or TerminologySyncPlanner()
        self._confirmations = confirmations or ConfirmationAuthority()

    def create_plan(self, request: CreateTerminologySyncPlanRequest) -> TerminologySyncPlan:
        inputs = self._inputs.load(request)
        if inputs.profile.mode is not request.mode:
            raise TerminologySyncPlanAuthorizationError(
                "SYNC_MODE_CHANGED",
                "the persisted terminology sync profile does not match the requested mode",
            )
        return self._planner.plan(inputs)

    def issue_confirmation(self, plan: TerminologySyncPlan, *, owner_id: str) -> ConfirmationToken:
        self._verify_authorizable(plan)
        if not owner_id.strip():
            raise ValueError("terminology sync confirmation owner must not be empty")
        return self._confirmations.issue(
            owner_id=owner_id,
            request_hash=_confirmation_request_hash(plan),
        )

    def authorize(self, request: AuthorizeTerminologySyncPlanRequest) -> AuthorizedTerminologySyncPlan:
        self._verify_authorizable(request.plan)
        if not _context_matches_plan(request.context, request.plan):
            raise TerminologySyncPlanAuthorizationError(
                "PLAN_SCOPE_CHANGED",
                "terminology sync plan does not belong to the requested Project/Variant/target",
            )
        current = self.create_plan(request.context)
        if current.plan_hash != request.plan.plan_hash:
            raise TerminologySyncPlanStaleError("terminology sync inputs changed after planning")
        if not request.plan.requires_confirmation:
            return AuthorizedTerminologySyncPlan(request.plan, request.owner_id, "NOT_REQUIRED")
        decision = self._confirmations.consume(
            request.confirmation_token,
            owner_id=request.owner_id,
            request_hash=_confirmation_request_hash(request.plan),
        )
        if not decision.allowed:
            raise TerminologySyncPlanAuthorizationError(decision.code, decision.reason)
        return AuthorizedTerminologySyncPlan(request.plan, request.owner_id, decision.code)

    @staticmethod
    def _verify_authorizable(plan: TerminologySyncPlan) -> None:
        if plan.compute_hash() != plan.plan_hash:
            raise TerminologySyncPlanAuthorizationError(
                "PLAN_HASH_INVALID",
                "terminology sync plan content does not match its hash",
            )
        if plan.blocked:
            raise TerminologySyncPlanAuthorizationError(
                "PLAN_BLOCKED",
                "blocked terminology sync plans cannot be authorized",
            )
        if plan.has_conflicts:
            raise TerminologySyncPlanAuthorizationError(
                "PLAN_HAS_CONFLICTS",
                "terminology sync conflicts require a new reviewed plan",
            )


def _context_matches_plan(
    context: CreateTerminologySyncPlanRequest,
    plan: TerminologySyncPlan,
) -> bool:
    return (
        context.local_project_id == plan.local_project_id
        and context.local_variant_id == plan.local_variant_id
        and context.target.target_id == plan.target_identity
        and context.mode is plan.mode
        and context.binding_revision == plan.binding_revision
    )


def _confirmation_request_hash(plan: TerminologySyncPlan) -> str:
    payload = f"terminology-sync\0{plan.mode.value}\0{plan.line_id}\0{plan.plan_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AuthorizedTerminologySyncPlan",
    "AuthorizeTerminologySyncPlanRequest",
    "CreateTerminologySyncPlanRequest",
    "TerminologySyncPlanAuthorizationError",
    "TerminologySyncPlanStaleError",
    "TerminologySyncPlanningInputPort",
    "TerminologySyncPlanningUseCase",
]
