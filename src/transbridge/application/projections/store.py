"""Revision-gated projection store with rebuildable, disposable subscriptions."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity

from .models import ProjectionDecision, ProjectionEvent, ProjectionSnapshot

ProjectionListener = Callable[[ProjectionSnapshot | None], None]


class ProjectionSubscription:
    def __init__(self, close_callback: Callable[[], None]) -> None:
        self._close_callback = close_callback
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close_callback()


class ProjectionStore:
    def __init__(self, snapshot: ProjectionSnapshot | None = None) -> None:
        self._snapshot = snapshot
        self._listeners: dict[int, ProjectionListener] = {}
        self._subscriptions: dict[int, ProjectionSubscription] = {}
        self._next_listener = 0
        self._closed = False
        self._seen_events: set[str] = set()
        self._lock = RLock()

    def snapshot(self) -> ProjectionSnapshot | None:
        with self._lock:
            if self._snapshot is None:
                return None
            return ProjectionSnapshot(
                self._snapshot.stream_id,
                self._snapshot.revision,
                self._snapshot.persisted_revision,
                self._snapshot.to_dict()["values"],
            )

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)

    def subscribe(self, listener: ProjectionListener, *, replay: bool = True) -> ProjectionSubscription:
        with self._lock:
            if self._closed:
                raise RuntimeError("projection store is closed")
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            snapshot = self.snapshot() if replay else None

            def unsubscribe() -> None:
                with self._lock:
                    self._listeners.pop(token, None)
                    self._subscriptions.pop(token, None)

            subscription = ProjectionSubscription(unsubscribe)
            self._subscriptions[token] = subscription
        if replay:
            try:
                listener(snapshot)
            except Exception:
                subscription.close()
                raise
        return subscription

    def apply(self, event: ProjectionEvent) -> ProjectionDecision:
        with self._lock:
            self._ensure_open()
            if event.event_id in self._seen_events:
                return self._ignored("PROJECTION_EVENT_DUPLICATE", "The projection event was already applied.")
            current = self._snapshot
            if current is not None and event.stream_id != current.stream_id:
                return self._ignored(
                    "PROJECTION_STREAM_MISMATCH",
                    "The projection event belongs to another aggregate stream.",
                )
            if current is not None and event.revision <= current.revision:
                self._seen_events.add(event.event_id)
                return self._ignored("PROJECTION_EVENT_STALE", "The projection event revision is stale.")
            if current is not None and event.revision != current.revision + 1:
                return self._ignored(
                    "PROJECTION_EVENT_GAP",
                    "A projection event is missing; rebuild from the aggregate snapshot.",
                )
            self._seen_events.add(event.event_id)
            self._snapshot = event.snapshot()
            diagnostics = self._notify(self._snapshot)
            return ProjectionDecision(True, self.snapshot(), diagnostics)

    def rebuild(self, snapshot: ProjectionSnapshot | None) -> ProjectionDecision:
        with self._lock:
            self._ensure_open()
            self._snapshot = snapshot
            self._seen_events.clear()
            diagnostics = self._notify(snapshot)
            return ProjectionDecision(True, self.snapshot(), diagnostics)

    def mark_persisted(self, revision: int) -> ProjectionDecision:
        with self._lock:
            self._ensure_open()
            current = self._snapshot
            if current is None:
                return self._ignored("PROJECTION_UNAVAILABLE", "There is no active projection.")
            if revision != current.revision:
                return self._ignored(
                    "PROJECTION_PERSISTED_REVISION_MISMATCH",
                    "The persisted acknowledgement does not match the aggregate revision.",
                )
            self._snapshot = ProjectionSnapshot(
                current.stream_id,
                current.revision,
                revision,
                current.to_dict()["values"],
            )
            diagnostics = self._notify(self._snapshot)
            return ProjectionDecision(True, self.snapshot(), diagnostics)

    def close(self) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.values())
            self._listeners.clear()
            self._subscriptions.clear()
            self._seen_events.clear()
            self._closed = True
        for subscription in subscriptions:
            subscription.close()

    def _notify(self, snapshot: ProjectionSnapshot | None) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        for listener in tuple(self._listeners.values()):
            try:
                listener(snapshot)
            except Exception:
                diagnostics.append(
                    Diagnostic(
                        "PROJECTION_LISTENER_FAILED",
                        "A projection listener failed after the aggregate state committed.",
                        DiagnosticSeverity.WARNING,
                    )
                )
        return tuple(diagnostics)

    def _ignored(self, code: str, message: str) -> ProjectionDecision:
        return ProjectionDecision(
            False,
            self.snapshot(),
            (Diagnostic(code, message, DiagnosticSeverity.WARNING),),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("projection store is closed")


__all__ = ["ProjectionListener", "ProjectionStore", "ProjectionSubscription"]
