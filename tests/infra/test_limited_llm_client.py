from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from transbridge.application.translation.ai_request_budget import (
    AiRequestBudget,
    AiRequestCancelledError,
)
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_tool_calling import LlmToolCall, LlmToolDefinition, LlmTurn
from transbridge.infra.prepared_llm_request import PreparedLlmRequest


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition was not reached before timeout")


class _ConcurrencyTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.started = 0
        self.release = threading.Event()

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.started += 1
            self.peak = max(self.peak, self.active)

    def leave(self) -> None:
        with self._lock:
            self.active -= 1


class _BlockingClient:
    def __init__(self, tracker: _ConcurrencyTracker) -> None:
        self.tracker = tracker
        self.cancel_count = 0

    def chat(self, messages, max_tokens=0):
        self.tracker.enter()
        try:
            assert self.tracker.release.wait(2)
            return str(messages[0]["content"])
        finally:
            self.tracker.leave()

    def chat_stream(self, messages, max_tokens, chunk_callback):
        result = self.chat(messages, max_tokens)
        chunk_callback(result)
        return result

    def cancel(self) -> None:
        self.cancel_count += 1


def test_two_clients_share_one_hard_in_flight_limit() -> None:
    budget = AiRequestBudget(2)
    tracker = _ConcurrencyTracker()
    left = LimitedLLMClient(_BlockingClient(tracker), budget)
    right = LimitedLLMClient(_BlockingClient(tracker), budget)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit((left if index % 2 else right).chat, [{"content": str(index)}], 100) for index in range(8)
        ]
        _wait_until(lambda: tracker.started == 2)
        time.sleep(0.05)
        assert tracker.started == 2
        assert budget.snapshot().in_flight == 2
        assert budget.snapshot().waiting == 6
        tracker.release.set()
        assert [future.result(timeout=2) for future in futures] == [str(index) for index in range(8)]

    assert tracker.peak == 2
    assert budget.snapshot().in_flight == 0
    assert budget.snapshot().waiting == 0
    assert budget.snapshot().peak_in_flight == 2


def test_different_runs_have_independent_budgets() -> None:
    tracker = _ConcurrencyTracker()
    clients = [
        LimitedLLMClient(_BlockingClient(tracker), AiRequestBudget(1)),
        LimitedLLMClient(_BlockingClient(tracker), AiRequestBudget(1)),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(client.chat, [{"content": "ok"}]) for client in clients]
        _wait_until(lambda: tracker.started == 2)
        tracker.release.set()
        assert [future.result(timeout=2) for future in futures] == ["ok", "ok"]
    assert tracker.peak == 2


def test_provider_exception_releases_lease() -> None:
    class _FailOnceClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, _messages, max_tokens=0):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("provider failed")
            return "recovered"

        def cancel(self) -> None:
            pass

    budget = AiRequestBudget(1)
    client = LimitedLLMClient(_FailOnceClient(), budget)
    with pytest.raises(RuntimeError, match="provider failed"):
        client.chat([])
    assert budget.snapshot().in_flight == 0
    assert client.chat([]) == "recovered"


def test_stream_callback_exception_releases_lease() -> None:
    class _StreamingClient:
        def chat(self, _messages, max_tokens=0):
            return "unused"

        def chat_stream(self, _messages, _max_tokens, callback):
            callback("chunk")
            return "chunk"

        def cancel(self) -> None:
            pass

    budget = AiRequestBudget(1)
    client = LimitedLLMClient(_StreamingClient(), budget)

    def fail(_chunk: str) -> None:
        raise LookupError("callback failed")

    with pytest.raises(LookupError, match="callback failed"):
        client.chat_stream([], 10, fail)
    assert budget.snapshot().in_flight == 0


def test_native_tool_stream_is_forwarded_without_falling_back_to_text() -> None:
    expected = LlmTurn(tool_calls=(LlmToolCall("call-1", "get_statistics", {}),), stop_reason="tool_calls")
    definition = LlmToolDefinition("get_statistics", "Return statistics", {"type": "object"})

    class _ToolClient:
        def __init__(self) -> None:
            self.received = None

        def chat_stream_with_tools(self, messages, max_tokens, tools, callback):
            self.received = (messages, max_tokens, tools)
            callback("checking")
            return expected

        def cancel(self) -> None:
            pass

    delegate = _ToolClient()
    client = LimitedLLMClient(delegate, AiRequestBudget(1))
    chunks = []

    result = client.chat_stream_with_tools([{"role": "user", "content": "stats"}], 64, [definition], chunks.append)

    assert result is expected
    assert delegate.received == ([{"role": "user", "content": "stats"}], 64, [definition])
    assert chunks == ["checking"]
    assert client.budget.snapshot().in_flight == 0


