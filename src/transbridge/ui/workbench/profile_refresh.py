"""Read applied and preview scheme revisions away from the Qt thread."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Event
from weakref import finalize

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal

_READ = object()


@dataclass(frozen=True, slots=True)
class ProfileRefreshResult:
    service: object
    profiles: tuple
    selected: object
    base: object
    selection_error: str = ""
    applied: object = None


def _read(factory, identity, service, selection):
    project_id, variant_id = identity
    service = service or factory.profile_service_for(project_id)
    profiles = service.list_profile_summaries(project_id)
    applied = service.selected_revision(project_id, variant_id)
    selected = applied
    selection_error = ""
    if selection is not _READ:
        try:
            if selection is None:
                selected = None
            else:
                summary = next((item for item in profiles if item.profile_id == selection), None)
                revision = None if summary is None else summary.latest_published_revision
                selected = None if revision is None else service.published_revision(selection, revision)
                if selected is None or selected.project_id != project_id:
                    raise RuntimeError("该译名方案没有可读取的已发布版本，请重新选择。")
        except Exception as exc:  # noqa: BLE001 - recover the applied revision after a failed preview read
            selected = applied
            selection_error = str(exc)
    read_base = getattr(factory, "base_terminology_snapshot", None)
    base = read_base(project_id, variant_id) if callable(read_base) else None
    return ProfileRefreshResult(service, profiles, selected, base, selection_error, applied)


class _Signals(QObject):
    finished = pyqtSignal(int, object, object, object)


class _Call(QRunnable):
    def __init__(self, generation, identity, factory, service, selection, closed, signals):
        super().__init__()
        self.generation, self.identity, self.factory = generation, identity, factory
        self.service, self.selection = service, selection
        self.closed, self.signals = closed, signals

    def run(self):
        if self.closed.is_set():
            return
        result, error = None, None
        try:
            result = _read(self.factory, self.identity, self.service, self.selection)
        except Exception as exc:  # noqa: BLE001 - original failure delivered to the UI boundary
            error = exc
        if not self.closed.is_set():
            self.signals.finished.emit(self.generation, self.identity, result, error)


class ProfileRefreshCoordinator(QObject):
    """One active read and one latest request; obsolete results are discarded."""

    ready = pyqtSignal(object, object)
    failed = pyqtSignal(object, object)

    def __init__(self, parent, factory, *, identity, owner, thread_pool=None):
        super().__init__(parent)
        self._factory, self._identity, self._owner = factory, identity, owner
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._closed = Event()
        # finalize roots the callback until teardown, including cyclic GC where
        # ordinary destroyed lambdas may already have cleared closure/defaults.
        parent.destroyed.connect(finalize(parent, self._closed.set))
        owner.destroyed.connect(finalize(owner, self._closed.set))
        self._signals = _Signals()
        self._signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._generation = 0
        self._active = False
        self._queue = deque()
        self._window = None
        self._closing_owner = None
        owner.installEventFilter(self)
        self._watch_window()

    def request(self, identity, *, service=None, selection=_READ):
        if self._closed.is_set():
            return
        self._generation += 1
        self._queue.clear()
        self._queue.append((self._generation, identity, service, selection))
        self._start_next()

    def invalidate(self):
        self._generation += 1
        self._queue.clear()

    def _start_next(self):
        if self._active or not self._queue or self._closed.is_set():
            return
        generation, identity, service, selection = self._queue.popleft()
        self._active = True
        self._pool.start(_Call(generation, identity, self._factory, service, selection, self._closed, self._signals))

    def _finished(self, generation, identity, result, error):
        self._active = False
        if self._closing_owner is not None:
            self._close_if_hidden(self._closing_owner)
        if self._closed.is_set():
            return
        if generation == self._generation and identity == self._identity():
            if error is None:
                self.ready.emit(identity, result)
            else:
                self.failed.emit(identity, error)
        self._start_next()

    def close(self):
        self._closed.set()
        self._queue.clear()
        for owner in (self._owner, self._window):
            if owner is not None and not sip.isdeleted(owner):
                owner.removeEventFilter(self)

    def _watch_window(self):
        window = self._owner.window()
        if window is not self._window:
            if self._window is not None and self._window is not self._owner and not sip.isdeleted(self._window):
                self._window.removeEventFilter(self)
            self._window = window
            if window is not self._owner:
                window.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        # Qt can still send teardown events while cyclic GC is clearing the
        # Python wrapper, before the C++ event filter is disconnected.
        closed = self.__dict__.get("_closed")
        if closed is None or closed.is_set():
            return False
        if event.type() == QEvent.Type.Close:
            # The host may reject Close to protect an unsaved editor. Wait for
            # its final visibility before permanently cancelling this binding.
            self._closing_owner = watched
            QTimer.singleShot(0, lambda: self._close_if_hidden(watched))
        elif watched is self._owner and event.type() in (QEvent.Type.ParentChange, QEvent.Type.Show):
            self._watch_window()
        return super().eventFilter(watched, event)

    def _close_if_hidden(self, owner):
        closed = self.__dict__.get("_closed")
        if closed is None:
            return
        if not closed.is_set() and (sip.isdeleted(owner) or not owner.isVisible()):
            self.close()
        self._closing_owner = None


__all__ = ["ProfileRefreshCoordinator", "ProfileRefreshResult"]
