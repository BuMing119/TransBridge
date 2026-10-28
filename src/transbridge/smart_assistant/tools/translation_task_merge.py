"""Apply saved assistant results through the authoritative three-way merge command."""

from __future__ import annotations

from dataclasses import replace

from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef


def merge_saved_results(ctx, store, record, payload, *, policy=None, execution=None, evidence=None):
    """Reapply existing successful results without entering any model execution path."""
    from .translation_task_results import _MERGE_PENDING, _RUN_LOCK, _authorized, _caller, task_ports
    from .types import ExecutionContext
    from .undo_capture import capture_variant_command

    if record.get("result_kind") == "source":
        from ._source_import import merge_source_result

        return merge_source_result(ctx, store, record)

    app, caller = _caller(ctx)
    _authorized(record, caller)
    if (record["project_id"], record["variant_id"]) != app.active_version_identity:
        raise ValueError("任务对应的工程版本已切换，成果已保留，请切回原版本再合入。")
    _app, commands, context, identity, _root = task_ports(ctx)
    _authorized(record, context)
    if (record["project_id"], record["variant_id"]) != identity:
        raise ValueError("任务对应的工程版本已切换，成果已保留，请切回原版本再合入。")
    policy = ResultConflictPolicy(policy or record.get("conflict_policy", "keep_current"))
    candidates = tuple(
        ResultEntryChange(
            ResultEntryState(
                entry_key=_key(row),
                original=row["before"]["original"],
                translation=row["before"]["translation"],
                stage=row["before"]["stage"],
                context=row["before"]["context"],
                source_locator=row["before"].get("source_locator"),
            ),
            ResultEntryState(
                entry_key=_key(row),
                original=row["before"]["original"],
                translation=row["text"],
                stage=row["stage"],
                context=row["before"]["context"],
                source_locator=row["before"].get("source_locator"),
            ),
        )
        for row in (payload or {}).get("rows", ())
    )
    evidence = evidence or TaskMergeEvidenceStore(store.root, record["task_id"])
    dispatch = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)

    commit_warnings = []

    def commit(mutation):
        cancellation = getattr(ctx, "cancellation_token", None)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if execution is None:
            mutation()
            return
        executed = False

        def publish():
            nonlocal executed
            mutation()
            executed = True

        try:
            decision = execution.commit(execution.ref.run_id, publish)
        except Exception as exc:
            if not executed:
                raise
            commit_warnings.append(f"合入已完成，但执行回执尚未保存：{exc}")
            return
        if not decision.accepted and executed:
            commit_warnings.append(f"合入已完成，但执行回执未确认：{decision.reason}")
        elif not decision.accepted:
            raise RuntimeError(f"任务成果已保留，合入未获执行：{decision.reason}")

    completed = []

    def command():
        result = commands.merge_entry_states(
            candidates,
            context,
            expected_variant_ref=VariantRef(VariantId(identity[1]), ProjectId(identity[0])),
            policy=policy,
            before_commit=lambda plan: evidence.save(candidates, plan, policy),
            commit_mutation=commit,
        )
        completed.append(result)
        if result.is_success and isinstance(ctx, ExecutionContext) and _app.active_version_identity == identity:
            # Lifecycle projection delivery has completed. Advance only this
            # request's own successful command before its outer effect receipt.
            for public, private in (
                ("project_revision", "_target_project_revision"),
                ("variant_revision", "_target_variant_revision"),
            ):
                revision = getattr(_app, public, None)
                if revision is not None:
                    object.__setattr__(ctx, private, revision)
        # Undo's Variant-command contract consumes the committed revision, while
        # callers of this adapter retain the original merge plan.
        return (
            replace(result, value={"revision": getattr(_app, "variant_revision", None)})
            if result.is_success
            else result
        )

    warning = ""
    try:
        dispatch.safe_mutate_wait(lambda: capture_variant_command(ctx, command))
    except Exception as exc:
        if not completed or not completed[0].is_success:
            raise
        warning = f"合入已完成，但助手执行或撤销回执尚未保存：{exc}"
    result = completed[0]
    if not result.is_success:
        details = "; ".join(item.message for item in result.diagnostics)
        raise RuntimeError(f"成果已保存，合入未完成：{details}")
    plan = result.value
    acknowledged = evidence.acknowledge(candidates, plan, policy)
    summary = {
        "applied_count": len(plan.applied),
        "unchanged_count": len(plan.unchanged_keys),
        "kept_current_count": len(plan.kept_current_keys),
        "pending_count": len(plan.pending),
        "receipt_saved": acknowledged,
        "policy": policy.value,
    }
    warning = "; ".join(filter(None, (warning, evidence.error, *commit_warnings)))
    applied = {row.entry_key for row in plan.applied} | set(plan.unchanged_keys)
    kept = set(plan.kept_current_keys)
    pending = {row.entry_key: row.reason for row in plan.pending}
    rows = [
        {
            **row,
            "applied": _key(row) in applied,
            "decision": "accepted" if _key(row) in applied else "rejected" if _key(row) in kept else row["decision"],
            "merge_status": "applied" if _key(row) in applied else "kept_current" if _key(row) in kept else "pending",
            "merge_reason": pending.get(_key(row), ""),
            "decision_revision": row.get("decision_revision", 0) + 1,
        }
        for row in (payload or {}).get("rows", ())
    ]
    try:
        store.save_results(record["task_id"], rows)
        record.update(store.update_metadata(record["task_id"], merge=summary, merge_error=warning))
    except (OSError, ValueError) as exc:
        warning = f"合入已完成，但成果状态回执尚未保存：{exc}"
        with _RUN_LOCK:
            _MERGE_PENDING[record["task_id"]] = evidence, summary, rows
    if not acknowledged:
        with _RUN_LOCK:
            _MERGE_PENDING[record["task_id"]] = evidence, summary, rows
    return summary, warning


def _key(row):
    from transbridge.application.io.identity import EntryKey

    return EntryKey.from_dict(row["entry_key"])
