# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare immutable full-source reading ranks outside the Qt event loop."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from threading import Event
from time import perf_counter
from types import MappingProxyType

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView

from transbridge.application.io import FormatId
from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.plugin_dialogue_order import dialogue_order_from_snapshot
from transbridge.parser.plugin.dialogue_order_binary import DialogueOrderCancelled
from transbridge.parser.plugin.dialogue_order_evidence import read_dialogue_order_evidence

_logger = logging.getLogger(__name__)


class _SourceOrder:
    ordered_keys = ()
    ranks = MappingProxyType({})
    source_keys = None
    source_rows = None


@dataclass(frozen=True, slots=True)
class _PreparedOrder:
    ordered_keys: tuple
    ranks: object
    source_keys: tuple
    source_rows: tuple


class _Lifetime:
    def __init__(self):
        self.closed = Event()

    def close(self, *_args):
        self.closed.set()


class _Cancellation:
    def __init__(self, cancelled, closed):
        self.cancelled, self.closed = cancelled, closed

    @property
    def is_cancelled(self):
        return self.cancelled.is_set() or self.closed.is_set()


class _Signals(QObject):
    finished = pyqtSignal(int, object, object)


class _ReadingCall(QRunnable):
    def __init__(self, generation, entries, snapshot, plugin, cancellation, signals):
        super().__init__()
        self.generation, self.entries = generation, entries
        self.snapshot, self.plugin = snapshot, plugin
        self.cancellation, self.signals = cancellation, signals

    def run(self):
        if self.cancellation.is_cancelled:
            return
        result, error = None, None
        try:
            evidence = (
                dialogue_order_from_snapshot(self.snapshot, cancellation=self.cancellation)
                if self.snapshot is not None
                else read_dialogue_order_evidence(self.plugin, cancellation=self.cancellation)
            )
            view = build_dialogue_reading_view(
                self.entries, evidence, cancellation=lambda: self.cancellation.is_cancelled
            )
            keys = tuple(view.ordered_keys)
            ranks = MappingProxyType({key: rank for rank, key in enumerate(keys)})
            source_keys = tuple(entry.identity for entry in self.entries)
            fallback = len(ranks)
            source_rows = tuple(
                sorted(range(len(source_keys)), key=lambda row: ranks.get(source_keys[row], fallback + row))
            )
            result = _PreparedOrder(keys, ranks, source_keys, source_rows)
        except DialogueOrderCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - report failures on the owning UI thread
            error = exc
        if not self.cancellation.is_cancelled:
            self.signals.finished.emit(self.generation, result, error)


