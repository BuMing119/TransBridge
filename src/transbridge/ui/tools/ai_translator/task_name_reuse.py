"""Write validated existing names to task drafts before starting name model work."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import FilesystemTranslationCheckpointPort, TranslationInput
from transbridge.application.translation.draft_reuse import commit_draft_reuse
from transbridge.application.translation.entry_alias import ai_entry_key

from .source_terms import source_run_id, source_terminology_path


def commit_reused_names(executor, tasks, groups, results, managers, *, wait):
    """The coordinator owns these drafts until all local commits have settled."""
    by_source = defaultdict(list)
    committed = set()
    for index, (text, reason) in results.items():
        if not reason:
            for occurrence in groups[index].occurrences:
                by_source[occurrence.source_key].append((occurrence.entry, text))
    if not by_source:
        return committed
    progress = executor.name_progress
    progress.begin("reuse", {source: len(rows) for source, rows in by_source.items()}, "写入已有名称译文")
    for task in tasks:
        rows = by_source.get(task.key, ())
        if not rows or not wait(executor):
            continue
        manager = managers[task.key]
        inputs = []
        for entry, _ in rows:
            context = manager.lookup_context_for_entry(entry)
            inputs.append(
                TranslationInput(
                    entry.identity,
                    entry.revision,
                    entry.original,
                    entry.translation,
                    entry.stage,
                    entry.context or "",
                    None if context is None else context.plugin_id,
                )
            )
        run_id = source_run_id(executor.attempt_id or executor.request.run_id, task.key) + "-name-reuse"
        root = Path(executor.config.get_ai_translator_dir(Path(source_terminology_path(task.esp_path, task.key)).stem))

        def active():
            if not wait(executor):
                return False
            if executor.consistency is not None:
                executor.consistency.require_current(executor.request)
            return True

        previous = executor.reused_drafts.get(task.key)
        if previous is not None:
            previous.require_current(task.collection, {item.entry_key for item in inputs})
        else:
            executor.reused_drafts[task.key] = commit_draft_reuse(
                tuple(inputs),
                {ai_entry_key(entry): text for entry, text in rows},
                collection=task.collection,
                run_id=run_id,
                checkpoint=FilesystemTranslationCheckpointPort(root / "runtime-v2"),
                guard=ImmediateCommitGuard(run_id, active=active),
            )
        message = f"已有名称译文已写入任务草稿：{len(rows)} 条；无需模型翻译"
        executor.log(task.key, message)
        progress.update("reuse", task.key, current=len(rows), detail=message)
    keys = {(source, key) for source, receipt in executor.reused_drafts.items() for key in receipt.keys}
    committed.update(
        index
        for index in results
        if not results[index][1]
        and all((occ.source_key, occ.entry.identity) in keys for occ in groups[index].occurrences)
    )
    return committed


def retain_unfinished_reuse(executor, tasks, outcomes):
    """Keep already-written text inspectable when the name barrier cannot complete."""
    from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
    from transbridge.ai_translator.translator import TranslationResult

    from .source_execution import SourceOutcome

    by_source = {item.task.key: item for item in outcomes}
    for task in tasks:
        receipt = executor.reused_drafts.get(task.key)
        if receipt is None:
            continue
        outcome = by_source.get(task.key, SourceOutcome(task, cancelled=executor.stop.is_set()))
        translation = outcome.translation or TranslationResult()
        entries = dict(translation.entry_outcomes)
        failures = list(translation.failed_entries)
        added = 0
        for snapshot in receipt.snapshots:
            if snapshot.entry_key in entries:
                continue
            status = "cancelled" if executor.stop.is_set() else "failed"
            reason = outcome.error or "已有译文已写入任务草稿，后续检查尚未完成"
            entries[snapshot.entry_key] = TranslationEntryOutcome(
                snapshot.entry_key, status, snapshot.translation, snapshot.stage, reason
            )
            if status == "failed":
                failures.append(f"{ai_entry_key(task.collection.get(snapshot.entry_key))}: {reason}")
                added += 1
        by_source[task.key] = replace(
            outcome,
            translation=replace(
                translation,
                entry_outcomes=entries,
                failed_entries=failures,
                failed_count=translation.failed_count + added,
            ),
        )
    return tuple(by_source[task.key] for task in tasks if task.key in by_source)
