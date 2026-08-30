"""Pure three-way planner for project terminology and ParaTranz terms."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from transbridge.application.ports.paratranz_terms import ParaTranzTerm, ParaTranzTermSnapshot
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import TermDecision

from .mapping import content_equal, local_content, lossy_mapping_reason, remote_content
from .models import (
    TerminologyDeletePolicy,
    TerminologySyncBaseline,
    TerminologySyncItemLink,
    TerminologySyncLine,
    TerminologySyncOutcome,
    TerminologySyncOwnership,
    TerminologySyncProfile,
    TerminologySyncTombstone,
)
from .plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncMode,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
    TerminologySyncReason,
    stable_plan_item_id,
)


class TerminologySyncPlanningError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TerminologySyncPlannerInput:
    line: TerminologySyncLine
    profile: TerminologySyncProfile
    local_snapshot: EffectiveTerminologySnapshot
    remote_snapshot: ParaTranzTermSnapshot
    baseline: TerminologySyncBaseline | None = None
    item_links: tuple[TerminologySyncItemLink, ...] = ()
    binding_revision: int | None = None
    variant_mapping_conflict: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_links", tuple(self.item_links))


class TerminologySyncPlanner:
    """Classify local/remote/base state without performing any side effect."""

    def plan(self, inputs: TerminologySyncPlannerInput) -> TerminologySyncPlan:
        self._validate_inputs(inputs)
        if inputs.variant_mapping_conflict:
            return self._blocked_plan(inputs, TerminologySyncReason.VARIANT_MAPPING_CONFLICT)
        if not inputs.remote_snapshot.stable:
            return self._blocked_plan(inputs, TerminologySyncReason.REMOTE_SNAPSHOT_UNSTABLE)

        local_by_id = {decision.term_id: decision for decision in inputs.local_snapshot.decisions}
        remote_by_id = {term.remote_id: term for term in inputs.remote_snapshot.items}
        duplicate_items = self._duplicate_link_items(inputs, local_by_id, remote_by_id)
        if duplicate_items:
            return self._make_plan(inputs, duplicate_items, diagnostics=("duplicate sync baseline identity",))

        items: list[TerminologySyncPlanItem] = []
        for link in sorted(inputs.item_links, key=lambda item: item.item_id):
            local = local_by_id.pop(link.local_term_id, None) if link.local_term_id is not None else None
            remote = remote_by_id.pop(link.remote_id, None) if link.remote_id is not None else None
            items.append(self._linked_item(inputs, link, local, remote))

        remaining_local = tuple(sorted(local_by_id.values(), key=lambda item: item.term_id))
        remaining_remote = tuple(sorted(remote_by_id.values(), key=lambda item: item.remote_id))
        items.extend(self._unlinked_items(inputs, remaining_local, remaining_remote))
        return self._make_plan(inputs, items)

    def _validate_inputs(self, inputs: TerminologySyncPlannerInput) -> None:
        line = inputs.line
        profile = inputs.profile
        local = inputs.local_snapshot
        remote = inputs.remote_snapshot
        if not line.active:
            raise TerminologySyncPlanningError("SYNC_LINE_RETIRED", "retired terminology sync lines are read-only")
        if profile.line_id != line.line_id:
            raise TerminologySyncPlanningError("PROFILE_LINE_MISMATCH", "sync profile belongs to another line")
        if (local.local_project_id, local.local_variant_id) != (line.project_id, line.variant_id):
            raise TerminologySyncPlanningError(
                "LOCAL_SCOPE_MISMATCH",
                "effective terminology snapshot belongs to another Project/Variant",
            )
        if local.status is not EffectiveSnapshotStatus.READY:
            raise TerminologySyncPlanningError(
                "LOCAL_VERSION_UNAVAILABLE",
                f"published terminology snapshot is not ready: {local.status.value}",
            )
        if remote.project_id != line.target.remote_project_id:
            raise TerminologySyncPlanningError("REMOTE_TARGET_MISMATCH", "remote snapshot belongs to another project")
        if inputs.baseline is not None and inputs.baseline.line_id != line.line_id:
            raise TerminologySyncPlanningError("BASELINE_LINE_MISMATCH", "sync baseline belongs to another line")
        if any(link.line_id != line.line_id for link in inputs.item_links):
            raise TerminologySyncPlanningError("LINK_LINE_MISMATCH", "sync item link belongs to another line")

    def _blocked_plan(
        self,
        inputs: TerminologySyncPlannerInput,
        reason: TerminologySyncReason,
    ) -> TerminologySyncPlan:
        item = TerminologySyncPlanItem(
            item_id=stable_plan_item_id(
                line_id=inputs.line.line_id,
                local_term_id=None,
                remote_id=None,
                base_digest=reason.value,
            ),
            action=TerminologySyncAction.BLOCKED,
            reason=reason,
            requires_review=True,
        )
        return self._make_plan(inputs, (item,), diagnostics=(reason.value,))

    def _duplicate_link_items(
        self,
        inputs: TerminologySyncPlannerInput,
        local_by_id: dict[str, TermDecision],
        remote_by_id: dict[int, ParaTranzTerm],
    ) -> tuple[TerminologySyncPlanItem, ...]:
        seen_local: set[str] = set()
        seen_remote: set[int] = set()
        items: list[TerminologySyncPlanItem] = []
        for link in sorted(inputs.item_links, key=lambda item: item.item_id):
            duplicate_local = link.local_term_id is not None and link.local_term_id in seen_local
            duplicate_remote = link.remote_id is not None and link.remote_id in seen_remote
            if link.local_term_id is not None:
                seen_local.add(link.local_term_id)
            if link.remote_id is not None:
                seen_remote.add(link.remote_id)
            if not duplicate_local and not duplicate_remote:
                continue
            reason = (
                TerminologySyncReason.DUPLICATE_LOCAL_IDENTITY
                if duplicate_local
                else TerminologySyncReason.DUPLICATE_REMOTE_IDENTITY
            )
            local = local_by_id.get(link.local_term_id) if link.local_term_id is not None else None
            remote = remote_by_id.get(link.remote_id) if link.remote_id is not None else None
            items.append(
                self._item(
                    inputs,
                    action=TerminologySyncAction.BLOCKED,
                    reason=reason,
                    local_term_id=link.local_term_id,
                    remote_id=link.remote_id,
                    base_digest=link.common_content_digest,
                    local=None if local is None else local_content(local),
                    remote=None if remote is None else remote_content(remote.entry),
                    managed=link.ownership is TerminologySyncOwnership.MANAGED,
                    requires_review=True,
                )
            )
        return tuple(items)

    def _linked_item(
        self,
        inputs: TerminologySyncPlannerInput,
        link: TerminologySyncItemLink,
        local: TermDecision | None,
        remote: ParaTranzTerm | None,
    ) -> TerminologySyncPlanItem:
        local_summary = None if local is None else local_content(local)
        remote_summary = None if remote is None else remote_content(remote.entry)
        managed = link.ownership is TerminologySyncOwnership.MANAGED
        if link.last_outcome is TerminologySyncOutcome.UNKNOWN:
            return self._item(
                inputs,
                action=TerminologySyncAction.CONFLICT,
                reason=TerminologySyncReason.UNKNOWN_OUTCOME,
                local_term_id=link.local_term_id,
                remote_id=link.remote_id,
                base_digest=link.common_content_digest,
                local=local_summary,
                remote=remote_summary,
                managed=managed,
                requires_review=True,
            )
        if link.remote_id is None:
            return self._item(
                inputs,
                action=TerminologySyncAction.BLOCKED,
                reason=TerminologySyncReason.REMOTE_ID_MISSING,
                local_term_id=link.local_term_id,
                base_digest=link.common_content_digest,
                local=local_summary,
                managed=managed,
                requires_review=True,
            )
        remote_identity_reused = remote is not None and (
            link.tombstone is not TerminologySyncTombstone.LIVE
            or (
                local_summary is not None
                and remote_summary is not None
                and local_summary.normalized_original != remote_summary.normalized_original
            )
        )
        if remote_identity_reused:
            return self._item(
                inputs,
                action=TerminologySyncAction.BLOCKED,
                reason=TerminologySyncReason.REMOTE_ID_REUSED,
                local_term_id=link.local_term_id,
                remote_id=link.remote_id,
                base_digest=link.common_content_digest,
                local=local_summary,
                remote=remote_summary,
                managed=managed,
                requires_review=True,
            )
        if local is not None and (lossy := lossy_mapping_reason(local)) is not None:
            return self._item(
                inputs,
                action=TerminologySyncAction.LOSSY_MAPPING,
                reason=lossy,
                local_term_id=link.local_term_id,
                remote_id=link.remote_id,
                base_digest=link.common_content_digest,
                local=local_summary,
                remote=remote_summary,
                managed=managed,
                requires_review=True,
            )
        action, reason, review = self._classify_linked(
            mode=inputs.profile.mode,
            delete_policy=inputs.profile.delete_policy,
            base_digest=link.common_content_digest,
            local=local_summary,
            remote=remote_summary,
            managed=managed,
        )
        return self._item(
            inputs,
            action=action,
            reason=reason,
            local_term_id=link.local_term_id,
            remote_id=link.remote_id,
            base_digest=link.common_content_digest,
            local=local_summary,
            remote=remote_summary,
            managed=managed,
            requires_review=review,
        )

    def _classify_linked(
        self,
        *,
        mode: TerminologySyncMode,
        delete_policy: TerminologyDeletePolicy,
        base_digest: str | None,
        local: TerminologyContentSummary | None,
        remote: TerminologyContentSummary | None,
        managed: bool,
    ) -> tuple[TerminologySyncAction, TerminologySyncReason, bool]:
        if base_digest is None:
            return TerminologySyncAction.CONFLICT, TerminologySyncReason.UNKNOWN_OUTCOME, True
        if local is None and remote is None:
            return TerminologySyncAction.SKIP, TerminologySyncReason.BOTH_DELETED, False
        if local is None:
            remote_changed = remote is not None and remote.digest != base_digest
            if remote_changed:
                return TerminologySyncAction.CONFLICT, TerminologySyncReason.BOTH_CHANGED, True
            if managed and delete_policy is TerminologyDeletePolicy.MANAGED_ONLY:
                return TerminologySyncAction.DELETE_REMOTE, TerminologySyncReason.LOCAL_DELETED, True
            return TerminologySyncAction.SKIP, TerminologySyncReason.INDEPENDENT_REMOTE, False
        if remote is None:
            local_changed = local.digest != base_digest
            if mode is TerminologySyncMode.BIDIRECTIONAL:
                if local_changed:
                    return TerminologySyncAction.CONFLICT, TerminologySyncReason.BOTH_CHANGED, True
                return TerminologySyncAction.PROPOSE_LOCAL_SUPPRESSION, TerminologySyncReason.REMOTE_DELETED, True
            return TerminologySyncAction.CREATE_REMOTE, TerminologySyncReason.REMOTE_DELETED, True
        local_changed = local.digest != base_digest
        remote_changed = remote.digest != base_digest
        if not local_changed and not remote_changed:
            return TerminologySyncAction.SKIP, TerminologySyncReason.UNCHANGED_ECHO, False
        if content_equal(local, remote):
            return TerminologySyncAction.SKIP, TerminologySyncReason.UNCHANGED_ECHO, False
        if local_changed and remote_changed:
            return TerminologySyncAction.CONFLICT, TerminologySyncReason.BOTH_CHANGED, True
        if local_changed:
            return TerminologySyncAction.UPDATE_REMOTE, TerminologySyncReason.LOCAL_CHANGED, True
        if mode is TerminologySyncMode.BIDIRECTIONAL:
            return TerminologySyncAction.PROPOSE_LOCAL_UPDATE, TerminologySyncReason.REMOTE_CHANGED, True
        return TerminologySyncAction.UPDATE_REMOTE, TerminologySyncReason.REMOTE_CHANGED, True

    def _unlinked_items(
        self,
        inputs: TerminologySyncPlannerInput,
        local_decisions: Iterable[TermDecision],
        remote_terms: Iterable[ParaTranzTerm],
    ) -> tuple[TerminologySyncPlanItem, ...]:
        local_groups: dict[str, list[TermDecision]] = {}
        remote_groups: dict[str, list[ParaTranzTerm]] = {}
        for decision in local_decisions:
            summary = local_content(decision)
            local_groups.setdefault(summary.normalized_original, []).append(decision)
        for term in remote_terms:
            summary = remote_content(term.entry)
            remote_groups.setdefault(summary.normalized_original, []).append(term)

        items: list[TerminologySyncPlanItem] = []
        for normalized in sorted(set(local_groups) | set(remote_groups)):
            locals_for_term = sorted(local_groups.get(normalized, ()), key=lambda item: item.term_id)
            remotes_for_term = sorted(remote_groups.get(normalized, ()), key=lambda item: item.remote_id)
            if len(locals_for_term) > 1 or len(remotes_for_term) > 1:
                reason = (
                    TerminologySyncReason.DUPLICATE_LOCAL_IDENTITY
                    if len(locals_for_term) > 1
                    else TerminologySyncReason.DUPLICATE_REMOTE_IDENTITY
                )
                for decision in locals_for_term or (None,):
                    for term in remotes_for_term or (None,):
                        items.append(
                            self._item(
                                inputs,
                                action=TerminologySyncAction.CONFLICT,
                                reason=reason,
                                local_term_id=None if decision is None else decision.term_id,
                                remote_id=None if term is None else term.remote_id,
                                local=None if decision is None else local_content(decision),
                                remote=None if term is None else remote_content(term.entry),
                                requires_review=True,
                            )
                        )
                continue
            decision = locals_for_term[0] if locals_for_term else None
            term = remotes_for_term[0] if remotes_for_term else None
            local_summary = None if decision is None else local_content(decision)
            remote_summary = None if term is None else remote_content(term.entry)
            if decision is not None and (lossy := lossy_mapping_reason(decision)) is not None:
                action, reason, review = TerminologySyncAction.LOSSY_MAPPING, lossy, True
            elif decision is not None and term is not None:
                if content_equal(local_summary, remote_summary):
                    action, reason, review = (
                        TerminologySyncAction.ADOPT_LINK,
                        TerminologySyncReason.SAFE_MATCH_PROPOSAL,
                        True,
                    )
                else:
                    action, reason, review = TerminologySyncAction.CONFLICT, TerminologySyncReason.BOTH_CHANGED, True
            elif decision is not None:
                action, reason, review = TerminologySyncAction.CREATE_REMOTE, TerminologySyncReason.LOCAL_ONLY, False
            elif inputs.profile.mode is TerminologySyncMode.BIDIRECTIONAL:
                action, reason, review = (
                    TerminologySyncAction.PROPOSE_LOCAL_ADD,
                    TerminologySyncReason.INDEPENDENT_REMOTE,
                    True,
                )
            else:
                action, reason, review = TerminologySyncAction.SKIP, TerminologySyncReason.INDEPENDENT_REMOTE, False
            items.append(
                self._item(
                    inputs,
                    action=action,
                    reason=reason,
                    local_term_id=None if decision is None else decision.term_id,
                    remote_id=None if term is None else term.remote_id,
                    local=local_summary,
                    remote=remote_summary,
                    requires_review=review,
                )
            )
        return tuple(items)

    def _item(
        self,
        inputs: TerminologySyncPlannerInput,
        *,
        action: TerminologySyncAction,
        reason: TerminologySyncReason,
        local_term_id: str | None = None,
        remote_id: int | None = None,
        base_digest: str | None = None,
        local: TerminologyContentSummary | None = None,
        remote: TerminologyContentSummary | None = None,
        managed: bool = False,
        requires_review: bool = False,
    ) -> TerminologySyncPlanItem:
        return TerminologySyncPlanItem(
            item_id=stable_plan_item_id(
                line_id=inputs.line.line_id,
                local_term_id=local_term_id,
                remote_id=remote_id,
                base_digest=base_digest,
            ),
            action=action,
            reason=reason,
            local_term_id=local_term_id,
            remote_id=remote_id,
            base_digest=base_digest,
            local=local,
            remote=remote,
            managed=managed,
            requires_review=requires_review,
        )

    def _make_plan(
        self,
        inputs: TerminologySyncPlannerInput,
        items: Iterable[TerminologySyncPlanItem],
        *,
        diagnostics: tuple[str, ...] = (),
    ) -> TerminologySyncPlan:
        local = inputs.local_snapshot
        assert local.version_id is not None
        assert local.content_digest is not None
        return TerminologySyncPlan(
            line_id=inputs.line.line_id,
            target_identity=inputs.line.target.target_id,
            binding_revision=inputs.binding_revision,
            profile_revision=inputs.profile.revision,
            mode=inputs.profile.mode,
            local_project_id=local.local_project_id,
            local_variant_id=local.local_variant_id,
            local_version_id=local.version_id,
            local_content_digest=local.content_digest,
            remote_snapshot_digest=inputs.remote_snapshot.observed_digest,
            baseline_revision=None if inputs.baseline is None else inputs.baseline.revision,
            items=tuple(items),
            diagnostics=diagnostics,
        )


__all__ = [
    "TerminologySyncPlanner",
    "TerminologySyncPlannerInput",
    "TerminologySyncPlanningError",
]
