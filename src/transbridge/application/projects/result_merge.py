"""Apply three-way result decisions against the latest authoritative Variant."""

from __future__ import annotations

from bisect import bisect_left
from operator import attrgetter

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.translation.result_merge import ResultEntryState, merge_result_entries

from .variant_commands import EntryStatePatch


class ProjectResultMergeService:
    """Re-read and rebase on CAS contention without changing the task identity."""

    def __init__(self, lifecycle, source_reader, commit_states, commit_records=None):
        self._lifecycle = lifecycle
        self._source_reader = source_reader
        self._commit_states = commit_states
        self._commit_records = commit_records

    def merge(
        self,
        candidates,
        context,
        *,
        expected_variant_ref,
        policy,
        commit_mutation=None,
        persist=False,
        before_commit=None,
        policies=None,
        decision_states=None,
        clear_external_scope=None,
    ):
        candidates = tuple(candidates)
        base = {item.base.entry_key: item.base for item in candidates}
        incoming = {item.incoming.entry_key: item.incoming for item in candidates}
        if len(base) != len(candidates):
            return _failed("RESULT_TARGET_DUPLICATED", "结果中存在重复词条。", context)
        for _attempt in range(3):
            active = self._lifecycle.active
            if active is None or active.variant is None or active.formal_variant_ref != expected_variant_ref:
                return _failed("ACTIVE_VARIANT_IDENTITY_CHANGED", "任务对应的工程版本已切换，成果已保留。", context)
            if context.project_id not in (None, active.project_ref.identity.value):
                return _failed("PROJECT_CONTEXT_MISMATCH", "任务对应的工程已切换，成果已保留。", context)
            snapshot = active.variant.snapshot()
            project_revision = active.project.envelope.revision
            try:
                texts = self._source_reader(active.project, snapshot, set(base), context) if base else {}
                records = _target_records(snapshot.entries, base)
                current = {
                    entry.entry_key: ResultEntryState(
                        entry.entry_key,
                        texts[entry.entry_key][0],
                        entry.translation,
                        entry.stage.value,
                        texts[entry.entry_key][1],
                        entry.tombstone,
                        texts[entry.entry_key][2] if len(texts[entry.entry_key]) > 2 else None,
                    )
                    for entry in records.values()
                    if entry.entry_key in texts
                }
                plan = merge_result_entries(
                    base, current, incoming, policy=policy, policies=policies, decision_states=decision_states
                )

                def guarded(mutation):
                    published = False

                    def publish():
                        nonlocal published
                        if published:
                            return
                        if before_commit is not None:
                            before_commit(plan)
                        mutation()
                        published = True

                    publish() if commit_mutation is None else commit_mutation(publish)
                    if not published:
                        raise DomainError(
                            ErrorCategory.PERMISSION,
                            "RESULT_MERGE_NOT_AUTHORIZED",
                            "合入未获执行，成果已保留，当前内容未修改。",
                        )

                states = {entry.entry_key: (entry.translation, entry.stage) for entry in plan.applied}
                commit_states = self._commit_states
                if clear_external_scope is not None:
                    if self._commit_records is None:
                        raise ValueError("external reference cleanup requires the entry record command")
                    states = {
                        entry.entry_key: EntryStatePatch(
                            entry.translation,
                            entry.stage,
                            tuple(
                                ref
                                for ref in records[entry.entry_key].external_refs
                                if (ref.system, ref.scope) != clear_external_scope
                            ),
                        )
                        for entry in plan.applied
                    }
                    commit_states = self._commit_records
                result = commit_states(
                    states,
                    context,
                    expected_project_revision=project_revision,
                    expected_variant_revision=snapshot.revision,
                    expected_variant_ref=expected_variant_ref,
                    commit_mutation=guarded,
                    persist=persist,
                )
                if result.is_success:
                    return OperationResult.completed(plan, diagnostics=result.diagnostics, run_id=context.run_id)
                if not any(
                    diagnostic.code
                    in {"ACTIVE_PROJECT_REVISION_CHANGED", "ACTIVE_VARIANT_REVISION_CHANGED", "SOURCE_UPDATE_STALE"}
                    for diagnostic in result.diagnostics
                ):
                    return result
            except Exception as exc:
                return OperationResult.from_exception(exc, run_id=context.run_id)
        return _failed("RESULT_MERGE_BUSY", "当前内容持续变化，成果已保留，请稍后重试合入。", context)


def _target_records(entries, keys):
    """Use the Snapshot's sorted-key invariant without rebuilding a full index.

    Sparse batches use indexed lookup. A dense batch walks the tuple once so a
    complete-source application does not regress to one binary search per row.
    """
    if len(keys) * max(1, len(entries).bit_length()) > len(entries):
        return {entry.entry_key: entry for entry in entries if entry.entry_key in keys}
    records = {}
    entry_key = attrgetter("entry_key")
    for key in keys:
        index = bisect_left(entries, key, key=entry_key)
        if index < len(entries) and (entry := entries[index]).entry_key == key:
            records[key] = entry
    return records


def _failed(code, message, context):
    return OperationResult.failed(DomainError(ErrorCategory.CONFLICT, code, message), run_id=context.run_id)
