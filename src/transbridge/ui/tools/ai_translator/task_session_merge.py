"""Small frozen result payloads and publication, independent of session capture."""

from __future__ import annotations

from transbridge.application.translation.result_merge import ResultEntryChange, ResultEntryState


def freeze_changes(draft, keys):
    draft.require_keys(keys)
    return tuple(
        ResultEntryChange(entry_state(draft.originals[key]), entry_state(draft.collections[key].get(key)))
        for key in keys
    )


def entry_state(entry):
    return ResultEntryState(entry.identity, entry.original, entry.translation, entry.stage, entry.context or "")


def freeze_application(session, candidates):
    """Freeze the authoritative command port before background execution."""
    persistence = session._persistence
    writer, policy = session.merge_evidence_writer, session.policy

    def preserve(plan):
        if writer is None:
            if plan.conflicts:
                raise RuntimeError("合入冲突证据尚未保存，当前内容保持原样。")
        else:
            writer.save(candidates, plan, policy)

    merge = persistence.freeze_merge_translation(
        candidates, policy, commit_mutation=session.commit_mutation, before_commit=preserve
    )

    def operation():
        result = merge()
        if result.is_success and writer is not None:
            writer.acknowledge(candidates, result.value, policy)
        return result

    return operation


def accept_result(session, plan, candidates):
    applied = {row.entry_key for row in plan.applied} | set(plan.unchanged_keys)
    session._applied_keys.update(applied)
    values = {row.incoming.entry_key: row.incoming for row in candidates}
    values.update({row.entry_key: row for row in plan.applied})
    for key in applied:
        row = values[key]
        session._applied_values[key] = (row.original, row.translation, row.stage, row.context or "")
    session._commit_result = plan
    if plan.applied:
        session._saved = False
        signal = (
            getattr(session._ctx, "collection_changed", None)
            if session._ctx.active_version_identity == session.version_identity
            else None
        )
        if signal is not None:
            signal.emit(getattr(session._ctx, "collection", None))
    return plan


def apply_result(session, candidates):
    """Commit selected fields against the current version; retain the frozen task base."""
    persistence = session._persistence

    def preserve(plan):
        # Never overwrite later edits unless the exact current/base/incoming
        # evidence is durable first. A disk failure leaves live data untouched.
        if session.merge_evidence_writer is None:
            if plan.conflicts:
                raise RuntimeError("合入冲突证据尚未保存，当前内容保持原样。")
            return
        session.merge_evidence_writer.save(candidates, plan, session.policy)

    from .version_operations import require_success

    result = require_success(
        persistence.merge_translation(
            candidates, session.policy, commit_mutation=session.commit_mutation, before_commit=preserve
        )
    )
    plan = result.value
    if session.merge_evidence_writer is not None:
        session.merge_evidence_writer.acknowledge(candidates, plan, session.policy)
    return accept_result(session, plan, candidates)
