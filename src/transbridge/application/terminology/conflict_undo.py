# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Restore one just-reviewed conflict through the existing audited draft transaction."""

from .conflicts import ConflictResolution
from .decisions import ManualActor, decision_state_digest
from .drafts import revised_draft
from .errors import RepositoryConflictError
from .models import ConflictStatus, ManualAction, ManualActionType


class ConflictUndoService:
    def __init__(self, drafts, actors, clock, ids):
        self._drafts = drafts
        self._actors = actors
        self._clock = clock
        self._ids = ids

    def undo(self, before, expectation, context):
        line = expectation.line
        if (context.project_id, context.variant_id) != (line.project_id, line.variant_id):
            raise PermissionError("撤销请求不属于当前工程和翻译版本")
        ref = before.draft_ref
        if (
            (ref.project_id, ref.variant_id) != (line.project_id, line.variant_id)
            or ref.draft_id != expectation.draft_id
            or ref.revision + 1 != expectation.draft_revision
        ):
            raise RepositoryConflictError("只能撤销刚刚保存的审核决定，请刷新后重新选择")
        actor = self._actors.resolve(context)
        if not isinstance(actor, ManualActor) or not actor.trusted:
            raise PermissionError("撤销术语审核需要可信的操作人身份")
        current = self._drafts.active(line.project_id, line.variant_id)
        self._drafts.require_expected(current, expectation)
        if (ref.base_version_id, ref.base_content_digest) != (
            current.ref.base_version_id,
            current.ref.base_content_digest,
        ):
            raise RepositoryConflictError("草稿基线已变化，无法撤销")
        group = before.conflict
        original = group.normalized_original
        if any(
            item.normalized_original != original
            or (item.project_id, item.variant_id) != (line.project_id, line.variant_id)
            for item in before.decisions
        ):
            raise ValueError("撤销快照包含其他术语或工程的决定")
        restored = tuple(
            sorted(
                (*tuple(item for item in current.decisions if item.normalized_original != original), *before.decisions),
                key=lambda item: item.term_id,
            )
        )
        before_digest = decision_state_digest(current.decisions)
        after_digest = decision_state_digest(restored)
        affected_ids = {item.term_id for item in current.decisions if item.normalized_original == original}
        prior = next(
            (
                action
                for action in current.actions
                if action.term_id in affected_ids
                and action.action_type in {ManualActionType.RESOLVE_CONFLICT, ManualActionType.IGNORE_CONFLICT}
                and action.before_digest == after_digest
                and action.after_digest == before_digest
            ),
            None,
        )
        if prior is None:
            raise RepositoryConflictError("审核快照与已保存的操作不一致，无法撤销")
        occurred_at = self._clock.now()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("manual action clock must return a timezone-aware datetime")
        action = ManualAction(
            self._ids.new_id(),
            prior.term_id,
            ManualActionType.UNDO_CONFLICT,
            actor.actor_id,
            occurred_at.isoformat(),
            current.ref.base_version_id,
            before_digest,
            after_digest,
            reason=f"撤销审核操作 {prior.action_id}",
        )
        resolutions = tuple(
            item for item in current.conflict_resolutions if item.conflict_group_id != group.conflict_group_id
        )
        if group.status is not ConflictStatus.UNRESOLVED:
            resolutions = (*resolutions, group)
        updated = revised_draft(
            current,
            decisions=restored,
            actions=(*current.actions, action),
            conflict_resolutions=resolutions,
            digest_context={"manual_action": action.action_id},
        )
        return ConflictResolution(self._drafts.save(updated, expectation=expectation), group)
