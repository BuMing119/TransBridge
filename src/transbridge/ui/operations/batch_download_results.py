"""Publish a frozen download result and present its local merge decisions."""

from __future__ import annotations

from hashlib import sha256
import logging

from transbridge.application.sync.local_candidate import RemoteSourceIndex, build_local_candidate
from transbridge.application.sync.models import SyncAction
from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    ResultEntryChange,
    ResultEntryState,
    source_locator_signature,
)
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef

from .batch_download_view import DownloadPreview, DownloadReceipt
from .sync_review_view import SyncReviewCandidate, SyncReviewRow

_logger = logging.getLogger(__name__)


def result_changes(plan):
    changes = []
    for source, source_plan in zip(plan.sources, plan.plans, strict=True):
        base = {entry.entry_key: entry for entry in source.local_entries}
        items = tuple(item for item in source_plan.items if item.action is SyncAction.UPDATE_LOCAL)
        candidate = build_local_candidate(
            source.local_entries,
            RemoteSourceIndex(source.remote_entries),
            items,
            attach_remote_refs=False,
            preserve_local_source=True,
        )
        selected = {item.entry_key for item in items}
        for entry in candidate:
            if entry.entry_key in selected:
                changes.append(ResultEntryChange(_state(base[entry.entry_key]), _state(entry)))
    return tuple(changes)


def publish_results(session, task_context, commit, progress, cancel, cancellation):
    cancellation.raise_if_cancelled()
    if cancel.is_set():
        cancellation.raise_if_cancelled()
        raise RuntimeError("下载已取消，成果已保留。")
    session._check_target()
    candidates = session._override_changes or result_changes(session.plan)

    def preserve(plan):
        session._check_target()
        session._merge_attempt_filename = session.result_store.save_merge(
            session.record, candidates, plan, session.merge_policy
        )

    def guarded(mutation):
        def publish():
            session._check_target()
            mutation()

        commit(publish)

    progress(0, 1, "正在合入并保存已确定的译文…")
    policies, reviewed = merge_options(session, candidates)
    commands = session.context.project_commands
    result = commands.merge_entry_states(
        candidates,
        task_context,
        expected_variant_ref=VariantRef(
            VariantId(session.request_context.variant_id), ProjectId(session.request_context.project_id)
        ),
        policy=session.merge_policy,
        policies=policies,
        decision_states=reviewed,
        clear_external_scope=("paratranz", f"project:{session.target.project_id}"),
        persist=True,
        commit_mutation=guarded,
        before_commit=preserve,
    )
    return _receipt(session, candidates, result, progress)


def merge_options(session, candidates):
    decisions = dict(session.decisions) if not session._override_changes else {}
    policies, reviewed = {}, {}
    keys = {row.incoming.entry_key for row in candidates}
    for row_id, state in session.review_states.items():
        choice = decisions.get(row_id)
        if state.entry_key in keys and choice in {"keep_local", "incoming"}:
            policies[state.entry_key] = (
                ResultConflictPolicy.KEEP_CURRENT if choice == "keep_local" else ResultConflictPolicy.PREFER_INCOMING
            )
            reviewed[state.entry_key] = state
    return policies, reviewed


def preview_entry_states(snapshot, registrations, keys):
    """Match authoritative merge evidence, including source-owned locator fields."""
    fingerprints = {row["source_id"]: row.get("fingerprint") for row in registrations}
    return {
        entry.identity: ResultEntryState(
            entry.identity,
            entry.original,
            entry.translation or "",
            entry.stage,
            entry.context or "",
            source_locator=source_locator_signature(entry.metadata, entry.string_id, fingerprints[source.source_id])
            if fingerprints.get(source.source_id)
            else None,
        )
        for source in snapshot.sources
        for entry in source.entries
        if entry.identity in keys
    }


def _receipt(session, candidates, result, progress):
    if not result.is_success:
        detail = "；".join(item.message for item in result.diagnostics)
        raise RuntimeError(f"合入或保存失败，下载成果已保留，可直接重试。{detail}")
    plan = result.value
    session.merge_result = plan
    if not session._override_changes:
        session.record["policy"] = session.merge_policy.value
    session.record["decisions"] = list(session.decisions)
    session.record["applied"] = [row.entry_key.to_dict() for row in plan.applied]
    session.record["pending"] = [row.entry_key.to_dict() for row in plan.pending]
    warnings = [item.message for item in result.diagnostics]
    try:
        session.result_store.confirm_merge(session.record, session._merge_attempt_filename)
    except OSError as exc:
        warnings.append(f"译文已保存，成果处理回执暂未写入；可幂等重试：{exc}")
    remote_rows = tuple(
        row for source_plan in session.plan.plans for row in _remote_review_rows(source_plan) if not row.decision
    )
    rows = (*remote_rows, *merge_review_rows(session, plan))
    preview = DownloadPreview(
        0,
        len(plan.unchanged_keys) + len(plan.kept_current_keys),
        len(plan.pending) + session.plan.deferred_count,
        session.details,
        rows,
        False,
        "已保存可合入译文；分歧成果独立保留，可选择后重试，也可关闭后再处理。",
        source_ids=session.source_ids,
        policy=session.merge_policy.value,
    )
    count = len(plan.applied)
    try:
        progress(1, 1, "已保存当前版本，下载成果已保留。")
    except Exception:
        _logger.exception("Download committed but progress presentation failed")
    return DownloadReceipt(
        count,
        (*session.details, f"已更新并保存 {count} 条译文；下载成果与覆盖前内容已独立保留。", *warnings),
        bool(rows),
        preview if rows else None,
        session._retryable,
    )


def _state(entry):
    return ResultEntryState(
        entry.entry_key, entry.original, entry.translation, entry.stage, entry.context, entry.deleted
    )


def merge_review_rows(session, plan):
    rows = []
    for conflict in plan.conflicts:
        row = _review(conflict)
        if conflict.current is not None:
            session.review_states[row.row_id] = conflict.current
        rows.append(row)
    return tuple(rows)


def _remote_review_rows(plan):
    from .sync_review_projection import review_rows

    return review_rows(plan)


def _row_id(key, current):
    return "merge:" + sha256((key.serialize() + repr(current)).encode()).hexdigest()


def _review(conflict):
    current = conflict.current or conflict.base
    structural = conflict.reason == "source_changed_or_missing"
    decision = {"keep_current": "keep_local", "prefer_incoming": "incoming"}.get(conflict.decision, "")
    return SyncReviewRow(
        _row_id(conflict.entry_key, conflict.current),
        conflict.entry_key.local_key,
        current.original,
        current.translation,
        current.context or "",
        "原文、上下文或条目已变化，或来源暂不可读取；成果先保留。"
        if structural
        else "下载期间这条译文有新修改，请选择当前译文或新成果。",
        (
            SyncReviewCandidate(
                "incoming",
                "本轮下载成果",
                conflict.incoming.original,
                conflict.incoming.translation,
                conflict.incoming.context or "",
                not structural,
                stage=conflict.incoming.stage,
            ),
        ),
        decision,
        not structural,
        "请核对源条目后再处理。" if structural else "",
        stage=current.stage,
    )
