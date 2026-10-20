"""Decode ownerless local histories without changing their saved workspace scope."""

from dataclasses import replace

from transbridge.application.contracts import RequestContext
from transbridge.application.tasks.models import OwnerRef
from transbridge.persistence.v2.ids import SessionRef
from transbridge.persistence.v2.models import SessionDto

from .models import RecoveryStatus, SessionSnapshot


def session_snapshot_for_context(dto: SessionDto, ref: SessionRef, context: RequestContext) -> SessionSnapshot:
    """Apply the existing ownerless-history compatibility rule entirely in memory.

    The repository must validate/migrate the DTO before calling this function.
    Explicit saved owners remain authoritative, even if an old diagnostic claims
    their scope is missing. Callers still enforce the resulting owner and scope.
    """
    snapshot = SessionSnapshot.from_dto(dto, ref)
    if isinstance(dto.envelope.data.get("owner"), dict):
        return snapshot
    owner = OwnerRef(
        context.owner_id,
        "legacy-session-migration",
        None if snapshot.project_id is None else snapshot.project_id.value,
        None if snapshot.variant_id is None else snapshot.variant_id.value,
        snapshot.ref.identity.value,
        context.permissions,
    )
    return replace(
        snapshot,
        owner=owner,
        recovery=RecoveryStatus.DEGRADED,
        degradation_reasons=tuple(reason for reason in snapshot.degradation_reasons if reason != "owner_scope_missing")
        + ("owner_scope_inferred_from_request",),
    )
