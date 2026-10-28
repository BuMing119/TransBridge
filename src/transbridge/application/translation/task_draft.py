"""Detached source drafts and immutable task inputs for selective retries."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import replace

from transbridge.application.io.identity import EntryKey
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .task_scope import SourceTask


class TaskDraft:
    @classmethod
    def from_captured(cls, tasks, states):
        """Build detached collections in a worker without inspecting live sources."""
        draft = cls.__new__(cls)
        detached = []
        for task in tasks:
            collection = TranslationEntryCollection(deepcopy(states[task.key].entries))
            detached.append(
                SourceTask(
                    task.key,
                    task.label,
                    task.esp_path,
                    collection,
                    tuple(collection.get(key) for key in task.translate_keys),
                    tuple(collection.get(key) for key in task.polish_keys),
                    tuple(collection.get(key) for key in getattr(task, "postprocess_keys", ())),
                )
            )
        draft.tasks = tuple(detached)
        selected = {entry.identity for task in draft.tasks for entry in task.entries}
        draft.originals = {
            entry.identity: entry for state in states.values() for entry in state.entries if entry.identity in selected
        }
        draft.keys = frozenset(draft.originals)
        draft.collections = {entry.identity: task.collection for task in draft.tasks for entry in task.entries}
        return draft

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
                    postprocess_entries=tuple(
                        entry for entry in restored.postprocess_entries if entry.identity in keys
                    ),
                )
            )
        self.tasks = tuple(tasks)
        self.collections = {entry.identity: task.collection for task in self.tasks for entry in task.entries}
        return tuple(selected)

    def prepare_retry(self, keys):
        draft = copy(self)
        selected = draft.retry(keys)
        return draft, selected

    @staticmethod
    def _remap(task: SourceTask, collection: TranslationEntryCollection) -> SourceTask:
        return replace(
            task,
            collection=collection,
            translate_entries=tuple(collection.get(entry.identity) for entry in task.translate_entries),
            polish_entries=tuple(collection.get(entry.identity) for entry in task.polish_entries),
            postprocess_entries=tuple(collection.get(entry.identity) for entry in task.postprocess_entries),
        )
