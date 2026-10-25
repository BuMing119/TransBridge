"""Bounded, source-fair execution of dependency-ordered translation batches."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, field
import threading
from typing import TYPE_CHECKING, Any

from transbridge.application.translation.ai_request_budget import EventState

if TYPE_CHECKING:
    from transbridge.ai_translator.batch_planner import Batch


@dataclass(frozen=True, slots=True)
class BatchExecutionSnapshot:
    active_batches: int
    ready_chains: int
    pending_runs: int
    peak_active_batches: int


@dataclass(slots=True)
class _Chain:
    items: Iterator
    next_item: Any


@dataclass(eq=False, slots=True)
class _Run:
    chains: Iterator[Iterable]
    operation: Callable[[Any], object]
    lane: Hashable
    stop_event: EventState | None
    pause_event: EventState | None
    ready: deque[_Chain] = field(default_factory=deque)
    active: int = 0
    exhausted: bool = False
    cancelled: bool = False
    error: BaseException | None = None

    @property
    def done(self) -> bool:
        return self.exhausted and not self.ready and self.active == 0

    def cancel_pending(self) -> None:
        self.cancelled = True
        self.ready.clear()
        self.chains = iter(())
        self.exhausted = True

    def fill(self, window: int) -> None:
        while not self.exhausted and len(self.ready) + self.active < window:
            try:
                items = iter(next(self.chains))
            except StopIteration:
                self.exhausted = True
                break
            try:
                first = next(items)
            except StopIteration:
                continue
            self.ready.append(_Chain(items, first))


class BatchExecutor:
    """Share a fixed worker pool across synchronous, independent source runs.

    A worker executes one batch at a time. Its dependency chain becomes ready
    again only after that operation returns, including required term extraction.
    Ready source lanes rotate; an idle or paused source reserves no workers.

    At most ``2 * max_workers`` chains per run are opened at once. The rest of
    the input remains lazy, so no future or queued callable is created per batch.
    Input iterables must be finite and cheap to advance; expensive work belongs
    in ``operation``. Nested calls from an operation to this executor are rejected
    rather than deadlocking. Independent postprocessing can use another pool
    while sharing the same model request budget.
    """

    _POLL_SECONDS = 0.05

    def __init__(self, max_workers: int) -> None:
        if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
            raise ValueError("max_workers must be a positive integer")
        self._max_workers = max_workers
        self._window = max_workers * 2
        self._condition = threading.Condition()
        self._lanes: dict[Hashable, deque[_Run]] = {}
        self._rotation: deque[Hashable] = deque()
        self._runs: set[_Run] = set()
        self._closed = False
        self._active = 0
        self._peak = 0
        self._worker_state = threading.local()
        self._threads = [
            threading.Thread(target=self._work, name=f"ai-batch-{index + 1}", daemon=True)
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def __enter__(self) -> BatchExecutor:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        self.shutdown()
        return False

    def snapshot(self) -> BatchExecutionSnapshot:
        with self._condition:
            return BatchExecutionSnapshot(
                active_batches=self._active,
                ready_chains=sum(len(run.ready) for run in self._runs),
                pending_runs=len(self._runs),
                peak_active_batches=self._peak,
            )

    def run[T](
        self,
        chains: Iterable[Iterable[T]],
        operation: Callable[[T], object],
        *,
        stop_event: EventState | None = None,
        pause_event: EventState | None = None,
        lane_key: Hashable | None = None,
    ) -> None:
        """Wait for this source's chains; propagate its first operation failure.

        Stop cancels unstarted batches and waits for already executing batches.
        A failure does the same, then raises the original exception. Other runs
        remain independent. ``pause_event.set()`` means running, as in workers.
        """

        if getattr(self._worker_state, "running", False):
            raise RuntimeError("a batch operation cannot nest run() on its own BatchExecutor")
        hash(lane_key)
        state = _Run(iter(chains), operation, lane_key, stop_event, pause_event)
        with self._condition:
            if self._closed:
                raise RuntimeError("BatchExecutor has been shut down")
            if stop_event is not None and stop_event.is_set():
                return
            state.fill(self._window)
            if state.done:
                return
            self._runs.add(state)
            if lane_key not in self._lanes:
                self._lanes[lane_key] = deque()
                self._rotation.append(lane_key)
            self._lanes[lane_key].append(state)
            self._condition.notify_all()
            try:
                while not state.done:
                    if stop_event is not None and stop_event.is_set():
                        state.cancel_pending()
                        self._condition.notify_all()
                    if not state.done:
                        self._condition.wait(self._POLL_SECONDS)
            except BaseException:
                state.cancel_pending()
                self._condition.notify_all()
                while state.active:
                    self._condition.wait(self._POLL_SECONDS)
                raise
            finally:
                self._remove_run(state)
            if state.error is not None:
                raise state.error

    def notify_state_changed(self) -> None:
        """Optionally wake workers immediately after pause/stop state changes."""

        with self._condition:
            self._condition.notify_all()

    def shutdown(self, *, wait: bool = True) -> None:
        """Cancel queued work and, by default, join all running batch workers."""

        if wait and getattr(self._worker_state, "running", False):
            raise RuntimeError("a batch operation cannot wait for its own executor shutdown")
        with self._condition:
            self._closed = True
            for state in self._runs:
                state.cancel_pending()
            self._condition.notify_all()
        if wait:
            for thread in self._threads:
                thread.join()

    def _remove_run(self, state: _Run) -> None:
        self._runs.remove(state)
        lane = self._lanes[state.lane]
        lane.remove(state)
        if not lane:
            del self._lanes[state.lane]
            self._rotation.remove(state.lane)
        self._condition.notify_all()

    def _next_ready(self) -> tuple[_Run, _Chain] | None:
        for lane_key in self._rotation:
            lane = self._lanes[lane_key]
            for index, state in enumerate(lane):
                if state.stop_event is not None and state.stop_event.is_set():
                    state.cancel_pending()
                    self._condition.notify_all()
                if state.pause_event is not None and not state.pause_event.is_set():
                    continue
                if state.ready:
                    self._rotation.rotate(-self._rotation.index(lane_key) - 1)
                    lane.rotate(-index - 1)
                    state.active += 1
                    self._active += 1
                    self._peak = max(self._peak, self._active)
                    return state, state.ready.popleft()
        return None

    def _work(self) -> None:
        self._worker_state.running = True
        while True:
            with self._condition:
                while not self._closed:
                    job = self._next_ready()
                    if job is not None:
                        break
                    self._condition.wait(self._POLL_SECONDS)
                else:
                    return
            state, chain = job
            error = None
            try:
                if (
                    not state.cancelled
                    and not self._closed
                    and (state.stop_event is None or not state.stop_event.is_set())
                ):
                    state.operation(chain.next_item)
            except BaseException as exc:
                error = exc
            with self._condition:
                state.active -= 1
                self._active -= 1
                if error is not None:
                    if state.error is None:
                        state.error = error
                    state.cancel_pending()
                if self._closed or state.cancelled or (state.stop_event is not None and state.stop_event.is_set()):
                    state.cancel_pending()
                else:
                    try:
                        try:
                            chain.next_item = next(chain.items)
                        except StopIteration:
                            pass
                        else:
                            state.ready.append(chain)
                        state.fill(self._window)
                    except BaseException as exc:
                        state.error = exc
                        state.cancel_pending()
                self._condition.notify_all()


def quest_batch_chains(batches: Iterable[Batch]) -> list[list[Batch]]:
    """Preserve explicit Quest order; unknown Quest batches are independent."""

    chains: list[list[Batch]] = []
    known: dict[str, list[Batch]] = {}
    for batch in batches:
        quest = (batch.quest_formid or "").strip()
        if not quest:
            chains.append([batch])
        elif quest in known:
            known[quest].append(batch)
        else:
            known[quest] = [batch]
            chains.append(known[quest])
    return chains


__all__ = ["BatchExecutionSnapshot", "BatchExecutor", "quest_batch_chains"]
