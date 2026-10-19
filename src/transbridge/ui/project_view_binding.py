"""Own the Qt-thread projection binding and atomic project-open handoff."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from threading import Lock

from PyQt6.QtCore import QThread

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.identity import EntryKey
from transbridge.application.projections.models import copy_projection_value
from transbridge.ui.project_labels import project_entry_labels
from transbridge.ui.projection_types import CollectionSlot

_UNSET = object()


@dataclass(frozen=True, slots=True)
class ProjectViewState:
    dirty: bool = False
    revision: int | None = None
    project_revision: int | None = None
    variant_revision: int | None = None
    project_id: str | None = None
    name: str | None = None
    sources: tuple[dict, ...] = ()
    variants: tuple[dict, ...] = ()
    variant_id: str | None = None
    binding: dict | None = None
    label_library: dict = field(default_factory=dict)
    entry_labels: dict = field(default_factory=dict)
    exact_labels: dict = field(default_factory=dict)
    snapshot: object = field(default=None, repr=False, compare=False)

    @classmethod
    def capture(cls, snapshot, previous_labels):
        if snapshot is None:
            return cls()
        values = snapshot.values
        binding = copy_projection_value(values.get("paratranz_binding"))
        if isinstance(binding, dict):
            int(binding["project_id"])
        labels, exact = project_entry_labels(values.get("entries", ()), previous_labels)
        return cls(
            dirty=snapshot.dirty,
            revision=snapshot.revision,
            project_revision=_optional(values.get("project_revision"), int),
            variant_revision=_optional(values.get("variant_revision"), int),
            project_id=_optional(values.get("project_id"), str),
            name=_optional(values.get("project_name"), str),
            sources=tuple(copy_projection_value(item) for item in values.get("sources", ())),
            variants=tuple(copy_projection_value(item) for item in values.get("variants", ())),
            variant_id=_optional(values.get("variant_id") or values.get("active_variant_id"), str),
            binding=None if not isinstance(binding, dict) else binding,
            label_library={
                str(key): copy_projection_value(value) for key, value in (values.get("label_library") or {}).items()
            },
            entry_labels=labels,
            exact_labels=exact,
            snapshot=snapshot,
        )

    @property
    def project_identity(self):
        return self.project_id, self.name, self.sources, self.variants

    def committed(self, snapshot):
        """Retarget prepared data to the acknowledged revision without recapturing entries."""
        values = snapshot.values
        expected = (self.project_id, self.project_revision, self.variant_id, self.variant_revision, self.revision)
        observed = (
            values.get("project_id"),
            values.get("project_revision"),
            values.get("variant_id") or values.get("active_variant_id"),
            values.get("variant_revision"),
            snapshot.revision,
        )
        if observed != expected:
            raise ValueError("The committed project changed after its workbench view was prepared")
        return replace(self, dirty=snapshot.dirty, snapshot=snapshot)


def _optional(value, convert):
    return None if value is None else convert(value)


def prepare_project_view(snapshot):
    """Capture metadata and label indices before the worker hands ownership to Qt."""
    return ProjectViewState.capture(snapshot, {})


def _snapshot_identity(snapshot):
    if snapshot is None:
        return None
    return (
        snapshot.stream_id,
        snapshot.revision,
        snapshot.persisted_revision,
        snapshot.values.get("variant_id") or snapshot.values.get("active_variant_id"),
    )


class ProjectViewBinding:
    """Keep queued notifications outside the GUI's project installation transaction."""

    def __init__(self, context, store):
        self.context, self.store = context, store
        self.state = ProjectViewState()
        self.installing = False
        self.failed = False
        self._subscription = None
        self._lock = Lock()
        self._generation = 0
        self._token = None
        self._installed = False
        self._installed_snapshot = None
        self._pending = _UNSET
        self._sequence = 0
        self._applied_sequence = 0
        self._closed = False

    @property
    def opening(self):
        with self._lock:
            return self._token is not None

    def start(self):
        if self.store is not None:
            self._subscription = self.store.subscribe(self.receive)

    def close(self):
        with self._lock:
            self._closed = True
            self._token = None
            self._pending = _UNSET
        subscription, self._subscription = self._subscription, None
        if subscription is not None:
            subscription.close()

    def receive(self, snapshot):
        with self._lock:
            if self._closed:
                return
            self._sequence += 1
            sequence = self._sequence
            if self._token is not None:
                self._pending = (sequence, snapshot)
                return
        if QThread.currentThread() != self.context.thread():
            self.context.safe_mutate(lambda: self._deliver(sequence, snapshot))
        else:
            self._deliver(sequence, snapshot)

    def _deliver(self, sequence, snapshot):
        with self._lock:
            if self._closed or sequence <= self._applied_sequence:
                return
            if self._token is not None:
                if self._pending is _UNSET or sequence > self._pending[0]:
                    self._pending = (sequence, snapshot)
                return
            self._applied_sequence = sequence
        self.apply(snapshot)

    def begin(self):
        self._require_gui_thread()
        with self._lock:
            if self._closed:
                raise RuntimeError("The project projection binding is closed")
            if self._token is not None:
                raise RuntimeError("A project is already opening")
            self._generation += 1
            self._token = self._generation
            self._installed = False
            self._installed_snapshot = None
            self._pending = _UNSET
            return self._token

    def install(self, token, snapshot, slots, *, state=None):
        with self._lock:
            if self._closed or token != self._token or self._installed:
                return False
        self._require_gui_thread()
        # Validate and derive everything before changing any visible reference.
        prepared_slots = {}
        for key, slot in slots:
            if not isinstance(key, str) or not key or key in prepared_slots or not isinstance(slot, CollectionSlot):
                raise ValueError("Project slots require unique non-empty locations and CollectionSlot values")
            prepared_slots[key] = slot
        if state is not None and (not isinstance(state, ProjectViewState) or state.snapshot is not snapshot):
            raise ValueError("Prepared project view must belong to the exact installation snapshot")
        next_state = state or ProjectViewState.capture(snapshot, self.context._entry_labels_exact)
        with self._lock:
            if self._closed or token != self._token:
                return False
            if self._pending is not _UNSET and _snapshot_identity(self._pending[1]) != _snapshot_identity(snapshot):
                return False
            self._installed = True
            self._installed_snapshot = snapshot
            self._applied_sequence = self._sequence
            self._pending = _UNSET
        context = self.context
        self.installing = True
        try:
            context._slots = prepared_slots
            context._active_key = next(reversed(prepared_slots), None)
            self.failed = False
            self._apply_state(next_state)
            context.collection_list_changed.emit()
        finally:
            self.installing = False
        context.project_installed.emit()
        context.collection_changed.emit(context.collection)
        return True

    def finish(self, token):
        with self._lock:
            if self._closed or token != self._token:
                return False
        self._require_gui_thread()
        with self._lock:
            if self._closed or token != self._token:
                return False
            self._token = None
            pending, self._pending = self._pending, _UNSET
            if self._installed and pending is not _UNSET and pending[1] is self._installed_snapshot:
                self._applied_sequence = max(self._applied_sequence, pending[0])
                pending = _UNSET
            elif self._installed and pending is not _UNSET:
                if _snapshot_identity(pending[1]) != _snapshot_identity(self._installed_snapshot):
                    self.failed = True
        if pending is not _UNSET:
            self._deliver(*pending)
        return True

    def _require_gui_thread(self):
        if QThread.currentThread() != self.context.thread():
            raise RuntimeError("Project view installation must run on the GUI thread")

    def edit_rejection(self):
        if self.opening:
            code, message = "PROJECT_OPEN_IN_PROGRESS", "工程仍在加载，请等待工作台恢复后再编辑。"
        elif self.failed:
            code, message = "PROJECT_VIEW_UNAVAILABLE", "工作台尚未恢复，请重新打开工程后再编辑。"
        else:
            return None
        return OperationResult.failed(DomainError(ErrorCategory.PREREQUISITE, code, message))

    def apply(self, snapshot):
        self._apply_state(ProjectViewState.capture(snapshot, self.context._entry_labels_exact))

    def _apply_state(self, state):
        context, old = self.context, self.state
        labels_changed = (
            context._label_library != state.label_library
            or context._entry_labels != state.entry_labels
            or context._entry_labels_exact != state.exact_labels
        )
        self.state = state
        context._label_library = state.label_library
        context._entry_labels = state.entry_labels
        context._entry_labels_exact = state.exact_labels
        context.paratranz_project_id = None if state.binding is None else int(state.binding["project_id"])
        if labels_changed:
            context.label_data_changed.emit()
        if old.dirty != state.dirty or (old.revision is not None and state.revision != old.revision and state.dirty):
            context.dirty_changed.emit()
        if old.project_identity != state.project_identity:
            context.project_changed.emit()
        if old.variant_id != state.variant_id and state.variant_id is not None:
            context.variant_changed.emit(state.variant_id)
        if old.binding != state.binding:
            context.paratranz_binding_changed.emit(context.paratranz_binding)

    def diverged(self):
        if self.store is None:
            return False
        snapshot = self.store.snapshot()
        if snapshot is None:
            return any(slot.collection for slot in self.context.slots.values())
        states = {
            EntryKey.from_dict(item["entry_key"]): (
                str(item.get("translation", "")),
                int(item.get("stage", 0)),
                _external_refs(copy_projection_value(value) for value in item.get("external_refs", ())),
            )
            for item in snapshot.values.get("entries", ())
        }
        visible = {}
        for slot in self.context.slots.values():
            for entry in slot.collection:
                state = (
                    entry.translation or "",
                    entry.stage,
                    _external_refs(value.to_dict() for value in entry.external_refs),
                )
                if entry.identity in visible and visible[entry.identity] != state:
                    return True
                visible[entry.identity] = state
        return visible != states


def _external_refs(values):
    return tuple(
        sorted(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in values)
    )
