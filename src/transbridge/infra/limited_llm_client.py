"""Concurrency-limited transparent decorator for :class:`LLMClient`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time

from transbridge.application.translation.ai_request_budget import (
    AiRequestBudget,
    AiRequestCancelledError,
    EventState,
)
from transbridge.infra.llm_client import LLMClient


@dataclass(frozen=True, slots=True)
class LimitedLlmCallMetrics:
    admission_wait_ms: int
    in_flight_at_admission: int
    peak_in_flight: int


class LimitedLLMClient(LLMClient):
    """Acquire a shared run budget around each logical LLM call.

    Multiple decorators can share the same ``AiRequestBudget``.  ``cancel`` is
    forwarded to the provider and also invalidates calls that were already
    waiting in this decorator, while calls started after cancellation may use
    the rebuilt delegate client as required by the ``LLMClient`` contract.
    """

    def __init__(
        self,
        delegate: LLMClient,
        budget: AiRequestBudget,
        *,
        cancel_event: EventState | None = None,
        pause_event: EventState | None = None,
    ) -> None:
        self._delegate = delegate
        self._budget = budget
        self._cancel_event = cancel_event
        self._pause_event = pause_event
        self._cancel_lock = threading.Lock()
        self._cancel_generation = 0
        self._call_state = threading.local()

    @property
    def delegate(self) -> LLMClient:
        return self._delegate

    @property
    def budget(self) -> AiRequestBudget:
        return self._budget

    @property
    def last_call_metrics(self) -> LimitedLlmCallMetrics | None:
        return getattr(self._call_state, "metrics", None)

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        return self.chat_prepared(lambda: messages, max_tokens)

    def chat_prepared(self, messages_factory: Callable[[], list[dict]], max_tokens: int = 0) -> str:
        """Acquire admission before constructing potentially expensive messages."""

        self._call_state.metrics = None
        generation = self._generation()
        with self._acquire(generation):
            self._raise_if_cancelled(generation)
            messages = messages_factory()
            self._raise_if_cancelled(generation)
            return self._delegate.chat(messages, max_tokens)

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        return self.chat_stream_prepared(lambda: messages, max_tokens, chunk_callback)

    def chat_stream_prepared(
        self,
        messages_factory: Callable[[], list[dict]],
        max_tokens: int,
        chunk_callback,
    ) -> str:
        """Streaming counterpart of :meth:`chat_prepared`."""

        self._call_state.metrics = None
        generation = self._generation()
        with self._acquire(generation):
            self._raise_if_cancelled(generation)
            messages = messages_factory()
            self._raise_if_cancelled(generation)
            return self._delegate.chat_stream(messages, max_tokens, chunk_callback)

    def chat_stream_with_tools(self, messages, max_tokens, tools, chunk_callback):
        """Forward a native tool round while retaining shared admission control."""

        self._call_state.metrics = None
        generation = self._generation()
        with self._acquire(generation):
            self._raise_if_cancelled(generation)
            return self._delegate.chat_stream_with_tools(messages, max_tokens, tools, chunk_callback)

    def cancel(self) -> None:
        # Advance first so waiting callers cannot start while provider
        # cancellation/recreation is in progress.
        with self._cancel_lock:
            self._cancel_generation += 1
        self._budget.notify_state_changed()
        self._delegate.cancel()

    def _generation(self) -> int:
        with self._cancel_lock:
            return self._cancel_generation

    def _acquire(self, generation: int):
        started = time.perf_counter()
        lease = self._budget.acquire(
            cancel_event=self._cancel_event,
            pause_event=self._pause_event,
            is_cancelled=lambda: self._generation_changed(generation),
        )
        snapshot = self._budget.snapshot()
        self._call_state.metrics = LimitedLlmCallMetrics(
            admission_wait_ms=round((time.perf_counter() - started) * 1000),
            in_flight_at_admission=snapshot.in_flight,
            peak_in_flight=snapshot.peak_in_flight,
        )
        return lease

    def _generation_changed(self, generation: int) -> bool:
        with self._cancel_lock:
            return generation != self._cancel_generation

    def _raise_if_cancelled(self, generation: int) -> None:
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise AiRequestCancelledError("AI request cancelled before provider call")
        if self._generation_changed(generation):
            raise AiRequestCancelledError("AI request cancelled before provider call")


__all__ = ["LimitedLLMClient", "LimitedLlmCallMetrics"]
