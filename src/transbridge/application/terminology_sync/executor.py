"""Safe, per-item executor for authorized terminology backup plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
from typing import Protocol

from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
)
from transbridge.application.ports.paratranz_terms import (
    ParaTranzTerminologyPort,
    ParaTranzTermWrite,
    ParaTranzTermWriteResult,
    TermWriteOperation,
    TermWriteStatus,
)
from transbridge.application.tasks import TaskCancelled

from .execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from .inbound import InboundChangeSetStorePort, InboundTerminologyChangeSet, build_inbound_change_set
from .mapping import content_equal, remote_content
from .models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncItemLink,
    TerminologySyncItemLinkUpdate,
    TerminologySyncItemOutcomeRecord,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncRunOutcome,
    TerminologySyncRunRecord,
    TerminologySyncTombstone,
)
from .plan_models import TerminologySyncAction, TerminologySyncMode, TerminologySyncPlan, TerminologySyncPlanItem
from .planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from .ports import TerminologySyncStatePort, TerminologySyncTargetBindingPort
from .use_case import AuthorizedTerminologySyncPlan, TerminologySyncPlanStaleError


class TerminologySyncFreshInputPort(Protocol):
    def load_for_plan(self, plan_hash: str) -> TerminologySyncPlannerInput: ...


@dataclass(frozen=True, slots=True)
class ExecuteTerminologyBackupRequest:
    authorized_plan: AuthorizedTerminologySyncPlan
    run_id: str
    cancellation: CancellationPort | None = None
    retry_token: TerminologySyncRetryToken | None = None

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("terminology sync run ID must not be empty")


class TerminologyBackupExecutor:
    def __init__(
        self,
        remote: ParaTranzTerminologyPort,
        state: TerminologySyncStatePort,
        fresh_inputs: TerminologySyncFreshInputPort,
        *,
        planner: TerminologySyncPlanner | None = None,
        bindings: TerminologySyncTargetBindingPort | None = None,
        clock: Callable[[], datetime] | None = None,
        inbound_store: InboundChangeSetStorePort | None = None,
    ) -> None:
        self._remote = remote
        self._state = state
        self._fresh_inputs = fresh_inputs
        self._planner = planner or TerminologySyncPlanner()
        self._bindings = bindings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._inbound_store = inbound_store

    def execute(self, request: ExecuteTerminologyBackupRequest) -> TerminologyBackupExecutionResult:
        plan = request.authorized_plan.plan
        if plan.mode not in {TerminologySyncMode.BACKUP, TerminologySyncMode.BIDIRECTIONAL}:
            raise ValueError("unsupported terminology sync mode")
        if plan.mode is TerminologySyncMode.BIDIRECTIONAL and self._inbound_store is None:
            raise RuntimeError("bidirectional terminology sync requires durable inbound storage")
        if request.authorized_plan.owner_id.strip() == "":
            raise ValueError("authorized backup plan requires an owner")
        retry = request.retry_token
        if retry is not None:
            self._validate_retry(retry, request.authorized_plan)
            if retry.unknown_item_ids:
                return self._reconcile_required_result(request, retry)
        self._require_current_binding(plan)
        current_inputs = self._fresh_inputs.load_for_plan(plan.plan_hash)
        if retry is not None and retry.confirmed_item_ids:
            self._validate_confirmed_retry_inputs(plan, current_inputs, retry)
        else:
            current_plan = self._planner.plan(current_inputs)
            if current_plan.plan_hash != plan.plan_hash:
                raise TerminologySyncPlanStaleError("terminology sync plan changed before execution")

        started_at = self._clock()
        links = {item.item_id: item for item in current_inputs.item_links}
        outcomes: list[TerminologySyncItemOutcome] = []
        updates: list[TerminologySyncItemLinkUpdate] = []
        confirmed_ids = set(() if retry is None else retry.confirmed_item_ids)
        cancelled = False
        stop_scheduling = False
        for item in plan.items:
            if item.item_id in confirmed_ids:
                outcomes.append(self._skipped(item, "ALREADY_CONFIRMED", "item was confirmed by an earlier attempt"))
                continue
            if _cancelled(request.cancellation):
                cancelled = True
                outcomes.append(self._cancelled(item))
                continue
            if stop_scheduling:
                outcomes.append(
                    self._skipped(
                        item,
                        "ABORTED_AFTER_FATAL_REMOTE_ERROR",
                        "a fatal remote error stopped further terminology requests",
                    )
                )
                continue
            if not item.action.executable_remote:
                outcomes.append(self._skipped(item, "NO_REMOTE_WRITE", "plan item has no remote side effect"))
                continue
            outcome = self._execute_item(current_inputs, item, request.cancellation)
            outcomes.append(outcome)
            if outcome.status is TerminologySyncItemStatus.SUCCEEDED:
                updates.append(self._confirmed_link_update(plan, item, outcome, links.get(item.item_id)))
            elif outcome.status is TerminologySyncItemStatus.UNKNOWN:
                updates.append(self._unknown_link_update(plan, item, outcome, links.get(item.item_id)))
            elif outcome.code in {
                "REMOTE_AUTHENTICATION",
                "REMOTE_AUTHORIZATION",
                "REMOTE_NOT_FOUND",
                "REMOTE_RATE_LIMITED",
                "REMOTE_UNAVAILABLE",
            }:
                stop_scheduling = True

        completed_at = self._clock()
        run_outcome = _run_outcome(outcomes, cancelled=cancelled)
        retry_token = self._retry_token(
            request.authorized_plan,
            outcomes,
            previous=retry,
            baseline_revision=0 if current_inputs.baseline is None else current_inputs.baseline.revision + 1,
        )
        inbound_change_set = self._build_inbound_change_set(
            plan,
            current_inputs,
            request.run_id,
            run_outcome,
            completed_at,
        )
        self._commit(
            request,
            current_inputs,
            outcomes,
            updates,
            run_outcome,
            started_at,
            completed_at,
            inbound_change_set=inbound_change_set,
        )
        return TerminologyBackupExecutionResult(
            request.run_id,
            plan.plan_hash,
            tuple(outcomes),
            retry_token=retry_token,
            reconcile_required=any(item.status is TerminologySyncItemStatus.UNKNOWN for item in outcomes),
        )

    def reconcile(self, request: ExecuteTerminologyBackupRequest) -> TerminologyBackupExecutionResult:
        """Resolve unknown write outcomes from a fresh read without repeating a write."""

        token = request.retry_token
        if token is None or not token.unknown_item_ids:
            raise ValueError("reconcile requires a retry token with unknown items")
        plan = request.authorized_plan.plan
        self._validate_retry(token, request.authorized_plan)
        self._require_current_binding(plan)
        inputs = self._fresh_inputs.load_for_plan(plan.plan_hash)
        current_baseline_revision = None if inputs.baseline is None else inputs.baseline.revision
        baseline_mismatch = current_baseline_revision != token.baseline_revision
        if (
            inputs.line.line_id != plan.line_id
            or inputs.line.target.target_id != plan.target_identity
            or inputs.local_snapshot.version_id != plan.local_version_id
            or inputs.local_snapshot.content_digest != plan.local_content_digest
            or baseline_mismatch
        ):
            raise TerminologySyncPlanStaleError("terminology sync scope changed before reconcile")
        started_at = self._clock()
        links = {item.item_id: item for item in inputs.item_links}
        plan_items = {item.item_id: item for item in plan.items}
        outcomes: list[TerminologySyncItemOutcome] = []
        updates: list[TerminologySyncItemLinkUpdate] = []
        for item_id in token.unknown_item_ids:
            item = plan_items[item_id]
            outcome = self._reconcile_item(inputs, item)
            outcomes.append(outcome)
            if outcome.status is TerminologySyncItemStatus.RECONCILED:
                updates.append(self._confirmed_link_update(plan, item, outcome, links.get(item.item_id)))
        completed_at = self._clock()
        remaining = tuple(item.item_id for item in outcomes if item.status is TerminologySyncItemStatus.UNKNOWN)
        confirmed = tuple(
            sorted(
                set(token.confirmed_item_ids)
                | {item.item_id for item in outcomes if item.status is TerminologySyncItemStatus.RECONCILED}
            )
        )
        next_token = None
        if remaining:
            next_token = TerminologySyncRetryToken(
                plan.line_id,
                plan.target_identity,
                plan.plan_hash,
                request.authorized_plan.owner_id,
                confirmed,
                remaining,
                0 if current_baseline_revision is None else current_baseline_revision + 1,
            )
        self._commit(
            request,
            inputs,
            outcomes,
            updates,
            TerminologySyncRunOutcome.UNKNOWN if remaining else TerminologySyncRunOutcome.SUCCEEDED,
            started_at,
            completed_at,
            remote_snapshot_digest=inputs.remote_snapshot.observed_digest,
        )
        return TerminologyBackupExecutionResult(
            request.run_id,
            plan.plan_hash,
            tuple(outcomes),
            retry_token=next_token,
            reconcile_required=bool(remaining),
        )

    def _reconcile_item(
        self,
        inputs: TerminologySyncPlannerInput,
        item: TerminologySyncPlanItem,
    ) -> TerminologySyncItemOutcome:
        remote_by_id = {term.remote_id: term for term in inputs.remote_snapshot.items}
        matched = None
        if item.action is TerminologySyncAction.CREATE_REMOTE and item.local is not None:
            candidates = tuple(
                term for term in inputs.remote_snapshot.items if content_equal(item.local, remote_content(term.entry))
            )
            if len(candidates) == 1:
                matched = candidates[0]
        elif item.action is TerminologySyncAction.UPDATE_REMOTE and item.remote_id is not None:
            candidate = remote_by_id.get(item.remote_id)
            if (
                candidate is not None
                and item.local is not None
                and content_equal(item.local, remote_content(candidate.entry))
            ):
                matched = candidate
        elif item.action is TerminologySyncAction.DELETE_REMOTE and item.remote_id not in remote_by_id:
            return TerminologySyncItemOutcome(
                item.item_id,
                item.action,
                TerminologySyncItemStatus.RECONCILED,
                "REMOTE_DELETE_RECONCILED",
                "fresh remote snapshot confirms the managed term is absent",
                remote_id=item.remote_id,
            )
        if matched is not None:
            return TerminologySyncItemOutcome(
                item.item_id,
                item.action,
                TerminologySyncItemStatus.RECONCILED,
                "REMOTE_WRITE_RECONCILED",
                "fresh remote snapshot confirms the intended terminology content",
                remote_id=matched.remote_id,
                remote_revision=matched.server_revision,
                remote_observed_digest=matched.observed_digest,
            )
        return TerminologySyncItemOutcome(
            item.item_id,
            item.action,
            TerminologySyncItemStatus.UNKNOWN,
            "REMOTE_OUTCOME_STILL_UNKNOWN",
            "fresh remote snapshot cannot uniquely prove the previous write outcome",
            remote_id=item.remote_id,
        )

    def _unknown_link_update(
        self,
        plan: TerminologySyncPlan,
        item: TerminologySyncPlanItem,
        outcome: TerminologySyncItemOutcome,
        previous: TerminologySyncItemLink | None,
    ) -> TerminologySyncItemLinkUpdate:
        if previous is not None:
            link = replace(
                previous,
                revision=previous.revision + 1,
                remote_revision=outcome.remote_revision or previous.remote_revision,
                remote_observed_digest=outcome.remote_observed_digest or previous.remote_observed_digest,
                last_outcome=TerminologySyncOutcome.UNKNOWN,
            )
            return TerminologySyncItemLinkUpdate(link, previous.revision)
        if item.local is None or item.local_term_id is None:
            raise ValueError("an unknown first write requires a stable local terminology anchor")
        link = TerminologySyncItemLink(
            line_id=plan.line_id,
            item_id=item.item_id,
            revision=0,
            local_term_id=item.local_term_id,
            local_version_id=plan.local_version_id,
            local_content_digest=item.local.digest,
            remote_id=outcome.remote_id or item.remote_id,
            remote_revision=outcome.remote_revision,
            remote_observed_digest=outcome.remote_observed_digest,
            common_content_digest=None,
            scope=item.local.scope,
            ownership=TerminologySyncOwnership.MANAGED,
            last_outcome=TerminologySyncOutcome.UNKNOWN,
        )
        return TerminologySyncItemLinkUpdate(link, None)

    def _execute_item(
        self,
        inputs: TerminologySyncPlannerInput,
        item: TerminologySyncPlanItem,
        cancellation: CancellationPort | None,
    ) -> TerminologySyncItemOutcome:
        try:
            remote_term = next(
                (term for term in inputs.remote_snapshot.items if term.remote_id == item.remote_id),
                None,
            )
            if item.action is TerminologySyncAction.CREATE_REMOTE:
                result = self._remote.create_term(
                    inputs.line.target.remote_project_id,
                    ParaTranzTermWrite(_entry(item), TermWriteOperation.CREATE),
                    cancellation=cancellation,
                )
            elif item.action is TerminologySyncAction.UPDATE_REMOTE:
                assert item.remote_id is not None
                result = self._remote.update_term(
                    inputs.line.target.remote_project_id,
                    ParaTranzTermWrite(
                        _entry(item),
                        TermWriteOperation.UPDATE,
                        remote_id=item.remote_id,
                        expected_revision=None if remote_term is None else remote_term.server_revision,
                        expected_digest=None if remote_term is None else remote_term.observed_digest,
                    ),
                    cancellation=cancellation,
                )
            else:
                assert item.action is TerminologySyncAction.DELETE_REMOTE
                assert item.remote_id is not None
                result = self._remote.delete_term(
                    inputs.line.target.remote_project_id,
                    item.remote_id,
                    expected_revision=None if remote_term is None else remote_term.server_revision,
                    expected_digest=None if remote_term is None else remote_term.observed_digest,
                    cancellation=cancellation,
                )
            return _remote_outcome(item, result)
        except ExternalServiceError as exc:
            unknown = exc.category in {
                ExternalServiceCategory.TIMEOUT,
                ExternalServiceCategory.TRANSPORT,
                ExternalServiceCategory.CANCELLED,
            }
            return TerminologySyncItemOutcome(
                item.item_id,
                item.action,
                TerminologySyncItemStatus.UNKNOWN if unknown else TerminologySyncItemStatus.FAILED,
                f"REMOTE_{exc.category.value.upper()}",
                str(exc),
                remote_id=item.remote_id,
                request_id=exc.request_id,
            )
        except TaskCancelled as exc:
            return TerminologySyncItemOutcome(
                item.item_id,
                item.action,
                TerminologySyncItemStatus.UNKNOWN,
                "REMOTE_CANCELLED",
                f"remote write cancellation crossed the dispatch boundary: {exc}",
                remote_id=item.remote_id,
            )

    def _require_current_binding(self, plan: TerminologySyncPlan) -> None:
        if self._bindings is None:
            return
        current = self._bindings.resolve_target_binding(plan.local_project_id)
        if (
            current is None
            or current.project_id != plan.local_project_id
            or current.target.target_id != plan.target_identity
            or current.revision != plan.binding_revision
        ):
            raise TerminologySyncPlanStaleError("terminology sync target binding changed before execution")

    def _confirmed_link_update(
        self,
        plan: TerminologySyncPlan,
        item: TerminologySyncPlanItem,
        outcome: TerminologySyncItemOutcome,
        previous: TerminologySyncItemLink | None,
    ) -> TerminologySyncItemLinkUpdate:
        if item.action is TerminologySyncAction.DELETE_REMOTE:
            if previous is None:
                raise ValueError("managed delete requires an existing sync item link")
            link = replace(
                previous,
                revision=previous.revision + 1,
                remote_revision=outcome.remote_revision or previous.remote_revision,
                remote_observed_digest=outcome.remote_observed_digest or previous.remote_observed_digest,
                tombstone=TerminologySyncTombstone.BOTH_DELETED,
                last_outcome=TerminologySyncOutcome.CONFIRMED,
            )
            return TerminologySyncItemLinkUpdate(link, previous.revision)
        assert item.local is not None
        remote_id = outcome.remote_id or item.remote_id
        assert remote_id is not None
        revision = 0 if previous is None else previous.revision + 1
        link = TerminologySyncItemLink(
            line_id=plan.line_id,
            item_id=item.item_id,
            revision=revision,
            local_term_id=item.local_term_id,
            local_version_id=plan.local_version_id,
            local_content_digest=item.local.digest,
            remote_id=remote_id,
            remote_revision=outcome.remote_revision,
            remote_observed_digest=outcome.remote_observed_digest or item.local.digest,
            common_content_digest=item.local.digest,
            scope=item.local.scope,
            ownership=TerminologySyncOwnership.MANAGED,
            tombstone=TerminologySyncTombstone.LIVE,
            last_outcome=TerminologySyncOutcome.CONFIRMED,
        )
        return TerminologySyncItemLinkUpdate(link, None if previous is None else previous.revision)

    def _commit(
        self,
        request: ExecuteTerminologyBackupRequest,
        inputs: TerminologySyncPlannerInput,
        outcomes: list[TerminologySyncItemOutcome],
        updates: list[TerminologySyncItemLinkUpdate],
        run_outcome: TerminologySyncRunOutcome,
        started_at: datetime,
        completed_at: datetime,
        remote_snapshot_digest: str | None = None,
        inbound_change_set: InboundTerminologyChangeSet | None = None,
    ) -> None:
        plan = request.authorized_plan.plan
        current_revision = None if inputs.baseline is None else inputs.baseline.revision
        baseline = TerminologySyncBaseline(
            plan.line_id,
            0 if current_revision is None else current_revision + 1,
            plan.local_version_id,
            plan.local_content_digest,
            remote_snapshot_digest or plan.remote_snapshot_digest,
            _common_snapshot_digest(updates, inputs.item_links),
            request.run_id,
        )
        run = TerminologySyncRunRecord(
            request.run_id,
            plan.line_id,
            plan.plan_id,
            request.authorized_plan.owner_id,
            plan.target_identity,
            current_revision,
            run_outcome,
            started_at.isoformat(),
            completed_at.isoformat(),
        )
        records = tuple(
            TerminologySyncItemOutcomeRecord(
                outcome_id=_outcome_id(request.run_id, item.item_id, item.attempt),
                run_id=request.run_id,
                line_id=plan.line_id,
                item_id=item.item_id,
                status=_stored_status(item.status),
                code=item.code,
                message=item.message,
                recorded_at=completed_at.isoformat(),
            )
            for item in outcomes
        )
        commit = TerminologySyncCommit(run, records, baseline, tuple(updates))
        atomic_commit = getattr(self._state, "commit_run_with_inbound", None)
        if inbound_change_set is not None and callable(atomic_commit):
            atomic_commit(
                commit,
                inbound_change_set,
                expected_baseline_revision=current_revision,
            )
            return
        self._state.commit_run(commit, expected_baseline_revision=current_revision)
        if inbound_change_set is not None:
            # Non-SQLite test adapters may not expose the composite unit of
            # work; production state always takes the atomic branch above.
            self._inbound_store.save_change_set(inbound_change_set)

    @staticmethod
    def _build_inbound_change_set(
        plan: TerminologySyncPlan,
        inputs: TerminologySyncPlannerInput,
        run_id: str,
        run_outcome: TerminologySyncRunOutcome,
        completed_at: datetime,
    ) -> InboundTerminologyChangeSet | None:
        if plan.mode is not TerminologySyncMode.BIDIRECTIONAL:
            return None
        inbound_actions = {
            TerminologySyncAction.PROPOSE_LOCAL_ADD,
            TerminologySyncAction.PROPOSE_LOCAL_UPDATE,
            TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION,
            TerminologySyncAction.CONFLICT,
        }
        if not any(item.action in inbound_actions for item in plan.items):
            return None
        return build_inbound_change_set(
            plan,
            inputs.remote_snapshot,
            source_run_id=run_id,
            source_run_outcome=run_outcome,
            created_at=completed_at,
        )

    def _retry_token(
        self,
        authorized: AuthorizedTerminologySyncPlan,
        outcomes: list[TerminologySyncItemOutcome],
        *,
        previous: TerminologySyncRetryToken | None,
        baseline_revision: int,
    ) -> TerminologySyncRetryToken | None:
        confirmed = tuple(
            sorted(
                set(() if previous is None else previous.confirmed_item_ids)
                | {item.item_id for item in outcomes if item.status is TerminologySyncItemStatus.SUCCEEDED}
            )
        )
        unknown = tuple(item.item_id for item in outcomes if item.status is TerminologySyncItemStatus.UNKNOWN)
        failed = any(
            item.status in {TerminologySyncItemStatus.FAILED, TerminologySyncItemStatus.CANCELLED} for item in outcomes
        )
        if not unknown and not failed:
            return None
        plan = authorized.plan
        return TerminologySyncRetryToken(
            plan.line_id,
            plan.target_identity,
            plan.plan_hash,
            authorized.owner_id,
            confirmed,
            unknown,
            baseline_revision,
        )

    @staticmethod
    def _validate_retry(token: TerminologySyncRetryToken, authorized: AuthorizedTerminologySyncPlan) -> None:
        plan = authorized.plan
        expected = (plan.line_id, plan.target_identity, plan.plan_hash, authorized.owner_id)
        actual = (token.line_id, token.target_identity, token.plan_hash, token.owner_id)
        if actual != expected or token.compute_digest() != token.token_digest:
            raise ValueError("retry token does not belong to the authorized terminology sync plan")

    @staticmethod
    def _validate_confirmed_retry_inputs(
        plan: TerminologySyncPlan,
        inputs: TerminologySyncPlannerInput,
        token: TerminologySyncRetryToken,
    ) -> None:
        if (
            inputs.line.line_id != plan.line_id
            or inputs.line.target.target_id != plan.target_identity
            or inputs.profile.mode is not plan.mode
            or inputs.profile.revision != plan.profile_revision
            or inputs.local_snapshot.version_id != plan.local_version_id
            or inputs.local_snapshot.content_digest != plan.local_content_digest
            or (None if inputs.baseline is None else inputs.baseline.revision) != token.baseline_revision
        ):
            raise TerminologySyncPlanStaleError("terminology sync scope changed before retry")
        links = {item.item_id: item for item in inputs.item_links}
        if any(
            item_id not in links
            or links[item_id].last_outcome not in {TerminologySyncOutcome.CONFIRMED, TerminologySyncOutcome.RECONCILED}
            for item_id in token.confirmed_item_ids
        ):
            raise TerminologySyncPlanStaleError("confirmed retry outcomes are not durable")
        remote_by_id = {term.remote_id: term for term in inputs.remote_snapshot.items}
        for item in plan.items:
            if item.item_id in token.confirmed_item_ids or not item.action.executable_remote:
                continue
            if item.action is TerminologySyncAction.CREATE_REMOTE:
                if item.local is not None and any(
                    content_equal(item.local, remote_content(term.entry)) for term in inputs.remote_snapshot.items
                ):
                    raise TerminologySyncPlanStaleError("unconfirmed create now has a matching remote term")
                continue
            current = remote_by_id.get(item.remote_id)
            if current is None or item.remote is None or not content_equal(item.remote, remote_content(current.entry)):
                raise TerminologySyncPlanStaleError("an unconfirmed remote item changed before retry")

    @staticmethod
    def _reconcile_required_result(
        request: ExecuteTerminologyBackupRequest,
        token: TerminologySyncRetryToken,
    ) -> TerminologyBackupExecutionResult:
        plan = request.authorized_plan.plan
        outcomes = tuple(
            TerminologySyncItemOutcome(
                item_id,
                next(item.action for item in plan.items if item.item_id == item_id),
                TerminologySyncItemStatus.UNKNOWN,
                "RECONCILE_REQUIRED",
                "unknown remote outcome must be reconciled before retry",
            )
            for item_id in token.unknown_item_ids
        )
        return TerminologyBackupExecutionResult(
            request.run_id,
            plan.plan_hash,
            outcomes,
            retry_token=token,
            reconcile_required=True,
        )

    @staticmethod
    def _skipped(item: TerminologySyncPlanItem, code: str, message: str) -> TerminologySyncItemOutcome:
        return TerminologySyncItemOutcome(
            item.item_id,
            item.action,
            TerminologySyncItemStatus.SKIPPED,
            code,
            message,
            remote_id=item.remote_id,
        )

    @staticmethod
    def _cancelled(item: TerminologySyncPlanItem) -> TerminologySyncItemOutcome:
        return TerminologySyncItemOutcome(
            item.item_id,
            item.action,
            TerminologySyncItemStatus.CANCELLED,
            "CANCELLED",
            "terminology sync was cancelled before this item started",
            remote_id=item.remote_id,
        )


def _entry(item: TerminologySyncPlanItem) -> TermEntry:
    if item.local is None:
        raise ValueError("remote create/update requires local terminology content")
    return TermEntry(
        term=item.local.original,
        translation=item.local.translation,
        source="transbridge_project",
        case_sensitive=item.local.case_sensitive,
        variants=list(item.local.variants),
        pos=item.local.part_of_speech,
        note=item.local.note,
    )


def _remote_outcome(item: TerminologySyncPlanItem, result: ParaTranzTermWriteResult) -> TerminologySyncItemOutcome:
    status = (
        TerminologySyncItemStatus.SUCCEEDED
        if result.status is TermWriteStatus.CONFIRMED
        else TerminologySyncItemStatus.UNKNOWN
    )
    return TerminologySyncItemOutcome(
        item.item_id,
        item.action,
        status,
        "REMOTE_CONFIRMED" if status is TerminologySyncItemStatus.SUCCEEDED else "REMOTE_OUTCOME_UNKNOWN",
        "remote terminology operation confirmed"
        if status is TerminologySyncItemStatus.SUCCEEDED
        else "remote outcome unknown",
        remote_id=result.remote_id or item.remote_id,
        remote_revision=result.server_revision,
        remote_observed_digest=result.observed_digest,
        request_id=result.request_id,
    )


def _run_outcome(
    outcomes: list[TerminologySyncItemOutcome],
    *,
    cancelled: bool,
) -> TerminologySyncRunOutcome:
    statuses = {item.status for item in outcomes}
    if TerminologySyncItemStatus.UNKNOWN in statuses:
        return TerminologySyncRunOutcome.UNKNOWN
    if cancelled:
        return TerminologySyncRunOutcome.CANCELLED
    if TerminologySyncItemStatus.FAILED in statuses:
        succeeded = TerminologySyncItemStatus.SUCCEEDED in statuses
        return TerminologySyncRunOutcome.PARTIAL if succeeded else TerminologySyncRunOutcome.FAILED
    return TerminologySyncRunOutcome.SUCCEEDED


def _stored_status(status: TerminologySyncItemStatus) -> TerminologySyncOutcome:
    return {
        TerminologySyncItemStatus.SUCCEEDED: TerminologySyncOutcome.CONFIRMED,
        TerminologySyncItemStatus.RECONCILED: TerminologySyncOutcome.RECONCILED,
        TerminologySyncItemStatus.UNKNOWN: TerminologySyncOutcome.UNKNOWN,
        TerminologySyncItemStatus.FAILED: TerminologySyncOutcome.FAILED,
        TerminologySyncItemStatus.SKIPPED: TerminologySyncOutcome.CONFIRMED,
        TerminologySyncItemStatus.CANCELLED: TerminologySyncOutcome.FAILED,
    }[status]


def _cancelled(cancellation: CancellationPort | None) -> bool:
    return cancellation is not None and cancellation.is_cancelled


def _outcome_id(run_id: str, item_id: str, attempt: int) -> str:
    return hashlib.sha256(f"{run_id}\0{item_id}\0{attempt}".encode()).hexdigest()


def _common_snapshot_digest(
    updates: list[TerminologySyncItemLinkUpdate],
    existing: tuple[TerminologySyncItemLink, ...],
) -> str:
    by_id = {item.item_id: item for item in existing}
    by_id.update((item.link.item_id, item.link) for item in updates)
    payload = "\n".join(
        f"{item_id}:{item.common_content_digest or '-'}:{item.tombstone.value}"
        for item_id, item in sorted(by_id.items())
        if item.common_content_digest is not None
        and item.last_outcome in {TerminologySyncOutcome.CONFIRMED, TerminologySyncOutcome.RECONCILED}
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "ExecuteTerminologyBackupRequest",
    "TerminologyBackupExecutor",
    "TerminologySyncFreshInputPort",
]
