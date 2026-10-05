"""Detached source drafts and immutable task inputs for selective retries."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from transbridge.application.io.identity import EntryKey
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .task_apply_preparation import DraftMergeInput
from .task_scope import SourceTask


class TaskDraft:
    def __init__(self, tasks: tuple[SourceTask, ...], states: dict) -> None:
        seen = set()
        detached = []
        for task in tasks:
            source = states.get(task.key)
            if source is None or source.collection is not task.collection or task.key in seen:
                raise ValueError("AI 任务来源重复或已变化，请重新选择处理内容。")
            seen.add(task.key)
            original = {entry.identity: entry for entry in task.collection}
            if any(original.get(entry.identity) is not entry for entry in task.entries):
                raise ValueError("AI 任务条目不属于其处理来源，请重新选择处理内容。")
            collection = TranslationEntryCollection(deepcopy(tuple(task.collection)))
            detached.append(self._remap(task, collection))
        self.tasks = tuple(detached)
        self.originals = {entry.identity: deepcopy(entry) for task in self.tasks for entry in task.entries}
        self.keys = frozenset(self.originals)

    def require_keys(self, keys: set[EntryKey]) -> None:
        if not keys.issubset(self.keys):
            raise ValueError("操作包含不属于本次任务的条目。")

    def retry(self, keys: set[EntryKey]) -> tuple[SourceTask, ...]:
        """Restore only selected inputs, retaining full source context and other drafts."""
        self.require_keys(keys)
        tasks, selected = [], []
        for task in self.tasks:
            if not any(entry.identity in keys for entry in task.entries):
                tasks.append(task)
                continue
            collection = TranslationEntryCollection(
                deepcopy(self.originals[entry.identity]) if entry.identity in keys else entry
                for entry in task.collection
            )
            restored = self._remap(task, collection)
            tasks.append(restored)
            selected.append(
                replace(
                    restored,
                    translate_entries=tuple(entry for entry in restored.translate_entries if entry.identity in keys),
                    polish_entries=tuple(entry for entry in restored.polish_entries if entry.identity in keys),
                )
            )
        self.tasks = tuple(tasks)
        return tuple(selected)

    def merge(self, states: dict, keys: set[EntryKey]) -> tuple:
        """Publish translation state only; source and metadata remain the live baseline."""
        return self.freeze_merge(states, keys).prepare().entries

    def freeze_merge(self, states: dict, keys: set[EntryKey]) -> DraftMergeInput:
        """Capture scalar draft values before dispatch; never expose live slots to workers."""
        self.require_keys(keys)
        candidates = {
            entry.identity: task.collection.get(entry.identity) for task in self.tasks for entry in task.entries
        }
        if any(candidates[key] is None for key in keys):
            raise ValueError("任务草稿缺少待应用条目，请重新运行任务。")
        return DraftMergeInput(
            tuple(entry for state in states.values() for entry in state.entries),
            tuple((key, (candidates[key].translation, candidates[key].stage)) for key in keys),
            tuple((source, len(state.entries)) for source, state in states.items()),
        )

    @staticmethod
    def _remap(task: SourceTask, collection: TranslationEntryCollection) -> SourceTask:
        return replace(
            task,
            collection=collection,
            translate_entries=tuple(collection.get(entry.identity) for entry in task.translate_entries),
            polish_entries=tuple(collection.get(entry.identity) for entry in task.polish_entries),
        )
