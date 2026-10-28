"""Bounded GUI capture and detached construction of an AI task session."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace
from time import monotonic

from PyQt6.QtCore import QObject, QTimer

from transbridge.application.translation.task_draft import TaskDraft
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.ui.workers import ApiWorker

from .task_apply_preparation import ApplyDelivery


@dataclass(frozen=True)
class SourceState:
    slot: object
    collection: object
    revision: object
    entries: tuple


@dataclass(frozen=True)
class CapturedTask:
    key: str
    label: str
    esp_path: str | None
    translate_keys: tuple
    polish_keys: tuple


@dataclass(frozen=True)
class PreparedSession:
    states: dict
    draft: TaskDraft
    initial_entry_results: TaskEntryResults


def read_slots(ctx):
    slots = {str(key): slot for key, slot in ctx.slots.items()}
    if len(slots) != len(ctx.slots):
        raise ValueError("AI 任务来源键重复。")
    return slots


def read_revisions(ctx):
    return getattr(ctx, "project_revision", None), getattr(ctx, "variant_revision", None)


def entries_unchanged(collection, baseline):
    """Compare every stored field, including nested metadata, without tuple copies.

    TranslationEntry stores its complete state in __dict__. Dict equality keeps
    this final GUI guard exact while avoiding Python dataclass tuple construction
    per entry. The check is intentionally stricter than dataclass compare=False.
    """
    return len(collection) == len(baseline) and all(
        type(entry) is type(before) and vars(entry) == vars(before)
        for entry, before in zip(collection, baseline, strict=True)
    )


def require_current(ctx, identity, revisions, states, *, contents=True):
    """The final content comparison must remain atomic with respect to GUI events."""
    if ctx.active_version_identity != identity:
        raise RuntimeError("活动项目或版本已变化，不能提交本次 AI 结果。")
    if read_revisions(ctx) != revisions:
        raise RuntimeError("项目或版本在 AI 运行期间已修改，请重新运行任务。")
    slots = read_slots(ctx)
    if slots.keys() != states.keys():
        raise RuntimeError("处理来源已变化，请重新运行 AI 任务。")
    for key, state in states.items():
        slot = slots[key]
        if (
            slot is not state.slot
            or slot.collection is not state.collection
            or getattr(slot.collection, "collection_revision", None) != state.revision
            or (contents and not entries_unchanged(slot.collection, state.entries))
        ):
            raise RuntimeError(f"来源 {key} 在 AI 运行期间已修改，不能覆盖当前内容。")


class SessionCapture:
    """Read mutable GUI objects only in slices on their owning thread."""

    def __init__(self, ctx, tasks, spec, *, selection_intent=None):
        self.ctx, self.tasks = ctx, tasks
        self.run_id = getattr(spec, "run_id", "run")
        self.identity = ctx.active_version_identity
        if self.identity is None:
            raise RuntimeError("请先打开一个项目版本，再创建 AI 任务。")
        self.revisions = read_revisions(ctx)
        self.states = {
            key: SourceState(slot, slot.collection, getattr(slot.collection, "collection_revision", None), ())
            for key, slot in read_slots(ctx).items()
        }
        self.captured_tasks = []
        self.selection_capture = selection_intent
        self.origins = {}

    def steps(self):
        for key, state in self.states.items():
            entries = []
            for entry in state.collection:
                # All scalar/identity fields are immutable values. The three metadata
                # envelopes may contain mutable JSON and must be detached on the GUI
                # thread; passing them to deepcopy in a worker would be a data race.
                shell = copy(entry)
                for field in ("metadata", "external_refs", "provenance"):
                    object.__setattr__(shell, field, deepcopy(getattr(entry, field)))
                for field in vars(entry).keys() - entry.__dataclass_fields__.keys():
                    object.__setattr__(shell, field, deepcopy(getattr(entry, field)))
                entries.append(shell)
                if self.selection_capture is not None:
                    self.origins[entry.identity] = id(entry)
                yield
            self.states[key] = replace(state, entries=tuple(entries))
        seen = set()
        for task in self.tasks:
            state = self.states.get(task.key)
            if state is None or state.collection is not task.collection or task.key in seen:
                raise ValueError("AI 任务来源重复或已变化，请重新选择处理内容。")
            seen.add(task.key)
            actions = []
            for selected in (task.translate_entries, task.polish_entries):
                keys = []
                for entry in selected:
                    if task.collection.get(entry.identity) is not entry:
                        raise ValueError("AI 任务条目不属于其处理来源，请重新选择处理内容。")
                    keys.append(entry.identity)
                    yield
                actions.append(tuple(keys))
            self.captured_tasks.append(CapturedTask(task.key, task.label, task.esp_path, *actions))
        if self.selection_capture is not None:
            yield from self.selection_capture.steps()

    def prepare(self):
        # This method uses only captured values; live slot/collection references in
        # SourceState are opaque ownership tokens and are never inspected here.
        identities = set()
        for state in self.states.values():
            for entry in state.entries:
                if entry.identity in identities:
                    raise ValueError("AI 任务来源包含重复 EntryKey，不能安全合并版本。")
                identities.add(entry.identity)
        tasks = tuple(self.captured_tasks)
        draft_states = self.states
        if self.selection_capture is not None:
            selected = self.selection_capture.intent.select(self.states, self.origins)
            if self.selection_capture.intent.recovery is not None:
                draft_states = dict(self.states)
                for task in selected:
                    previous = draft_states.get(task.key, SourceState(None, None, None, ()))
                    draft_states[task.key] = replace(previous, entries=tuple(task.collection))
            tasks = tuple(
                CapturedTask(
                    task.key,
                    task.label,
                    task.esp_path,
                    tuple(entry.identity for entry in task.translate_entries),
                    tuple(entry.identity for entry in task.polish_entries),
                )
                for task in selected
                if task.entries
            )
        draft = TaskDraft.from_captured(tasks, draft_states)
        results = TaskEntryResults(draft.tasks, self.run_id, originals=draft.originals)
        results.index
        return PreparedSession(self.states, draft, results)

    def require_current(self):
        require_current(self.ctx, self.identity, self.revisions, self.states)
        if self.selection_capture is not None:
            self.selection_capture.require_current()


_ACTIVE_JOBS: set[SessionPreparationJob] = set()


class SessionPreparationJob(QObject):
    """Cancel cooperatively; retain even a destroyed owner's worker until finished."""

    def __init__(self, capture, assemble, on_success, on_error, *, parent=None, prepare=None):
        super().__init__()
        self.capture, self.assemble = capture, assemble
        self.on_success, self.on_error = on_success, on_error
        self._prepare = capture.prepare if prepare is None else prepare
        self._steps = capture.steps()
        self._worker = self._delivery = None
        self._cancelled = False
        self.is_busy = True
        _ACTIVE_JOBS.add(self)
        if parent is not None:
            parent.destroyed.connect(self.cancel)
        QTimer.singleShot(0, self._capture_slice)

    @property
    def busy(self):
        return self.is_busy

    def cancel(self):
        self._cancelled = True

    def _capture_slice(self):
        if self._cancelled:
            self._complete(None, "AI 任务准备已取消。")
            return
        try:
            require_current(
                self.capture.ctx, self.capture.identity, self.capture.revisions, self.capture.states, contents=False
            )
            deadline = monotonic() + 0.004
            for _ in range(128):
                next(self._steps)
                if monotonic() >= deadline:
                    break
        except StopIteration:
            worker = ApiWorker(self._prepare, route_http_errors=False)
            self._worker = worker
            self._delivery = ApplyDelivery(worker, self._complete)
            worker.start()
        except Exception as exc:
            self._complete(None, str(exc))
        else:
            QTimer.singleShot(0, self._capture_slice)

    def _complete(self, prepared, error):
        self._worker = self._delivery = None
        if not self.is_busy:
            return
        self.is_busy = False
        try:
            if self._cancelled:
                raise RuntimeError("AI 任务准备已取消。")
            if error is not None:
                raise RuntimeError(error)
            if prepared is None:
                raise RuntimeError("AI 任务准备线程未返回结果。")
            # Chunked validation alone misses an edit to an earlier chunk. This
            # final exact scan and assembly run in the same GUI callback.
            self.capture.require_current()
            session = self.assemble(prepared, self.capture)
        except Exception as exc:
            self.on_error(str(exc))
        else:
            self.on_success(session)
        finally:
            _ACTIVE_JOBS.discard(self)
            self.deleteLater()
