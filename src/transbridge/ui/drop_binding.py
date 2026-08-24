"""Thin Qt binding for safe local-file drops.

No candidate is dispatched automatically.  A host must display the resolution
and explicitly call :meth:`confirm` after the user accepts the proposed plan.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, pyqtSignal

from .drop_router import DropResolution, DropResolutionStatus, DropRouter


class SafeDropBinding(QObject):
    resolution_ready = pyqtSignal(object)
    intent_confirmed = pyqtSignal(object, object)
    dismissed = pyqtSignal()

    def __init__(
        self,
        target: QObject,
        *,
        router: DropRouter | None = None,
        resolver: Callable[[tuple[str, ...]], DropResolution] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or target)
        if router is not None and resolver is not None:
            raise ValueError("pass router or resolver, not both")
        self._target = target
        self._resolve = resolver or (router or DropRouter()).resolve
        self._closed = False
        set_accept_drops = getattr(target, "setAcceptDrops", None)
        if callable(set_accept_drops):
            set_accept_drops(True)
        target.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._closed or watched is not self._target:
            return False
        if event.type() is QEvent.Type.DragEnter:
            if _has_file_urls(event):
                event.acceptProposedAction()
                return True
            return False
        if event.type() is QEvent.Type.Drop:
            paths = _local_paths(event)
            resolution = self._resolve(paths)
            self.resolution_ready.emit(resolution)
            event.acceptProposedAction()
            return True
        return False

    def inspect_paths(self, paths: tuple[str, ...]) -> DropResolution:
        """Non-event entry for tests and other Qt local-file sources."""

        if self._closed:
            return DropResolution.cancelled()
        resolution = self._resolve(paths)
        self.resolution_ready.emit(resolution)
        return resolution

    def confirm(self, resolution: DropResolution) -> bool:
        """Emit the inert canonical request only after an explicit UI confirm."""

        if self._closed or resolution.status is not DropResolutionStatus.CANDIDATE:
            return False
        candidate = resolution.candidate
        if candidate is None:
            return False
        self.intent_confirmed.emit(candidate.intent_id, candidate.payload_mapping())
        return True

    def dismiss(self) -> DropResolution:
        self.dismissed.emit()
        return DropResolution.cancelled()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._target.removeEventFilter(self)


def _has_file_urls(event: QEvent) -> bool:
    mime_data = getattr(event, "mimeData", lambda: None)()
    return bool(mime_data is not None and mime_data.hasUrls() and any(url.isLocalFile() for url in mime_data.urls()))


def _local_paths(event: QEvent) -> tuple[str, ...]:
    mime_data = getattr(event, "mimeData", lambda: None)()
    if mime_data is None or not mime_data.hasUrls():
        return ()
    return tuple(url.toLocalFile() for url in mime_data.urls() if url.isLocalFile())


__all__ = ["SafeDropBinding"]
