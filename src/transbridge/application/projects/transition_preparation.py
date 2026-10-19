"""Prepare an isolated lifecycle candidate and own its provisional leases.

The lifecycle invokes this slice while holding its state lock. No active state,
projection, or persistence pointer is published here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext

from .models import (
    ActiveProject,
    LifecycleActivation,
    LifecycleLease,
    PreparedTransition,
    TransitionTarget,
    project_with_active_variant,
)
from .ports import CandidateLoaderPort, LifecycleLeasePort

type ActiveSignature = tuple[str, int, str | None, int | None] | None


@dataclass(frozen=True, slots=True)
class PreparedState:
    public: PreparedTransition
    candidate: ActiveProject | None
    leases: tuple[LifecycleLease, ...]
    old_signature: ActiveSignature
    activation: LifecycleActivation | None = None


def prepare_transition_candidate(
    target: TransitionTarget,
    context: RequestContext,
    *,
    loader: CandidateLoaderPort,
    leases: LifecycleLeasePort,
    active: ActiveProject | None,
    generation: int,
    create_token: Callable[[], str],
    prepare_candidate: Callable[[ActiveProject | None], None] | None = None,
) -> PreparedState:
    """Return a fully prepared candidate or release every provisional lease."""
    acquired: tuple[LifecycleLease, ...] = ()
    try:
        acquired = leases.acquire(target, context)
        if any(lease.owner_id != context.owner_id for lease in acquired):
            raise DomainError(
                ErrorCategory.PERMISSION,
                "LIFECYCLE_LEASE_OWNER_MISMATCH",
                "A lifecycle lease was issued to a different owner.",
            )
        candidate = None if target.project_ref is None else loader.prepare_candidate(target, context)
        candidate = _validate_candidate(target, candidate, acquired)
        if prepare_candidate is not None:
            prepare_candidate(candidate)
        token = create_token()
        public: PreparedTransition = {
            "token": token,
            "owner_id": context.owner_id,
            "expected_generation": generation,
            "old": None if active is None else active.summary(),
            "candidate": None if candidate is None else candidate.summary(),
            "target": target.to_dict(),
            "leases": [lease.lease_id for lease in acquired],
        }
        return PreparedState(public, candidate, acquired, active_signature(active))
    except Exception:
        release_leases(leases, acquired)
        raise


def _validate_candidate(
    target: TransitionTarget,
    candidate: ActiveProject | None,
    leases: tuple[LifecycleLease, ...],
) -> ActiveProject | None:
    if target.project_ref is None:
        if candidate is not None:
            raise ValueError("close transition must not materialize a candidate")
        return None
    if candidate is None or candidate.project_ref != target.project_ref:
        raise ValueError("candidate Project does not match the transition target")
    if candidate.formal_variant_ref != target.variant_ref:
        raise ValueError("candidate Variant does not match the transition target")
    if target.snapshot_ref is not None and candidate.source_ref != target.snapshot_ref:
        raise ValueError("snapshot candidate does not retain its read-only source reference")
    if candidate.leases:
        raise ValueError("candidate loader must not retain hidden lifecycle leases")
    project = project_with_active_variant(candidate.project, target.variant_ref)
    return replace(candidate, project=project, source_ref=target.snapshot_ref, leases=leases)


def release_leases(port: LifecycleLeasePort, leases: tuple[LifecycleLease, ...]) -> bool:
    """Best-effort cleanup that does not replace the original operation error."""
    if not leases:
        return False
    try:
        port.release(leases)
    except Exception:  # noqa: BLE001 - the caller can surface release failure as a warning
        return True
    return False


def active_signature(active: ActiveProject | None) -> ActiveSignature:
    if active is None:
        return None
    return (
        active.project_ref.identity.value,
        active.project.envelope.revision,
        None if active.formal_variant_ref is None else active.formal_variant_ref.identity.value,
        None if active.variant is None else active.variant.revision,
    )


__all__ = ["ActiveSignature", "PreparedState", "active_signature", "prepare_transition_candidate", "release_leases"]
