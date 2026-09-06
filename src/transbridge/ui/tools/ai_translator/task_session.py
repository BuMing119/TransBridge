"""Detached output and one version transaction for a complete AI task."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.version_persistence import VersionPersistence
from transbridge.ui.workers import ApiWorker

from .task_scope import SourceTask
from .version_snapshot import _require_success


@dataclass(frozen=True)
class _SourceState:
    slot: object
    collection: object
    revision: object
    entries: tuple


class TaskSession:
    """Keep live collections unchanged until every source has completed successfully."""

    def __init__(self, ctx: object, tasks: tuple[SourceTask, ...], spec: object) -> None:
        self._ctx = ctx
        self._identity = ctx.active_version_identity
        if self._identity is None:
            raise RuntimeError("请先打开一个项目版本，AI 任务需要先创建版本快照。")
        self._persistence = VersionPersistence(ctx, self._identity)
        self._worker: ApiWorker | None = None
        self._captured = self._completed = self._saved = self._discarded = False
        self._commit_result = None
        self._states = self._read_states()
        self._revisions = self._read_revisions()
        seen = set()
        detached = []
        for task in tasks:
            source = self._states.get(task.key)
            if source is None or source.collection is not task.collection or task.key in seen:
                raise ValueError("AI 任务来源重复或已变化，请重新选择处理内容。")
            seen.add(task.key)
            collection = TranslationEntryCollection(deepcopy(tuple(task.collection)))
            mapped = {entry.identity: entry for entry in collection}
            original = {entry.identity: entry for entry in task.collection}

            def remap(entries: tuple) -> tuple:
                if any(original.get(entry.identity) is not entry for entry in entries):
                    raise ValueError("AI 任务条目不属于其处理来源，请重新选择处理内容。")
                return tuple(mapped[entry.identity] for entry in entries)

            detached.append(
                replace(
                    task,
                    collection=collection,
                    translate_entries=remap(task.translate_entries),
                    polish_entries=remap(task.polish_entries),
                )
            )
        self.tasks = tuple(detached)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        mode = {"translate": "翻译", "polish": "润色", "mixed": "混合", "custom": "自定义"}.get(
            getattr(spec, "mode", "translate"), "翻译"
        )
        suffix = str(getattr(spec, "run_id", "run"))[-8:]
        self.before_snapshot_name = f"AI-{mode}-执行前-{stamp}-{suffix}"
        self.after_snapshot_name = f"AI-{mode}-保存后-{stamp}-{suffix}"

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    @property
    def completed(self) -> bool:
        return self._completed

    @property
    def saved(self) -> bool:
        return self._saved

    @property
    def can_save(self) -> bool:
        return self._completed and not self._saved

    def capture_before(self, *, on_success: Callable, on_error: Callable[[str], None]) -> None:
        self._require_current()
        if self._captured:
            on_success({"snapshot_name": self.before_snapshot_name, "already_captured": True})
            return
        entries = tuple(deepcopy(entry) for state in self._states.values() for entry in state.entries)

        def operation():
            self._require_current()
            return _require_success(self._persistence.create_snapshot(self.before_snapshot_name, entries))

        def captured(result):
            try:
                self._require_current()
            except RuntimeError as exc:
                on_error(str(exc))
                return
            self._captured = True
            on_success(result)

        self._run(operation, on_success=captured, on_error=on_error)

    def mark_completed(self) -> object:
        if self._completed:
            return self._commit_result
        self._require_current()
        if not self._captured:
            raise RuntimeError("执行前版本快照尚未完成，不能提交 AI 结果。")
        entries = self._merged_entries()
        result = _require_success(self._persistence.commit_translation(entries))
        # A successful authoritative command may already have reprojected slots.
        # Only replace collections still holding the unchanged pre-run objects.
        current = self._slots()
        for task in self.tasks:
            previous = self._states[task.key]
            slot = current.get(task.key)
            if slot is previous.slot and slot.collection is previous.collection:
                if tuple(slot.collection) == previous.entries:
                    slot.collection = TranslationEntryCollection(deepcopy(tuple(task.collection)))
        self._completed = True
        self._commit_result = result
        self._states = self._read_states()
        self._revisions = self._read_revisions()
        signal = getattr(self._ctx, "collection_changed", None)
        if signal is not None:
            signal.emit(getattr(self._ctx, "collection", None))
        return result

    def save_translation(self, *, on_success: Callable, on_error: Callable[[str], None]) -> None:
        self._require_current()
        if not self._completed:
            raise RuntimeError("AI 任务尚未正常完成，不能保存翻译。")
        if self._saved:
            on_success({"snapshot_name": self.after_snapshot_name, "already_saved": True})
            return
        entries = tuple(deepcopy(entry) for state in self._states.values() for entry in state.entries)

        def operation():
            self._require_current()
            return _require_success(self._persistence.save_translation(entries, self.after_snapshot_name))

        def saved(result):
            self._saved = True
            on_success(result)

        self._run(operation, on_success=saved, on_error=on_error)

    def rollback_uncommitted(self) -> None:
        if not self._completed:
            self._discarded = True

    def reset_sources(self, keys) -> None:
        """Retry whole failed sources from their captured inputs, retaining successful drafts."""
        self._require_current()
        if self._completed:
            raise RuntimeError("AI 任务已经提交，不能重置处理来源。")
        keys = set(keys)
        if not keys.issubset({task.key for task in self.tasks}):
            raise ValueError("重试包含未知处理来源。")
        reset = []
        for task in self.tasks:
            if task.key not in keys:
                reset.append(task)
                continue
            collection = TranslationEntryCollection(deepcopy(self._states[task.key].entries))
            reset.append(
                replace(
                    task,
                    collection=collection,
                    translate_entries=tuple(collection.get(entry.identity) for entry in task.translate_entries),
                    polish_entries=tuple(collection.get(entry.identity) for entry in task.polish_entries),
                )
            )
        self.tasks = tuple(reset)

    def _slots(self) -> dict[str, object]:
        slots = {str(key): slot for key, slot in self._ctx.slots.items()}
        if len(slots) != len(self._ctx.slots):
            raise ValueError("AI 任务来源键重复。")
        return slots

    def _read_revisions(self) -> tuple:
        return getattr(self._ctx, "project_revision", None), getattr(self._ctx, "variant_revision", None)

    def _read_states(self) -> dict[str, _SourceState]:
        states = {}
        identities = set()
        legacy_ids = set()
        for key, slot in self._slots().items():
            entries = tuple(slot.collection)
            for entry in entries:
                if entry.identity in identities:
                    raise ValueError("AI 任务来源包含重复 EntryKey，不能安全合并版本。")
                identities.add(entry.identity)
                if entry.id and not getattr(self._ctx, "uses_authoritative_projection", False):
                    if entry.id in legacy_ids:
                        raise ValueError("旧版工程存在跨插件重复条目 ID，请先迁移到 V2 工程后运行 AI 任务。")
                    legacy_ids.add(entry.id)
            states[key] = _SourceState(
                slot, slot.collection, getattr(slot.collection, "collection_revision", None), deepcopy(entries)
            )
        return states

    def _require_current(self) -> None:
        if self._discarded:
            raise RuntimeError("AI 任务已取消，未发布的结果已丢弃。")
        if self._ctx.active_version_identity != self._identity:
            raise RuntimeError("活动项目或版本已变化，不能提交本次 AI 结果。")
        if self._read_revisions() != self._revisions:
            raise RuntimeError("项目或版本在 AI 运行期间已修改，请重新运行任务。")
        slots = self._slots()
        if slots.keys() != self._states.keys():
            raise RuntimeError("处理来源已变化，请重新运行 AI 任务。")
        for key, state in self._states.items():
            slot = slots[key]
            if (
                slot is not state.slot
                or slot.collection is not state.collection
                or getattr(slot.collection, "collection_revision", None) != state.revision
                or tuple(slot.collection) != state.entries
            ):
                raise RuntimeError(f"来源 {key} 在 AI 运行期间已修改，不能覆盖当前内容。")

    def _merged_entries(self) -> tuple:
        translated = {task.key: task.collection for task in self.tasks}
        entries = tuple(
            deepcopy(entry) for key, state in self._states.items() for entry in translated.get(key, state.entries)
        )
        if len({entry.identity for entry in entries}) != len(entries):
            raise ValueError("AI 结果包含重复 EntryKey，不能安全合并版本。")
        return entries

    def _run(self, operation: Callable, *, on_success: Callable, on_error: Callable[[str], None]) -> None:
        if self._worker is not None:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        worker = ApiWorker(operation, route_http_errors=False)
        self._worker = worker
        worker.result.connect(on_success)
        worker.error.connect(on_error)

        def cleanup():
            if self._worker is worker:
                self._worker = None
            worker.deleteLater()

        worker.finished.connect(cleanup)
        worker.start()


__all__ = ["TaskSession"]
