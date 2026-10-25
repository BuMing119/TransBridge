"""Bounded latest-state feedback with GUI polling and legacy signal delivery."""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from threading import RLock

from .task_run_presentation import concise_event


class TaskFeedback:
    """Keep recent progress and visible logs while preserving delivery barriers.

    Polling detaches values under a short lock and delivers on the GUI thread.
    Legacy signal delivery keeps publication ordered under the lock. Neither
    mode performs disk I/O or lets ordinary status updates grow without bound.
    """

    def __init__(
        self,
        *,
        progress: Callable,
        project_progress: Callable,
        log_batch: Callable,
        request_state: Callable | None = None,
        max_logs: int = 3000,
        max_states: int = 1024,
        batch_size: int = 100,
    ):
        if min(max_logs, max_states, batch_size) < 1:
            raise ValueError("Feedback limits must be positive")
        self._progress = progress
        self._project_progress = project_progress
        self._log_batch = log_batch
        self._request_state = request_state
        self._max_logs = max_logs
        self._max_states = max_states
        self._batch_size = min(batch_size, 100)
        self._lock = RLock()
        self._states = OrderedDict()
        self._logs = deque()
        self._omitted = None
        self._sequence = 0
        self._closed = False
        self._polling = False
        self._barriers = deque()

    def enable_polling(self) -> None:
        """Select GUI-owned delivery before starting the worker."""
        with self._lock:
            self._polling = True

    def progress(self, source: str, stage: str, current: int, total: int, message: str) -> None:
        with self._lock:
            key = ("progress", source) if self._polling else ("progress", source, stage)
            self._state(key, (source, stage, current, total, message), source)

    def project_progress(self, event) -> None:
        with self._lock:
            source = event.sources[0].key if event.sources else ""
            self._state(("project",) if self._polling else ("project", event.phase), event, source)

    def request_state(self, snapshot) -> None:
        with self._lock:
            self._state(("request",), snapshot, "")

    def log(self, source: str, text: str) -> None:
        # Discard per-entry translations/technical payloads before they become
        # GUI events. This is the same visible-event policy as the progress view.
        event = concise_event(text)
        if event is None:
            return
        with self._lock:
            if self._closed:
                return
            self._sequence += 1
            self._logs.append((self._sequence, "log", source, event))
            if len(self._logs) > self._max_logs:
                sequence, _, key, _ = self._logs.popleft()
                self._note_omitted(sequence, key)

    def drain(self, *, all_pending: bool = False) -> None:
        """Deliver at most one routine log batch; barriers can drain the bounded tail."""
        with self._lock:
            if not self._closed and not self._polling:
                self._deliver(self._take(all_pending=all_pending))

    def poll(self, *, all_pending: bool = False) -> None:
        """Detach a bounded delivery on the GUI thread; never hold locks while painting."""
        with self._lock:
            if not self._polling:
                return
            # Lifecycle barriers include the bounded log tail before completion.
            final = all_pending or bool(self._barriers)
            events = self._take(all_pending=final, log_chunk=self._max_logs + 1 if final else self._batch_size)
        self._deliver(events)

    def barrier(self, callback: Callable, *args) -> None:
        """Flush earlier feedback before an immediate source lifecycle event."""
        with self._lock:
            if not self._closed:
                if self._polling:
                    self._enqueue_barrier(callback, args)
                else:
                    self._deliver(self._take(all_pending=True))
                    callback(*args)

    def finish(self, callback: Callable, *args) -> None:
        """Stop accepting feedback, flush the tail, then publish final completion."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._polling:
                self._enqueue_barrier(callback, args)
            else:
                self._deliver(self._take(all_pending=True))
                callback(*args)

    def _enqueue_barrier(self, callback, args):
        self._sequence += 1
        self._barriers.append((self._sequence, "barrier", callback, args))

    def _state(self, key, payload, source):
        if self._closed:
            return
        self._sequence += 1
        self._states[key] = (self._sequence, key[0], key, payload)
        self._states.move_to_end(key)
        if len(self._states) > self._max_states:
            _, (sequence, _, _, _) = self._states.popitem(last=False)
            self._note_omitted(sequence, source)

    def _note_omitted(self, sequence, source):
        if self._omitted is None:
            self._omitted = (sequence, "omitted", source, 1)
        else:
            first, kind, key, count = self._omitted
            self._omitted = (min(sequence, first), kind, key, count + 1)

    def _take(self, *, all_pending, log_chunk=None):
        deliveries = []
        pending = sorted(
            (
                *self._states.values(),
                *self._logs,
                *self._barriers,
                *((self._omitted,) if self._omitted is not None else ()),
            ),
            key=lambda item: item[0],
        )
        limit = self._max_logs + 1 if all_pending else self._batch_size
        batch = []
        sent = 0

        def flush_logs():
            if batch:
                deliveries.append((self._log_batch, (tuple(batch),)))
                batch.clear()

        for item in pending:
            sequence, kind, key, payload = item
            if kind in {"log", "omitted"}:
                if sent == limit:
                    # Logs retain their own order and delivery budget, but a
                    # log flood must never hold the latest status behind them.
                    continue
                if kind == "omitted":
                    if self._omitted != item:
                        continue
                    self._omitted = None
                    payload = (
                        f"已省略 {payload} 条较早的过程消息（界面缓冲已达上限）；请结合任务结果与 LLM 日志查看详情。"
                    )
                else:
                    if not self._logs or self._logs[0][0] != sequence:
                        continue
                    self._logs.popleft()
                batch.append((key, payload))
                sent += 1
                if len(batch) == (log_chunk or self._batch_size):
                    flush_logs()
            elif kind == "barrier":
                self._barriers.popleft()
                flush_logs()
                deliveries.append((key, payload))
            else:
                if self._states.get(key) != item:
                    continue
                del self._states[key]
                flush_logs()
                if kind == "progress":
                    deliveries.append((self._progress, payload))
                elif kind == "request":
                    if self._request_state is not None:
                        deliveries.append((self._request_state, (payload,)))
                else:
                    deliveries.append((self._project_progress, (payload,)))
        flush_logs()
        return deliveries

    @staticmethod
    def _deliver(events):
        for callback, args in events:
            callback(*args)
