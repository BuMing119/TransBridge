"""Entrypoint-neutral facade for terminology synchronization workflows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from threading import RLock
from typing import Protocol

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.security.hitl import ConfirmationAuthority, ConfirmationToken
from transbridge.application.tasks import JobSnapshot, OwnerRef, TaskRuntime
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor

from .draft_import_models import DraftImportChoice, DraftImportCommitResult, DraftImportProposal, DraftImportSelection
from .execution_models import TerminologyBackupExecutionResult, TerminologySyncRetryToken
from .inbound import InboundTerminologyChangeSet
from .models import TerminologySyncMode, TerminologySyncTarget
from .plan_models import TerminologySyncPlan, TerminologySyncPlanItem
from .task_adapter import TerminologySyncTaskDraft, TerminologySyncTaskEntrypoint
from .use_case import (
    AuthorizedTerminologySyncPlan,
    AuthorizeTerminologySyncPlanRequest,
    CreateTerminologySyncPlanRequest,
    TerminologySyncPlanningUseCase,
)


@dataclass(frozen=True, slots=True)
class TerminologySyncPreflight:
    """Bounded prerequisite projection shared by UI, Agent and MCP."""

    mode: TerminologySyncMode
    available: bool
    project_id: str | None = None
    variant_id: str | None = None
    local_version_id: str | None = None
    local_content_digest: str | None = None
    target: TerminologySyncTarget | None = None
    profile_id: str | None = None
    mapping_status: str = "unmapped"
    last_outcome: str | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", TerminologySyncMode(self.mode))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.available and (self.project_id is None or self.variant_id is None or self.target is None):
            raise ValueError("available terminology sync preflight requires Project, Variant and target")


class TerminologySyncContextPort(Protocol):
    def preflight(self, context: RequestContext, mode: TerminologySyncMode) -> TerminologySyncPreflight: ...

    def planning_request(
        self,
        context: RequestContext,
        mode: TerminologySyncMode,
    ) -> CreateTerminologySyncPlanRequest: ...

    def activate_mapping(
        self,
        context: RequestContext,
        mode: TerminologySyncMode,
        *,
        replace_existing: bool = False,
    ) -> TerminologySyncPreflight: ...


class TerminologyInboundApplicationPort(Protocol):
    def list_inbound(self, context: RequestContext) -> tuple[InboundTerminologyChangeSet, ...]: ...

    def get_inbound(self, context: RequestContext, change_set_id: str) -> InboundTerminologyChangeSet: ...

    def prepare_selection(
        self,
        context: RequestContext,
        change_set_id: str,
        choices: tuple[DraftImportChoice, ...],
    ) -> DraftImportSelection: ...

    def preview_import(self, selection: DraftImportSelection) -> DraftImportProposal: ...

    def commit_import(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult: ...


@dataclass(frozen=True, slots=True)
class TerminologySyncPlanRef:
    plan_id: str
    plan_hash: str


@dataclass(frozen=True, slots=True)
class TerminologySyncPlanSummary:
    ref: TerminologySyncPlanRef
    mode: TerminologySyncMode
    project_id: str
    variant_id: str
    target_identity: str
    counts: tuple[tuple[str, int], ...]
    diagnostics: tuple[str, ...]
    blocked: bool
    has_conflicts: bool
    destructive: bool
    requires_confirmation: bool
    execution_available: bool


@dataclass(frozen=True, slots=True)
class _StoredPlan:
    plan: TerminologySyncPlan
    request: CreateTerminologySyncPlanRequest
    context: RequestContext


class TerminologySyncApplicationService:
    """One workflow and plan repository for every product entrypoint."""

    def __init__(
        self,
        *,
        contexts: TerminologySyncContextPort,
        planning: TerminologySyncPlanningUseCase,
        tasks: TerminologySyncTaskEntrypoint,
        runtime: TaskRuntime,
        inbound: TerminologyInboundApplicationPort | None = None,
        bidirectional_tasks: TerminologySyncTaskEntrypoint | None = None,
        max_plans: int = 100,
    ) -> None:
        if max_plans < 1:
            raise ValueError("max_plans must be positive")
        self._contexts = contexts
        self._planning = planning
        self._tasks = tasks
        self._runtime = runtime
        self._inbound = inbound
        self._bidirectional_tasks = bidirectional_tasks
        self._max_plans = max_plans
        self._plans: dict[str, _StoredPlan] = {}
        self._jobs: dict[str, tuple[JobRef, OwnerRef, TerminologySyncTaskEntrypoint]] = {}
        self._proposals: dict[str, DraftImportProposal] = {}
        self._lock = RLock()
        self._mapping_confirmations = ConfirmationAuthority()

    def preflight(self, context: RequestContext, mode: TerminologySyncMode) -> TerminologySyncPreflight:
        return self._contexts.preflight(context, TerminologySyncMode(mode))

    def create_plan(self, context: RequestContext, mode: TerminologySyncMode) -> TerminologySyncPlanSummary:
        mode = TerminologySyncMode(mode)
        preflight = self.preflight(context, mode)
        if not preflight.available:
            detail = "; ".join(preflight.diagnostics) or "terminology synchronization prerequisites are unavailable"
            raise RuntimeError(detail)
        request = self._contexts.planning_request(context, mode)
        plan = self._planning.create_plan(request)
        stored = _StoredPlan(plan, request, context)
        with self._lock:
            self._plans[plan.plan_hash] = stored
            while len(self._plans) > self._max_plans:
                self._plans.pop(next(iter(self._plans)))
        return _summary(
            plan, execution_available=plan.mode is TerminologySyncMode.BACKUP or self._bidirectional_tasks is not None
        )

    def issue_mapping_replacement_confirmation(
        self,
        context: RequestContext,
        mode: TerminologySyncMode,
    ) -> ConfirmationToken:
        mode = TerminologySyncMode(mode)
        preflight = self.preflight(context, mode)
        return self._mapping_confirmations.issue(
            owner_id=context.owner_id,
            request_hash=_mapping_request_hash(context, mode, preflight),
        )

    def activate_mapping(
        self,
        context: RequestContext,
        mode: TerminologySyncMode,
        replacement_token: ConfirmationToken | None = None,
    ) -> TerminologySyncPreflight:
        activate = getattr(self._contexts, "activate_mapping", None)
        if not callable(activate):
            raise RuntimeError("terminology sync mapping activation is not configured")
        mode = TerminologySyncMode(mode)
        replace_existing = replacement_token is not None
        if replace_existing:
            preflight = self.preflight(context, mode)
            decision = self._mapping_confirmations.consume(
                replacement_token,
                owner_id=context.owner_id,
                request_hash=_mapping_request_hash(context, mode, preflight),
            )
            if not decision.allowed:
                raise PermissionError(decision.reason)
        result = activate(context, mode, replace_existing=replace_existing)
        with self._lock:
            self._plans.clear()
        return result

    def page_plan(
        self, ref: TerminologySyncPlanRef, request: PageRequest = PageRequest()
    ) -> Page[TerminologySyncPlanItem]:
        plan = self._stored(ref).plan
        start = _cursor_offset(request, plan.plan_hash)
        items = plan.items[start : start + request.limit]
        next_offset = start + len(items)
        cursor = None
        if next_offset < len(plan.items):
            cursor = SnapshotCursor(plan.plan_hash, request.query_fingerprint, (str(next_offset),), str(next_offset))
        return Page(items, plan.plan_hash, cursor, len(plan.items))

    def issue_confirmation(self, ref: TerminologySyncPlanRef, owner: OwnerRef) -> ConfirmationToken:
        stored = self._stored(ref)
        _check_owner_scope(stored.context, owner)
        return self._planning.issue_confirmation(stored.plan, owner_id=owner.owner_id)

    def authorize(
        self,
        ref: TerminologySyncPlanRef,
        owner: OwnerRef,
        confirmation_token: ConfirmationToken | None = None,
    ) -> AuthorizedTerminologySyncPlan:
        stored = self._stored(ref)
        _check_owner_scope(stored.context, owner)
        return self._planning.authorize(
            AuthorizeTerminologySyncPlanRequest(
                plan=stored.plan,
                owner_id=owner.owner_id,
                context=stored.request,
                confirmation_token=confirmation_token,
            )
        )

    def submit(self, authorized_plan: AuthorizedTerminologySyncPlan, owner: OwnerRef) -> JobRef:
        task_entrypoint = self._tasks
        if authorized_plan.plan.mode is TerminologySyncMode.BIDIRECTIONAL:
            if self._bidirectional_tasks is None:
                raise RuntimeError("bidirectional terminology execution is not configured")
            task_entrypoint = self._bidirectional_tasks
        ref = task_entrypoint.submit(TerminologySyncTaskDraft(authorized_plan), owner)
        with self._lock:
            self._jobs[ref.job_id] = (ref, owner, task_entrypoint)
        return ref

    def execute(
        self,
        ref: TerminologySyncPlanRef,
        owner: OwnerRef,
        confirmation_token: ConfirmationToken | None = None,
    ) -> JobRef:
        return self.submit(self.authorize(ref, owner, confirmation_token), owner)

    def status(self, ref: JobRef, actor: OwnerRef) -> JobSnapshot:
        return self._runtime.get(ref, actor)

    def result(self, ref: JobRef, actor: OwnerRef) -> TerminologyBackupExecutionResult | None:
        with self._lock:
            stored = self._jobs.get(ref.job_id)
        if stored is None:
            raise KeyError("unknown terminology sync job")
        stored_ref, _owner, entrypoint = stored
        if stored_ref != ref:
            raise PermissionError("terminology sync job identity changed")
        return entrypoint.result(ref, actor)

    def retry(self, token: TerminologySyncRetryToken, actor: OwnerRef) -> JobRef:
        if token.owner_id != actor.owner_id:
            raise PermissionError("terminology sync retry belongs to another owner")
        with self._lock:
            candidate = self._plans.get(token.plan_hash)
        if candidate is None:
            raise KeyError("unknown or expired terminology sync retry plan")
        stored = self._stored(TerminologySyncPlanRef(candidate.plan.plan_id, token.plan_hash))
        _check_owner_scope(stored.context, actor)
        authorized = AuthorizedTerminologySyncPlan(stored.plan, actor.owner_id, "RETRY")
        entrypoint = self._entrypoint_for(stored.plan.mode)
        ref = entrypoint.submit(TerminologySyncTaskDraft(authorized, retry_token=token), actor)
        with self._lock:
            self._jobs[ref.job_id] = (ref, actor, entrypoint)
        return ref

    def reconcile(self, token: TerminologySyncRetryToken, actor: OwnerRef) -> JobRef:
        if token.owner_id != actor.owner_id:
            raise PermissionError("terminology sync reconciliation belongs to another owner")
        if not token.unknown_item_ids:
            raise ValueError("reconciliation requires unknown item outcomes")
        with self._lock:
            stored = self._plans.get(token.plan_hash)
        if stored is None:
            raise KeyError("unknown or expired terminology sync reconcile plan")
        _check_owner_scope(stored.context, actor)
        authorized = AuthorizedTerminologySyncPlan(stored.plan, actor.owner_id, "RECONCILE")
        entrypoint = self._entrypoint_for(stored.plan.mode)
        ref = entrypoint.reconcile(TerminologySyncTaskDraft(authorized, retry_token=token), actor)
        with self._lock:
            self._jobs[ref.job_id] = (ref, actor, entrypoint)
        return ref

    def list_inbound(self, context: RequestContext) -> tuple[InboundTerminologyChangeSet, ...]:
        return self._require_inbound().list_inbound(context)

    def get_inbound(self, context: RequestContext, change_set_id: str) -> InboundTerminologyChangeSet:
        return self._require_inbound().get_inbound(context, change_set_id)

    def prepare_import_selection(
        self,
        context: RequestContext,
        change_set_id: str,
        choices: tuple[DraftImportChoice, ...],
    ) -> DraftImportSelection:
        return self._require_inbound().prepare_selection(context, change_set_id, choices)

    def preview_import(self, selection: DraftImportSelection) -> DraftImportProposal:
        proposal = self._require_inbound().preview_import(selection)
        with self._lock:
            self._proposals[proposal.proposal_digest] = proposal
            while len(self._proposals) > self._max_plans:
                self._proposals.pop(next(iter(self._proposals)))
        return proposal

    def commit_import(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult:
        return self._require_inbound().commit_import(proposal, context)

    def commit_import_ref(self, proposal_digest: str, context: RequestContext) -> DraftImportCommitResult:
        with self._lock:
            proposal = self._proposals.get(proposal_digest)
        if proposal is None:
            raise KeyError("unknown or expired inbound draft import proposal")
        return self.commit_import(proposal, context)

    def _stored(self, ref: TerminologySyncPlanRef) -> _StoredPlan:
        with self._lock:
            stored = self._plans.get(ref.plan_hash)
        if stored is None or stored.plan.plan_id != ref.plan_id:
            raise KeyError("unknown or expired terminology sync plan")
        return stored

    def _require_inbound(self) -> TerminologyInboundApplicationPort:
        if self._inbound is None:
            raise RuntimeError("inbound terminology review is not configured")
        return self._inbound

    def _entrypoint_for(self, mode: TerminologySyncMode) -> TerminologySyncTaskEntrypoint:
        if mode is TerminologySyncMode.BIDIRECTIONAL:
            if self._bidirectional_tasks is None:
                raise RuntimeError("bidirectional terminology execution is not configured")
            return self._bidirectional_tasks
        return self._tasks


def owner_from_context(context: RequestContext, *, entrypoint: str) -> OwnerRef:
    return OwnerRef(
        owner_id=context.owner_id,
        entrypoint=entrypoint,
        project_id=context.project_id,
        variant_id=context.variant_id,
        session_id=context.session_id,
        permissions=context.permissions,
    )


def _summary(plan: TerminologySyncPlan, *, execution_available: bool) -> TerminologySyncPlanSummary:
    return TerminologySyncPlanSummary(
        ref=TerminologySyncPlanRef(plan.plan_id, plan.plan_hash),
        mode=plan.mode,
        project_id=plan.local_project_id,
        variant_id=plan.local_variant_id,
        target_identity=plan.target_identity,
        counts=plan.counts,
        diagnostics=plan.diagnostics,
        blocked=plan.blocked,
        has_conflicts=plan.has_conflicts,
        destructive=plan.destructive,
        requires_confirmation=plan.requires_confirmation,
        execution_available=execution_available,
    )


def _check_owner_scope(context: RequestContext, owner: OwnerRef) -> None:
    if (
        context.owner_id != owner.owner_id
        or context.project_id != owner.project_id
        or context.variant_id != owner.variant_id
        or context.session_id != owner.session_id
    ):
        raise PermissionError("terminology sync plan belongs to another owner scope")


def _cursor_offset(request: PageRequest, plan_hash: str) -> int:
    cursor = request.cursor
    if cursor is None:
        return 0
    if cursor.snapshot_digest != plan_hash or cursor.query_fingerprint != request.query_fingerprint:
        raise ValueError("terminology sync plan cursor is stale")
    try:
        offset = int(cursor.stable_id)
    except ValueError as exc:
        raise ValueError("invalid terminology sync plan cursor") from exc
    if offset < 0:
        raise ValueError("invalid terminology sync plan cursor")
    return offset


def _mapping_request_hash(
    context: RequestContext,
    mode: TerminologySyncMode,
    preflight: TerminologySyncPreflight,
) -> str:
    target_id = "" if preflight.target is None else preflight.target.target_id
    payload = (
        f"terminology-sync-mapping\0{context.project_id}\0{context.variant_id}\0{mode.value}"
        f"\0{target_id}\0{preflight.profile_id or ''}\0{preflight.mapping_status}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "TerminologyInboundApplicationPort",
    "TerminologySyncApplicationService",
    "TerminologySyncContextPort",
    "TerminologySyncPlanRef",
    "TerminologySyncPlanSummary",
    "TerminologySyncPreflight",
    "owner_from_context",
]