def test_pause_blocks_provider_start_and_cancel_invalidates_only_existing_waiters() -> None:
    budget = AiRequestBudget(1)
    pause_event = threading.Event()
    tracker = _ConcurrencyTracker()
    tracker.release.set()
    delegate = _BlockingClient(tracker)
    client = LimitedLLMClient(delegate, budget, pause_event=pause_event)

    with ThreadPoolExecutor(max_workers=1) as executor:
        waiting = executor.submit(client.chat, [{"content": "old"}])
        _wait_until(lambda: budget.snapshot().waiting == 1)
        assert tracker.started == 0
        client.cancel()
        with pytest.raises(AiRequestCancelledError):
            waiting.result(timeout=1)

    assert delegate.cancel_count == 1
    pause_event.set()
    budget.notify_state_changed()
    assert client.chat([{"content": "new"}]) == "new"
    assert tracker.started == 1


def test_external_cancel_event_prevents_provider_start() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    tracker = _ConcurrencyTracker()
    client = LimitedLLMClient(_BlockingClient(tracker), AiRequestBudget(1), cancel_event=cancel_event)

    with pytest.raises(AiRequestCancelledError):
        client.chat([{"content": "late"}])
    assert tracker.started == 0


def test_prepared_chat_builds_messages_before_request_admission() -> None:
    budget = AiRequestBudget(1)
    occupied = budget.acquire()
    factory_called = threading.Event()

    class _Client:
        def chat(self, messages, max_tokens=0):
            return messages[0]["content"]

        def cancel(self) -> None:
            pass

    client = LimitedLLMClient(_Client(), budget)

    def prepare() -> list[dict]:
        factory_called.set()
        return [{"content": "prepared"}]

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.chat_prepared, prepare, 20)
        _wait_until(lambda: budget.snapshot().waiting == 1)
        assert factory_called.is_set()
        assert budget.snapshot().preparing == 0
        occupied.release()
        assert future.result(timeout=1) == "prepared"

    assert factory_called.is_set()
    assert budget.snapshot().in_flight == 0


def test_preparation_error_releases_exactly_one_lease() -> None:
    class _NeverCalledClient:
        def chat(self, _messages, max_tokens=0):
            raise AssertionError("provider must not be called")

        def cancel(self) -> None:
            pass

    budget = AiRequestBudget(1)
    client = LimitedLLMClient(_NeverCalledClient(), budget)

    def fail_preparation() -> list[dict]:
        raise ValueError("prompt preparation failed")

    with pytest.raises(ValueError, match="prompt preparation failed"):
        client.chat_prepared(fail_preparation)

    assert budget.snapshot().in_flight == 0
    with budget.acquire():
        assert budget.snapshot().in_flight == 1


def test_cancelled_prepared_chat_never_builds_messages() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    factory_called = False

    class _NeverCalledClient:
        def chat(self, _messages, max_tokens=0):
            raise AssertionError("provider must not be called")

        def cancel(self) -> None:
            pass

    def prepare() -> list[dict]:
        nonlocal factory_called
        factory_called = True
        return []

    client = LimitedLLMClient(
        _NeverCalledClient(),
        AiRequestBudget(1),
        cancel_event=cancel_event,
    )

    with pytest.raises(AiRequestCancelledError):
        client.chat_prepared(prepare)
    assert factory_called is False


def test_blocked_preparation_does_not_consume_request_slots() -> None:
    budget = AiRequestBudget(3, max_preparing=2)
    tracker = _ConcurrencyTracker()
    tracker.release.set()
    client = LimitedLLMClient(_BlockingClient(tracker), budget)
    barrier = threading.Barrier(3)
    release_preparation = threading.Event()

    def prepare() -> list[dict]:
        barrier.wait(timeout=2)
        assert release_preparation.wait(2)
        return [{"content": "prepared"}]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(client.chat_prepared, prepare) for _ in range(2)]
        try:
            barrier.wait(timeout=2)
            snapshot = budget.snapshot()
            assert snapshot.preparing == 2
            assert snapshot.in_flight == 0
            assert snapshot.peak_in_flight == 0
            assert tracker.started == 0
            assert client.chat([{"content": "ready"}]) == "ready"
            assert tracker.started == 1
        finally:
            release_preparation.set()
        assert [future.result(timeout=2) for future in futures] == ["prepared", "prepared"]
    assert budget.snapshot().preparing == 0


def test_preparation_pool_is_bounded_and_released_before_waiting_for_request() -> None:
    budget = AiRequestBudget(1)
    occupied = budget.acquire()
    tracker = _ConcurrencyTracker()
    tracker.release.set()
    client = LimitedLLMClient(_BlockingClient(tracker), budget)
    preparing = threading.Event()
    release_preparation = threading.Event()

    def prepare() -> list[dict]:
        preparing.set()
        assert release_preparation.wait(2)
        return [{"content": "prepared"}]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.chat_prepared, prepare)
        try:
            assert preparing.wait(1)
            second = executor.submit(client.chat_prepared, lambda: [{"content": "second"}])
            _wait_until(lambda: budget.snapshot().waiting_preparation == 1)
            assert budget.snapshot().preparing == 1
            release_preparation.set()
            _wait_until(lambda: budget.snapshot().waiting == 2)
            assert budget.snapshot().preparing == 0
            assert budget.snapshot().waiting_preparation == 0
            assert tracker.started == 0
        finally:
            release_preparation.set()
            occupied.release()
        assert first.result(timeout=2) == "prepared"
        assert second.result(timeout=2) == "second"
    assert budget.snapshot().peak_preparing == 1
    assert budget.snapshot().peak_in_flight == 1


