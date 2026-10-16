"""Capture immutable entry batches and compute one isolated terminology preview."""

from __future__ import annotations

from threading import Event
from time import perf_counter
from types import MappingProxyType
from weakref import finalize

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal

from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput, ProfileEntryProjector


def profile_snapshot_key(profile, base):
    profile_key = (
        None
        if profile is None
        else (profile.project_id, profile.profile_id, profile.revision, profile.content_digest, profile.name)
    )
    base_key = None if base is None else (base.snapshot_identity, base.status, base.diagnostics)
    return profile_key, base_key


def preview_identity(ctx, scope):
    return scope, ctx.active_version_identity, ctx.project_revision, ctx.variant_revision


class _Signals(QObject):
    finished = pyqtSignal(int, object, object)


class _ProjectionCall(QRunnable):
    def __init__(self, generation, profile, base, chunks, prepared, cancelled, closed, signals):
        super().__init__()
        self.generation, self.profile, self.base = generation, profile, base
        self.chunks, self.prepared = chunks, prepared
        self.cancelled, self.closed, self.signals = cancelled, closed, signals

    def run(self):
        if self.cancelled.is_set() or self.closed.is_set():
            return
        result, error = None, None
        try:
            projector = self.prepared or ProfileEntryProjector(self.profile, self.base)
            output = {}
            for chunk in self.chunks:
                for entry in chunk:
                    if self.cancelled.is_set() or self.closed.is_set():
                        return
                    output[entry.entry_key] = projector.project_input(entry)
            result = projector, MappingProxyType(output)
        except Exception as exc:  # noqa: BLE001 - retain the failure for the owning UI
            error = exc
        if not self.cancelled.is_set() and not self.closed.is_set():
            self.signals.finished.emit(self.generation, result, error)


class TerminologyPreviewCoordinator(QObject):
    """Own profile identity, bounded capture, cancellation and atomic display results."""

    changed = pyqtSignal()

    def __init__(self, parent, *, identity, thread_pool=None, batch_size=256, capture_budget_ms=4):
        super().__init__(parent)
        self._identity = identity
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._batch_size = batch_size
        self._capture_budget = capture_budget_ms / 1000
        self._closed, self._cancelled = Event(), Event()
        parent.destroyed.connect(finalize(parent, self._closed.set))
        self._signals = _Signals()
        self._signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._capture_batch)
        self._generation = 0
        self._entries = ()
        self._chunks = []
        self._offset = 0
        self._request_identity = None
        self._key = profile_snapshot_key(None, None)
        self._prepared = None
        self.profile = self.base = None
        self.projection = MappingProxyType({})
        self.pending = False
        self.error = ""
        self._window = None
        self._closing_owner = None
        parent.installEventFilter(self)
        self._watch_window()

    @property
    def message(self):
        if self.error:
            return f"译名方案预览失败：{self.error}。当前保留项目译文，请重新选择方案重试。"
        return "正在生成译名方案预览；暂时显示项目译文。" if self.pending else ""

    def set_profile(self, profile, base=None):
        key = profile_snapshot_key(profile, base)
        if self._closed.is_set() or (key == self._key and not self.error):
            return False
        self._key, self.profile, self.base = key, profile, base
        self._prepared = None
        self._start()
        return True

    def set_entries(self, entries):
        # The owner replaces this list on collection refresh. Mutable entry fields
        # are copied in bounded main-thread batches, never read by the worker.
        self._entries = entries
        self._start()

    def invalidate_context(self, *_args):
        if self.set_profile(None):
            self.changed.emit()

    @property
    def profile_label(self):
        return None if self.profile is None else self.profile.name

    def _start(self):
        self._cancelled.set()
        self._cancelled = Event()
        self._timer.stop()
        self._generation += 1
        self.projection = MappingProxyType({})
        self.error = ""
        self.pending = self.profile is not None and bool(self._entries) and not self._closed.is_set()
        self._request_identity = self._identity()
        self._chunks, self._offset = [], 0
        if self.pending:
            self._timer.start(0)

    def _is_current(self):
        if self._closing_owner is not None:
            self._close_if_hidden(self._closing_owner)
        return not self._closed.is_set() and self._request_identity == self._identity()

    def _discard_stale(self):
        self._cancelled.set()
        self._timer.stop()
        self._chunks = []
        self.pending = False
        if not self._closed.is_set():
            self.error = "条目上下文已变化，请刷新当前列表或重新选择译名方案"
            self.changed.emit()

    def _capture_batch(self):
        if not self._is_current():
            self._discard_stale()
            return
        chunk = []
        deadline = perf_counter() + self._capture_budget
        try:
            while self._offset < len(self._entries) and len(chunk) < self._batch_size:
                chunk.append(ProfileEntryInput.capture(self._entries[self._offset]))
                self._offset += 1
                if perf_counter() >= deadline:
                    break
        except Exception as exc:  # noqa: BLE001 - malformed input remains visible, with no partial application
            self._failed(exc)
            return
        self._chunks.append(tuple(chunk))
        if self._offset < len(self._entries):
            self._timer.start(0)
            return
        self._pool.start(
            _ProjectionCall(
                self._generation,
                self.profile,
                self.base,
                tuple(self._chunks),
                self._prepared,
                self._cancelled,
                self._closed,
                self._signals,
            )
        )
        self._chunks = []

    def _finished(self, generation, result, error):
        if generation != self._generation:
            return
        if not self._is_current():
            self._discard_stale()
            return
        if error is not None:
            self._failed(error)
            return
        self._prepared, self.projection = result
        self.pending = False
        self.changed.emit()

    def _failed(self, error):
        self._cancelled.set()
        self._chunks = []
        self.pending = False
        self.error = str(error)
        self.changed.emit()

    def close(self):
        self._closed.set()
        self._cancelled.set()
        self._timer.stop()
        self._chunks = []
        self.pending = False
        for owner in (self.parent(), self._window):
            if owner is not None and not sip.isdeleted(owner):
                owner.removeEventFilter(self)

    def _watch_window(self):
        window = self.parent().window()
        if window is not self._window:
            if self._window is not None and self._window is not self.parent() and not sip.isdeleted(self._window):
                self._window.removeEventFilter(self)
            self._window = window
            if window is not self.parent():
                window.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        closed = self.__dict__.get("_closed")
        if closed is None or closed.is_set():
            return False
        if event.type() == QEvent.Type.Close:
            self._closing_owner = watched
            QTimer.singleShot(0, lambda: self._close_if_hidden(watched))
        elif watched is self.parent() and event.type() in (QEvent.Type.ParentChange, QEvent.Type.Show):
            self._watch_window()
        return super().eventFilter(watched, event)

    def _close_if_hidden(self, owner):
        closed = self.__dict__.get("_closed")
        if closed is None:
            return
        if not closed.is_set() and (sip.isdeleted(owner) or not owner.isVisible()):
            self.close()
        self._closing_owner = None


__all__ = ["TerminologyPreviewCoordinator", "preview_identity", "profile_snapshot_key"]
