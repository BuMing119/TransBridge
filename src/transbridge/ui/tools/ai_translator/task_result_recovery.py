"""Restore validated results and decisions on detached task snapshots."""

from __future__ import annotations

from dataclasses import replace
import json

from transbridge.application.io.identity import EntryKey


def save_recovery_results(store, task_id, ledger):
    """Called only in persistence/preparation workers, never for progress events."""
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
        })
    store.save_results(task_id, rows)


def load_recovery_results(store, record):
    payload = store.load_results(record["task_id"])
    if payload is None:
        return None
    scope = {
        EntryKey.from_dict(key): (source["key"], action)
        for source in record["sources"]
        for field, action in (("translate_keys", "translation"), ("polish_keys", "polish"))
        for key in source[field]
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
        if (saved["source"], saved["action"], before["original"], before["context"]) != (
            row.source,
            row.action,
            current.original,
            current.context or "",
        ):
            raise ValueError(f"已保存结果的原文或范围已变化：{key.local_key}")
        same_before = (current.translation, current.stage) == (before["translation"], before["stage"])
        same_after = saved["decision"] == "accepted" and (current.translation, current.stage) == (
            saved["text"],
            saved["stage"],
        )
        if not same_before and not same_after:
            raise ValueError(f"当前译文已变化，不能覆盖：{key.local_key}")
        ledger.entries[key] = replace(
            row,
            status="succeeded",
            text=saved["text"],
            stage=saved["stage"],
            reason=saved["reason"],
            decision=saved["decision"],
            applied=same_after and not same_before,
            attempt=saved["attempt"],
            report_details_json=json.dumps(saved["report_details"], ensure_ascii=False, allow_nan=False),
        )
        restored.add(key)
    ledger._index = None
    _ = ledger.index
    remaining = tuple(
        replace(
            task,
            translate_entries=tuple(entry for entry in task.translate_entries if entry.identity not in restored),
            polish_entries=tuple(entry for entry in task.polish_entries if entry.identity not in restored),
        )
        for task in tasks
    )
    return ledger, tuple(task for task in remaining if task.entries), len(restored)
