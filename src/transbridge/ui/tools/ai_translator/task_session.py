"""Version transactions for explicitly selected, detached AI task results."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.entry_alias import ai_entry_id
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.version_persistence import VersionPersistence
from transbridge.ui.workers import ApiWorker

from .task_apply_preparation import ApplyDelivery
from .task_draft import TaskDraft
from .task_scope import SourceTask
from .version_snapshot import _require_success


@dataclass(frozen=True)
class _SourceState:
    slot: object
    collection: object
    revision: object
    entries: tuple


class TaskSession:
    """Publish selected drafts atomically while guarding against external version edits."""

    def __init__(
        self, ctx: object, tasks: tuple[SourceTask, ...], spec: object, *, project_dir: Path | None = None
    ) -> None:
        self._ctx = ctx
        self._identity = ctx.active_version_identity
        self.version_label = next(
            (
                str(item.get("name") or item.get("id"))
                for item in getattr(ctx, "project_variants", ())
                if self._identity is not None and str(item.get("id")) == str(self._identity[1])
            ),
            str(self._identity[1]) if self._identity is not None else "",
        )
        if self._identity is None:
            raise RuntimeError("请先打开一个项目版本，AI 任务需要先创建版本快照。")
        if project_dir is None:
            project_dir = getattr(getattr(ctx, "active_project", None), "project_dir", None)
        self._project_dir = Path(project_dir) if project_dir is not None else None
        self._persistence = VersionPersistence(ctx, self._identity)
        self._worker: ApiWorker | None = None
        self._apply_delivery: ApplyDelivery | None = None
        self._captured = self._completed = self._saved = self._discarded = False
        self._commit_result = None
        self._applied_keys: set[EntryKey] = set()
        self._generation = 0
        self._states = self._read_states()
        self._revisions = self._read_revisions()
        self._draft = TaskDraft(tasks, self._states)
        self.tasks = self._draft.tasks
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        mode = {"translate": "翻译", "polish": "润色", "mixed": "混合", "custom": "自定义"}.get(
            getattr(spec, "mode", "translate"), "翻译"
        )
        suffix = str(getattr(spec, "run_id", "run"))[-8:]
        self.before_snapshot_name = f"AI-{mode}-执行前-{stamp}-{suffix}"
        self.after_snapshot_name = f"AI-{mode}-保存后-{stamp}-{suffix}"
        self._after_snapshot_base = self.after_snapshot_name

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
    def project_saved(self) -> bool:
        return self._saved or bool(getattr(self._persistence, "project_saved", False))

    @property
    def snapshot_saved(self) -> bool:
        return self._saved or bool(getattr(self._persistence, "snapshot_saved", False))

    @property
    def project_saved_signal(self):
        """Project dirty-state notifications; callers must verify with observe_project_saved."""
        return getattr(self._ctx, "dirty_changed", None)

    def observe_project_saved(self) -> bool:
        """Latch a verified external save without treating a different clean version as saved."""
        if not self._applied_keys or self.project_saved or getattr(self._ctx, "dirty", None) is not False:
            return False
        try:
            self._require_current(allow_discarded=True)
        except RuntimeError:
            # A different revision/project cannot establish durability of our captured result.
            return False
        self._persistence.mark_project_saved()
        return True

    @property
    def version_identity(self) -> tuple[str, str]:
        return self._identity

    @property
    def project_dir(self) -> Path | None:
        return self._project_dir

    @property
    def history_dir(self) -> Path | None:
        return None if self._project_dir is None else self._project_dir / "ai-task-history"

    @property
    def can_save(self) -> bool:
        return bool(self._applied_keys) and not self._saved

    @property
    def applied_keys(self) -> frozenset[EntryKey]:
        return frozenset(self._applied_keys)

    def require_current(self) -> None:
        """Validate the current project inputs before applying or dispatching retries."""
        self._require_current()

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
        result = self.apply_entries(set(self._draft.keys))
        self.finish()
        return result

    def finish(self) -> None:
        """Seal a task after its ledger has no remaining executable entries."""
        self._require_current()
        self._completed = True

    def apply_entries(self, keys: set[EntryKey]) -> object:
        """Commit an explicit subset once without ending the remaining task."""
        keys = self._application_keys(keys)
        if not keys:
            return self._commit_result
        prepared = self._draft.freeze_merge(self._states, keys).prepare()
        return self._commit_entries(keys, prepared.entries, baselines=dict(prepared.baselines))

    def _application_keys(self, keys: set[EntryKey]) -> set[EntryKey]:
        self._require_current()
        if self.is_busy:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        if not self._captured:
            raise RuntimeError("执行前版本快照尚未完成，不能提交 AI 结果。")
        keys = set(keys)
        self._draft.require_keys(keys)
        keys.difference_update(self._applied_keys)
        if not keys:
            return keys
        if self._completed:
            raise RuntimeError("AI 任务已经结束，不能继续应用结果。")
        return keys

    def apply_entries_async(
        self,
        keys: set[EntryKey],
        *,
        on_success: Callable,
        on_error: Callable[[str], None],
        validate: Callable | None = None,
    ) -> None:
        """Prepare detached inputs off-thread, then validate and commit on the GUI thread."""
        keys = self._application_keys(keys)
        if not keys:
            on_success(self._commit_result)
            return
        frozen = self._draft.freeze_merge(self._states, keys)
        worker = ApiWorker(frozen.prepare, route_http_errors=False)

        def completed(entries, error):
            # finished has arrived: callbacks may safely begin a subsequent save.
            self._worker = None
            self._apply_delivery = None
            try:
                if error is not None:
                    raise RuntimeError(error)
                if entries is None:
                    raise RuntimeError("应用准备线程未返回结果。")
                self._require_current()
                if validate is not None:
                    validate()
                result = self._commit_entries(keys, entries.entries, baselines=dict(entries.baselines))
            except Exception as exc:
                on_error(str(exc))
                return
            on_success(result)

        self._worker = worker
        self._apply_delivery = ApplyDelivery(worker, completed)
        worker.start()

    def _commit_entries(self, keys: set[EntryKey], entries: tuple, *, baselines: dict | None = None) -> object:
        persistence = self._persistence if not self._generation else VersionPersistence(self._ctx, self._identity)
        result = _require_success(persistence.commit_translation(entries))
        # A successful authoritative command may already have reprojected slots.
        # Only replace collections still holding the unchanged pre-run objects.
        current = self._slots()
        merged = {entry.identity: entry for entry in entries}
        for task in self.tasks:
            previous = self._states[task.key]
            slot = current.get(task.key)
            if slot is previous.slot and slot.collection is previous.collection:
                if tuple(slot.collection) == previous.entries and any(entry.identity in keys for entry in task.entries):
                    slot.collection = TranslationEntryCollection(merged[entry.identity] for entry in previous.entries)
        self._persistence = persistence
        self._generation += 1
        self.after_snapshot_name = self._after_snapshot_base
        if self._generation > 1:
            self.after_snapshot_name += f"-{self._generation}"
        self._saved = False
        self._applied_keys.update(keys)
        self._commit_result = result
        self._states = self._read_states(baselines)
        self._revisions = self._read_revisions()
        signal = getattr(self._ctx, "collection_changed", None)
        if signal is not None:
            signal.emit(getattr(self._ctx, "collection", None))
        return result

    def save_translation(self, *, on_success: Callable, on_error: Callable[[str], None]) -> None:
        self._require_current(allow_discarded=True)
        if not self._applied_keys:
            raise RuntimeError("任务结果尚未应用，不能保存翻译。")
        if self._saved:
            on_success({"snapshot_name": self.after_snapshot_name, "already_saved": True})
            return
        entries = tuple(entry for state in self._states.values() for entry in state.entries)

        def operation():
            detached = deepcopy(entries)
            self._require_current(allow_discarded=True)
            return _require_success(self._persistence.save_translation(detached, self.after_snapshot_name))

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
        self.retry_entries({entry.identity for task in self.tasks if task.key in keys for entry in task.entries})

    def retry_entries(self, keys: set[EntryKey]) -> tuple[SourceTask, ...]:
        """Return only selected execution inputs; prior successful drafts remain intact."""
        self._require_current()
        if self.is_busy:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        if self._completed:
            raise RuntimeError("AI 任务已经提交，不能重试条目。")
        keys = set(keys)
        if keys & self._applied_keys:
            raise ValueError("已应用的条目不能重试。")
        selected = self._draft.retry(keys)
        self.tasks = self._draft.tasks
        return selected

    def _slots(self) -> dict[str, object]:
        slots = {str(key): slot for key, slot in self._ctx.slots.items()}
        if len(slots) != len(self._ctx.slots):
            raise ValueError("AI 任务来源键重复。")
        return slots

    def _read_revisions(self) -> tuple:
        return getattr(self._ctx, "project_revision", None), getattr(self._ctx, "variant_revision", None)

    def _read_states(self, prepared: dict | None = None) -> dict[str, _SourceState]:
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
                    if ai_entry_id(entry) in legacy_ids:
                        raise ValueError("旧版工程存在跨插件重复条目 ID，请先迁移到 V2 工程后运行 AI 任务。")
                    legacy_ids.add(ai_entry_id(entry))
            baseline = None if prepared is None else prepared.get(key)
            if baseline is None or baseline != entries:
                baseline = deepcopy(entries)
            states[key] = _SourceState(
                slot, slot.collection, getattr(slot.collection, "collection_revision", None), baseline
            )
        return states

    def _require_current(self, *, allow_discarded: bool = False) -> None:
        if self._discarded and not allow_discarded:
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