@pytest.mark.parametrize("streaming", [False, True])
def test_two_phase_request_refreshes_state_only_after_admission(streaming: bool) -> None:
    budget = AiRequestBudget(1)
    occupied = budget.acquire()
    tracker = _ConcurrencyTracker()
    tracker.release.set()
    client = LimitedLLMClient(_BlockingClient(tracker), budget)
    term = {"value": "old"}
    finalized = threading.Event()
    chunks: list[str] = []

    def finalize(prefix: str) -> list[dict]:
        assert budget.snapshot().in_flight == 1
        assert budget.snapshot().preparing == 0
        finalized.set()
        return [{"content": prefix + term["value"]}]

    request = PreparedLlmRequest(prepare=lambda: "term=", finalize=finalize)
    with ThreadPoolExecutor(max_workers=1) as executor:
        if streaming:
            future = executor.submit(client.chat_stream_prepared, request, 20, chunks.append)
        else:
            future = executor.submit(client.chat_prepared, request, 20)
        try:
            _wait_until(lambda: budget.snapshot().waiting == 1)
            assert not finalized.is_set()
            term["value"] = "new"
        finally:
            occupied.release()
        assert future.result(timeout=2) == "term=new"
    assert chunks == (["term=new"] if streaming else [])


def test_finalize_error_releases_request_lease() -> None:
    budget = AiRequestBudget(1)
    tracker = _ConcurrencyTracker()
    client = LimitedLLMClient(_BlockingClient(tracker), budget)

    def fail(_prepared) -> list[dict]:
        raise RuntimeError("finalize failed")

    with pytest.raises(RuntimeError, match="finalize failed"):
        client.chat_prepared(PreparedLlmRequest(prepare=lambda: None, finalize=fail))
    assert tracker.started == 0
    assert budget.snapshot().in_flight == 0
    assert budget.snapshot().preparing == 0


def test_cancel_while_preparing_prevents_finalize_and_provider_call() -> None:
    budget = AiRequestBudget(1)
    tracker = _ConcurrencyTracker()
    client = LimitedLLMClient(_BlockingClient(tracker), budget)
    preparing = threading.Event()
    release_preparation = threading.Event()

    def prepare():
        preparing.set()
        assert release_preparation.wait(2)

    def never_finalize(_prepared):
        raise AssertionError("cancelled request must not finalize")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.chat_prepared, PreparedLlmRequest(prepare, never_finalize))
        try:
            assert preparing.wait(1)
            client.cancel()
        finally:
            release_preparation.set()
        with pytest.raises(AiRequestCancelledError):
            future.result(timeout=2)
    assert budget.snapshot().in_flight == 0
    assert budget.snapshot().preparing == 0
    assert tracker.started == 0


def test_pause_before_preparation_blocks_factory_and_can_be_cancelled() -> None:
    budget = AiRequestBudget(1)
    pause = threading.Event()
    tracker = _ConcurrencyTracker()
    client = LimitedLLMClient(_BlockingClient(tracker), budget, pause_event=pause)
    called = threading.Event()

    def prepare():
        called.set()
        return []

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.chat_prepared, prepare)
        _wait_until(lambda: budget.snapshot().waiting_preparation == 1)
        assert budget.snapshot().preparing == 0
        assert budget.snapshot().in_flight == 0
        assert not called.is_set()
        client.cancel()
        with pytest.raises(AiRequestCancelledError):
            future.result(timeout=1)
    assert budget.snapshot().waiting_preparation == 0
    assert tracker.started == 0


def test_client_lane_keys_share_slots_fairly() -> None:
    budget = AiRequestBudget(1)
    occupied = budget.acquire()
    tracker = _ConcurrencyTracker()
    tracker.release.set()
    order = []

    class _OrderedClient(_BlockingClient):
        def chat(self, messages, max_tokens=0):
            result = super().chat(messages, max_tokens)
            order.append(result)
            return result

    translation = LimitedLLMClient(_OrderedClient(tracker), budget, lane_key=("A", "translate"))
    proofread = LimitedLLMClient(_OrderedClient(tracker), budget, lane_key=("B", "proofread"))
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = []
        try:
            for index, (client, content) in enumerate(
                [(translation, "A1"), (translation, "A2"), (translation, "A3"), (proofread, "P1")], start=1
            ):
                futures.append(executor.submit(client.chat, [{"content": content}]))
                _wait_until(lambda: budget.snapshot().waiting == index)
        finally:
            occupied.release()
        for future in futures:
            future.result(timeout=2)
    assert order == ["A1", "P1", "A2", "A3"]
