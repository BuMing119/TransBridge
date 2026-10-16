"""Retry evidence and freshness checks for interrupted terminology synchronization."""

from __future__ import annotations

from dataclasses import replace

from transbridge.application.terminology.effective import EffectiveSnapshotStatus

from .execution_models import TerminologySyncItemOutcome, TerminologySyncItemStatus, TerminologySyncRetryToken
from .mapping import content_equal, remote_content
from .models import TerminologySyncOutcome
from .plan_models import TerminologySyncAction, TerminologySyncPlan
from .planner import TerminologySyncPlanner, TerminologySyncPlannerInput
from .use_case import AuthorizedTerminologySyncPlan, TerminologySyncPlanStaleError


def build_retry_token(
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


def validate_retry_token(token: TerminologySyncRetryToken, authorized: AuthorizedTerminologySyncPlan) -> None:
    plan = authorized.plan
    expected = (plan.line_id, plan.target_identity, plan.plan_hash, authorized.owner_id)
    actual = (token.line_id, token.target_identity, token.plan_hash, token.owner_id)
    if actual != expected or token.compute_digest() != token.token_digest:
        raise ValueError("retry token does not belong to the authorized terminology sync plan")


def validate_retry_inputs(
    plan: TerminologySyncPlan,
    inputs: TerminologySyncPlannerInput,
    token: TerminologySyncRetryToken,
    planner: TerminologySyncPlanner,
) -> None:
    """Allow this attempt's durable progress while rejecting changed pending work."""
    if (
        not inputs.line.active
        or inputs.line.line_id != plan.line_id
        or inputs.line.target.target_id != plan.target_identity
        or inputs.binding_revision != plan.binding_revision
        or inputs.profile.line_id != plan.line_id
        or inputs.profile.mode is not plan.mode
        or inputs.profile.revision != plan.profile_revision
        or inputs.local_snapshot.local_project_id != plan.local_project_id
        or inputs.local_snapshot.local_variant_id != plan.local_variant_id
        or inputs.local_snapshot.status is not EffectiveSnapshotStatus.READY
        or inputs.local_snapshot.version_id != plan.local_version_id
        or inputs.local_snapshot.content_digest != plan.local_content_digest
        or not inputs.remote_snapshot.stable
        or inputs.remote_snapshot.project_id != inputs.line.target.remote_project_id
        or inputs.variant_mapping_conflict
        or (None if inputs.baseline is None else inputs.baseline.revision) != token.baseline_revision
    ):
        raise TerminologySyncPlanStaleError("terminology sync scope changed before retry")

    if not token.confirmed_item_ids:
        # Even a failed run records a new baseline revision. With no successful
        # items that revision is the only allowed difference from the exact plan.
        current = planner.plan(inputs)
        comparable = replace(current, baseline_revision=plan.baseline_revision, plan_hash="")
        if comparable.plan_hash != plan.plan_hash:
            raise TerminologySyncPlanStaleError("terminology sync inputs changed before retry")
        return

    links = {item.item_id: item for item in inputs.item_links}
    if any(
        item_id not in links
        or links[item_id].last_outcome not in {TerminologySyncOutcome.CONFIRMED, TerminologySyncOutcome.RECONCILED}
        for item_id in token.confirmed_item_ids
    ):
        raise TerminologySyncPlanStaleError("confirmed retry outcomes are not durable")
    remote_by_id = {term.remote_id: term for term in inputs.remote_snapshot.items}
    for item in plan.items:
        if item.item_id in token.confirmed_item_ids:
            continue
        if not item.action.executable_remote and item.action is not TerminologySyncAction.ADOPT_LINK:
            continue
        if item.action is TerminologySyncAction.CREATE_REMOTE:
            if item.local is not None and any(
                remote_content(term.entry).normalized_original == item.local.normalized_original
                for term in inputs.remote_snapshot.items
            ):
                raise TerminologySyncPlanStaleError("unconfirmed create now has a matching remote term")
            continue
        current = remote_by_id.get(item.remote_id)
        if current is None or item.remote is None or not content_equal(item.remote, remote_content(current.entry)):
            raise TerminologySyncPlanStaleError("an unconfirmed remote item changed before retry")


__all__ = ["build_retry_token", "validate_retry_inputs", "validate_retry_token"]
