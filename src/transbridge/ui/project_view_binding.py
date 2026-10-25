"""Own the Qt-thread projection binding and atomic project-open handoff."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import logging
from threading import Condition

from PyQt6.QtCore import QThread

from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.io.identity import EntryKey
from transbridge.application.projections.models import copy_projection_value
from transbridge.ui.entry_projection import prepare_projected_entries, sync_projected_entries
from transbridge.ui.project_labels import project_entry_labels
from transbridge.ui.projection_types import CollectionSlot

_UNSET = object()
_logger = logging.getLogger(__name__)


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


def _same_entry_scope(previous, current):
    if previous is None or current is None:
        return False
    project = previous.values.get("project_id")
    variant = previous.values.get("variant_id") or previous.values.get("active_variant_id")
    return (
        project is not None
        and variant is not None
        and previous.stream_id == current.stream_id
        and project == current.values.get("project_id")
        and variant == (current.values.get("variant_id") or current.values.get("active_variant_id"))
    )


class ProjectViewBinding:
    """Keep queued notifications outside the GUI's project installation transaction."""

    def __init__(self, context, store):
        self.context, self.store = context, store
        self.state = ProjectViewState()
        self.installing = False
        self.failed = False
        self._subscription = None
        self._lock = Condition()
        self._generation = 0
        self._token = None
        self._installed = False
        self._installed_snapshot = None
        self._pending = _UNSET
        self._sequence = 0
        self._applied_sequence = 0
        self._deliveries = 0
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
            self._lock.notify_all()
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
            previous = self.state.snapshot
            prepared = None
            if not self.installing and _same_entry_scope(previous, snapshot):
                try:
                    prepared = prepare_projected_entries(tuple(self.context.slots.values()), previous, snapshot)
                except Exception:
                    # A preparation failure must not strand queued delivery or
                    # a background save waiting for it. Retain the cause and
                    # reconcile the current view at the normal Qt boundary.
                    _logger.exception("Unable to prepare project entries; retrying reconciliation on Qt delivery")
            with self._lock:
                if self._closed:
                    return
            self.context.safe_mutate(lambda: self._deliver(sequence, snapshot, prepared))
        else:
            self._deliver(sequence, snapshot)

    def _deliver(self, sequence, snapshot, prepared=None):
        with self._lock:
            if self._closed or sequence <= self._applied_sequence:
                return
            if self._token is not None:
                if self._pending is _UNSET or sequence > self._pending[0]:
                    self._pending = (sequence, snapshot)
                return
            self._applied_sequence = sequence
            self._deliveries += 1
        try:
            self.apply(snapshot, prepared=prepared)
        finally:
            with self._lock:
                self._deliveries -= 1
                self._lock.notify_all()

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
            self._lock.notify_all()
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
            self.installing = True
        context = self.context
        try:
            context._slots = prepared_slots
            context._active_key = next(reversed(prepared_slots), None)
            self.failed = False
            self._apply_state(next_state)
            context.collection_list_changed.emit()
        finally:
            with self._lock:
                self.installing = False
                self._lock.notify_all()
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
        with self._lock:
            self._lock.notify_all()
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

    def apply(self, snapshot, *, prepared=None):
        self._apply_state(ProjectViewState.capture(snapshot, self.context._entry_labels_exact), prepared=prepared)

    def _apply_state(self, state, *, prepared=None):
        context, old = self.context, self.state
        entries_changed = False
        previous_collection = context.collection
        if not self.installing and _same_entry_scope(old.snapshot, state.snapshot):
            applied = None
            if prepared is not None and prepared.matches(context.slots.values(), old.snapshot):
                applied = prepared.apply()
            entries_changed = (
                sync_projected_entries(context.slots.values(), old.snapshot, state.snapshot)
                if applied is None
                else applied
            )
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
        if entries_changed and context.collection is not previous_collection:
            context.collection_changed.emit(context.collection)

    def diverged(self):
        if self.store is None:
            return False
        if QThread.currentThread() == self.context.thread():
            snapshot = self.state.snapshot if self.opening else self.store.snapshot()
            return self._diverged_snapshot(snapshot)
        # The durable commit can precede its queued Qt delivery. A background
        # save waits for that delivery instead of treating the old view as an
        # unsubmitted edit and pausing autosave. Never wait on the Qt thread.
        while True:
            with self._lock:
                self._lock.wait_for(
                    lambda: (
                        self._closed
                        or (
                            not self.installing
                            and self._deliveries == 0
                            and (self._token is not None or self._sequence <= self._applied_sequence)
                        )
                    )
                )
                if self._closed:
                    raise DomainError(
                        ErrorCategory.PREREQUISITE, "PROJECT_VIEW_UNAVAILABLE", "工程视图已关闭，无法核验保存。"
                    )
                sequence = self._sequence
                token = self._token
                installed = self.state.snapshot
            # Creation checks the old workbench inside its own open transaction.
            # That transaction can only finish after this worker returns. Its
            # authority is the installed view, not the pending next project.
            snapshot = installed if token is not None else self.store.snapshot()
            diverged = self._diverged_snapshot(snapshot)
            with self._lock:
                if (
                    not self._closed
                    and not self.installing
                    and self._token == token
                    and sequence == self._sequence
                    and self._deliveries == 0
                    and self.state.snapshot is installed
                ):
                    return diverged

    def _diverged_snapshot(self, snapshot):
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
