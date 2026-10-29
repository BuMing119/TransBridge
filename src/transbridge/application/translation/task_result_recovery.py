"""Restore validated results and decisions on detached task snapshots."""

from __future__ import annotations

from dataclasses import replace
import json

from transbridge.application.io.identity import EntryKey
from transbridge.application.preflight.inputs import SOURCE_CONTEXT_KNOWN


def save_recovery_results(store, task_id, ledger):
    """Called only in persistence/preparation workers, never for progress events."""
    store.save_ledger_results(task_id, ledger)


def recovery_result_rows(ledger):
    rows = []
    for key, row in getattr(ledger, "entries", {}).items():
        if row.status != "succeeded":
            continue
        rows.append({
            "entry_key": key.to_dict(),
            "source": row.source,
            "action": row.action,
            "before": {
                "original": row.before.original,
                "translation": row.before.translation,
                "stage": row.before.stage,
                "context": row.before.context or "",
                "context_known": source_context_known(row.before),
                "revision": row.before.revision.value,
            },
            "status": row.status,
            "text": row.text,
            "stage": row.stage,
            "reason": row.reason,
            "decision": row.decision,
            "applied": row.applied,
            "attempt": row.attempt,
            "report_details": row.report_details,
            "merge_status": row.merge_status,
            "merge_reason": row.merge_reason,
            "decision_revision": row.decision_revision,
        })
    return rows


def load_recovery_results(store, record):
    payload = store.load_results(record["task_id"])
    if payload is None:
        return None
    scope = {
        EntryKey.from_dict(key): (source["key"], action)
        for source in record["sources"]
        for field, action in (
            ("translate_keys", "translation"),
            ("polish_keys", "polish"),
            ("postprocess_keys", "postprocess"),
        )
        for key in source.get(field, ())
    }
    for row in payload["rows"]:
        if scope.get(EntryKey.from_dict(row["entry_key"])) != (row["source"], row["action"]):
            raise ValueError("已保存结果不属于原任务范围，不能恢复")
    return payload


def restore_recovery_results(ledger, tasks, record):
    """Reuse only completed results; unchanged current-after values are already saved."""
    payload = (record or {}).get("results")
    if payload is None:
        return ledger, tasks, 0
    restored = set()
    for saved in payload["rows"]:
        key = EntryKey.from_dict(saved["entry_key"])
        row = ledger.entries.get(key)
        if row is None:
            continue  # Already-saved entries may have been excluded by scope validation.
        before, current = saved["before"], row.before
        if (saved["source"], saved["action"]) != (
            row.source,
            row.action,
        ):
            raise ValueError(f"已保存结果的范围已变化：{key.local_key}")
        source_changed = (current.original, current.context or "") != (before["original"], before["context"])
        same_before = (current.translation, current.stage) == (before["translation"], before["stage"])
        same_after = saved["decision"] == "accepted" and (current.translation, current.stage) == (
            saved["text"],
            saved["stage"],
        )
        # Recovery restores evidence, not permission to overwrite the live entry.
        # The merge command will compare these original frozen values to current.
        frozen_before = replace(
            current,
            original=before["original"],
            translation=before["translation"],
            stage=before["stage"],
            context=recovery_source_context(before),
        )
        ledger.entries[key] = replace(
            row,
            before=frozen_before,
            status="succeeded",
            text=saved["text"],
            stage=saved["stage"],
            reason=saved["reason"],
            decision=saved["decision"],
            applied=not source_changed and same_after and not same_before,
            attempt=saved["attempt"],
            report_details_json=json.dumps(saved["report_details"], ensure_ascii=False, allow_nan=False),
            merge_status="pending" if source_changed else saved.get("merge_status", ""),
            merge_reason="source_changed_or_missing" if source_changed else saved.get("merge_reason", ""),
            decision_revision=saved.get("decision_revision", 0),
        )
        restored.add(key)
    ledger._index = None
    _ = ledger.index
    remaining = tuple(
        replace(
            task,
            translate_entries=tuple(entry for entry in task.translate_entries if entry.identity not in restored),
            polish_entries=tuple(entry for entry in task.polish_entries if entry.identity not in restored),
            postprocess_entries=tuple(entry for entry in task.postprocess_entries if entry.identity not in restored),
        )
        for task in tasks
    )
    return ledger, tuple(task for task in remaining if task.entries), len(restored)


def recovery_source_context(before):
    """Old records retain their known-context guard; new unknown input stays unknown."""
    return None if before.get("context_known") is False else before["context"]


def source_context_known(entry):
    """Parsed empty context is known; only saved-only metadata marks it unknown."""
    return dict(getattr(entry, "metadata", ())).get(SOURCE_CONTEXT_KNOWN, True) is not False