class DialogueReadingOrderCoordinator(QObject):
    """Capture structure in bounded batches and deliver only current display ranks."""

    def __init__(self, preview, *, thread_pool=None, batch_size=256, capture_budget_ms=4):
        super().__init__(preview)
        self.preview = preview
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._batch_size, self._capture_budget = batch_size, capture_budget_ms / 1000
        self._lifetime = _Lifetime()
        self._closed, self._cancelled = self._lifetime.closed, Event()
        preview.destroyed.connect(self._lifetime.close)
        self._generation = 0
        self._request_identity = self._completed_identity = None
        self._completed_entries = ()
        self._completed_keys = ()
        self._completed_ranks = _SourceOrder.ranks
        self._completed_source_keys = self._completed_source_rows = None
        self._entries, self._captured = (), []
        self._offset = 0
        self._same_structure = False
        self._pending = None
        self._signals = _Signals()
        self._signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._capture_batch)
        self._delivery_timer = QTimer(self)
        self._delivery_timer.setSingleShot(True)
        self._delivery_timer.timeout.connect(self._deliver)
        preview._table.itemDelegate().closeEditor.connect(self._editor_closed)
        self._window = None
        preview.installEventFilter(self)
        self._watch_window()

    def _identity(self):
        context = self.preview._ctx
        slot = context.active_slot
        return (
            context.active_version_identity,
            context.active_key,
            id(slot),
            id(getattr(slot, "source_snapshot", None)),
            id(getattr(slot, "plugin", None)),
            id(getattr(slot, "reading_source_entries", None)),
        )

    def set_entries(self, entries):
        if self._closed.is_set():
            return
        identity = self._identity()
        self.invalidate_context()
        self._request_identity = identity
        self._entries = entries
        self._captured, self._offset = [], 0
        self._same_structure = identity == self._completed_identity and len(entries) == len(self._completed_entries)
        self.preview._table.set_default_entry_order(
            self._completed_keys if identity == self._completed_identity else (),
            ranks=self._completed_ranks if identity == self._completed_identity else _SourceOrder.ranks,
            source_keys=self._completed_source_keys if identity == self._completed_identity else None,
            source_rows=self._completed_source_rows if identity == self._completed_identity else None,
            render=False,
        )
        slot = self.preview._ctx.active_slot
        snapshot, plugin = getattr(slot, "source_snapshot", None), getattr(slot, "plugin", None)
        if not entries or (snapshot is None and plugin is None):
            return
        if snapshot is not None and snapshot.format_id is not FormatId.PLUGIN_SSE:
            return
        prepared = getattr(slot, "reading_source_entries", None)
        if prepared is not None:
            if identity == self._completed_identity and prepared is self._completed_entries:
                return
            self._captured = prepared
            self._start_worker()
            return
        self._timer.start(0)

    def invalidate_context(self, *_args):
        self._cancelled.set()
        self._cancelled = Event()
        self._generation += 1
        self._timer.stop()
        self._delivery_timer.stop()
        self._pending = None
        self._captured = []

    def _is_current(self):
        return (
            not self._closed.is_set()
            and not getattr(self.preview._ctx, "project_installing", False)
            and self._request_identity == self._identity()
        )

    def _capture_batch(self):
        if not self._is_current():
            return
        deadline, count = perf_counter() + self._capture_budget, 0
        try:
            while self._offset < len(self._entries) and count < self._batch_size:
                entry = self._entries[self._offset]
                captured = ReadingEntry(entry.identity, entry.context, tuple(entry.metadata))
                if self._same_structure and captured != self._completed_entries[self._offset]:
                    self._same_structure = False
                self._captured.append(captured)
                self._offset += 1
                count += 1
                if perf_counter() >= deadline:
                    break
        except Exception as exc:  # noqa: BLE001 - malformed input keeps the existing source order
            self._failed(exc)
            return
        if self._offset < len(self._entries):
            self._timer.start(0)
            return
        if self._same_structure:
            self._captured = []
            return
        self._start_worker()

    def _start_worker(self):
        slot = self.preview._ctx.active_slot
        self._pool.start(
            _ReadingCall(
                self._generation,
                tuple(self._captured),
                getattr(slot, "source_snapshot", None),
                getattr(slot, "plugin", None),
                _Cancellation(self._cancelled, self._closed),
                self._signals,
            )
        )

    def _finished(self, generation, result, error):
        if generation != self._generation or not self._is_current():
            return
        if error is not None:
            self._failed(error)
            return
        self._pending = generation, result
        self._deliver()

    def _deliver(self):
        if self._pending is None or not self._is_current():
            return
        generation, result = self._pending
        if generation != self._generation:
            return
        # A delayed result must not reset the model under an uncommitted F2 draft.
        if self.preview._table.state() == QAbstractItemView.State.EditingState:
            return
        self._pending = None
        self._completed_identity = self._request_identity
        self._completed_entries = tuple(self._captured)
        self._completed_keys = tuple(result.ordered_keys)
        self._completed_ranks = result.ranks
        self._completed_source_keys, self._completed_source_rows = result.source_keys, result.source_rows
        self._captured = []
        self.preview._table.set_default_entry_order(
            self._completed_keys,
            ranks=self._completed_ranks,
            source_keys=self._completed_source_keys,
            source_rows=self._completed_source_rows,
        )

    def _editor_closed(self, *_args):
        if not self._closed.is_set():
            self._delivery_timer.start(0)

    def _failed(self, error):
        self._cancelled.set()
        self._captured = []
        _logger.warning("WORKBENCH_DIALOGUE_ORDER_FAILED: keeping source display order: %s", error, exc_info=error)
        self._completed_identity = None
        self._completed_entries = ()
        self._pending = self._generation, _SourceOrder()
        self._deliver()

    def close(self):
        self._closed.set()
        self.invalidate_context()
        self._entries, self._completed_entries = (), ()
        self._completed_keys, self._completed_ranks = (), _SourceOrder.ranks
        self._completed_source_keys = self._completed_source_rows = None
        for owner in (self.preview, self._window):
            if owner is not None and not sip.isdeleted(owner):
                owner.removeEventFilter(self)

    def _watch_window(self):
        window = self.preview.window()
        if window is not self._window:
            if self._window is not None and self._window is not self.preview and not sip.isdeleted(self._window):
                self._window.removeEventFilter(self)
            self._window = window
            if window is not self.preview:
                window.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if self._closed.is_set():
            return False
        if event.type() == QEvent.Type.Close:
            QTimer.singleShot(0, lambda: self._close_if_hidden(watched))
        elif watched is self.preview and event.type() in (QEvent.Type.ParentChange, QEvent.Type.Show):
            self._watch_window()
        return super().eventFilter(watched, event)

    def _close_if_hidden(self, owner):
        if not self._closed.is_set() and (sip.isdeleted(owner) or not owner.isVisible()):
            self.close()
