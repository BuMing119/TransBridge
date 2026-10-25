"""Offline recovery benchmark: durable results, fresh session, Qt heartbeat."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter, sleep

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_result_preparation import TaskResultPreparation
from transbridge.ui.tools.ai_translator.task_result_recovery import (
    load_recovery_results,
    restore_recovery_results,
    save_recovery_results,
)
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


def benchmark(count, directory):
    tasks = []
    for source, size in (("plugin-a", count // 2), ("plugin-b", count - count // 2)):
        entries = tuple(
            TranslationEntry(
                str(index),
                str(index),
                f"Source text {index}",
                "",
                0,
                "INFO:NAM1",
                entry_key=EntryKey(SourceNamespace(source), str(index)),
            )
            for index in range(size)
        )
        tasks.append(SourceTask(source, source, None, TranslationEntryCollection(entries), entries, ()))
    tasks = tuple(tasks)
    ledger = TaskEntryResults(tasks, "before-crash")
    ledger.entries = {
        key: replace(
            row,
            status="succeeded",
            text=f"候选译文 {key.local_key}",
            stage=2,
            decision="rejected" if index % 10 == 0 else "accepted",
            attempt=1,
            report_details_json='{"review_status":"needs_review","review_reason":"保留复核依据"}',
        )
        for index, (key, row) in enumerate(ledger.entries.items())
    }
    record = {
        "task_id": "logical-task",
        "sources": [
            {
                "key": task.key,
                "translate_keys": [entry.identity.to_dict() for entry in task.translate_entries],
                "polish_keys": [],
            }
            for task in tasks
        ],
    }
    app = QApplication.instance() or QApplication([])
    operation = TaskResultPreparation(None)
    done, failures, samples = [], [], []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: samples.append(perf_counter()))

    def recover():
        store = TaskRecoveryStore(directory)
        start = perf_counter()
        save_recovery_results(store, record["task_id"], ledger)
        saved = perf_counter()
        # A new store and ledger represent a different process/session. No live
        # collection or old in-memory candidate evidence is reused.
        record["results"] = load_recovery_results(TaskRecoveryStore(directory), record)
        new = TaskEntryResults(tasks, "after-crash")
        restored, remaining, total = restore_recovery_results(new, tasks, record)
        assert total == count and not remaining
        assert sum(row.decision == "rejected" for row in restored.entries.values()) == (count + 9) // 10
        assert all(row.report_details["review_reason"] == "保留复核依据" for row in restored.entries.values())
        return {"entries": total, "save_seconds": saved - start, "restore_seconds": perf_counter() - saved}

    started = perf_counter()
    samples.append(started)
    timer.start()
    operation.start(recover, on_success=done.append, on_error=failures.append)
    while operation.busy:
        app.processEvents()
        sleep(0.001)
    samples.append(perf_counter())
    timer.stop()
    if failures:
        raise RuntimeError(failures[0])
    result = done[0]
    result.update(
        elapsed_seconds=perf_counter() - started,
        heartbeat_count=len(samples) - 2,
        max_heartbeat_gap_ms=max(b - a for a, b in zip(samples, samples[1:])) * 1000,
        model_requests=0,
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entries", type=int, default=20_000)
    args = parser.parse_args()
    if args.entries <= 0:
        parser.error("--entries must be positive")
    workspace = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=".tmp-recovery-benchmark-", dir=workspace) as temporary:
        directory = Path(temporary).resolve()
        if directory.parent != workspace or not directory.name.startswith(".tmp-recovery-benchmark-"):
            raise RuntimeError("Unexpected benchmark directory")
        print(json.dumps(benchmark(args.entries, directory), ensure_ascii=False))


if __name__ == "__main__":
    main()
