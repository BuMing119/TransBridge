from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from transbridge.infra.llm_reasoning import (
    ReasoningCapability,
    ReasoningCapabilityKey,
    ReasoningCapabilityManager,
    ReasoningCapabilityStatus,
    ReasoningCapabilityStore,
    ReasoningIntent,
    ReasoningScopedLLMClient,
)


def _key() -> ReasoningCapabilityKey:
    config = SimpleNamespace(
        provider="openai_compatible",
        base_url="HTTPS://Example.Test/v1/?token=must-not-persist",
        model="reasoner",
        api_key="super-secret",
    )
    return ReasoningCapabilityKey.from_config(config)


def test_capability_key_normalizes_endpoint_and_store_omits_credentials(tmp_path: Path) -> None:
    key = _key()
    path = tmp_path / "capabilities.json"
    store = ReasoningCapabilityStore(path)

    store.put(key, ReasoningCapability.supported("reasoning_effort"))

    raw = path.read_text(encoding="utf-8")
    assert "super-secret" not in raw
    assert "must-not-persist" not in raw
    assert key.base_url == "https://example.test/v1"
    assert store.get(key) == ReasoningCapabilityStore(path).get(key)


def test_store_never_persists_endpoint_userinfo_or_path_secrets(tmp_path: Path) -> None:
    key = ReasoningCapabilityKey(
        protocol="openai_compatible",
        base_url="https://user:password@example.test/v1/private-token",
        model="reasoner",
    )
    path = tmp_path / "capabilities.json"

    ReasoningCapabilityStore(path).put(key, ReasoningCapability.supported("reasoning_effort"))

    raw = path.read_text(encoding="utf-8")
    assert "user" not in raw
    assert "password" not in raw
    assert "private-token" not in raw
    assert "example.test" not in raw


def test_corrupt_and_future_cache_documents_are_safe_misses(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    path.write_text("not json", encoding="utf-8")
    assert ReasoningCapabilityStore(path).get(_key()) is None

    path.write_text(json.dumps({"schema_version": 999, "entries": {}}), encoding="utf-8")
    assert ReasoningCapabilityStore(path).get(_key()) is None


def test_expired_indeterminate_record_is_removed(tmp_path: Path) -> None:
    key = _key()
    path = tmp_path / "capabilities.json"
    store = ReasoningCapabilityStore(path)
    expired = ReasoningCapability(
        ReasoningCapabilityStatus.INDETERMINATE,
        detected_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )
    store.put(key, expired)

    assert store.get(key) is None
    assert key.digest not in json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_manager_runs_one_probe_for_concurrent_resolvers(tmp_path: Path) -> None:
    manager = ReasoningCapabilityManager(ReasoningCapabilityStore(tmp_path / "capabilities.json"))
    calls = 0
    call_lock = threading.Lock()

    def detect() -> ReasoningCapability:
        nonlocal calls
        with call_lock:
            calls += 1
        time.sleep(0.05)
        return ReasoningCapability.supported("reasoning_effort")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: manager.resolve(_key(), detect), range(8)))

    assert calls == 1
    assert {result.direct_mechanism for result in results} == {"reasoning_effort"}


def test_manager_does_not_block_business_call_until_slow_probe_finishes(tmp_path: Path) -> None:
    manager = ReasoningCapabilityManager(ReasoningCapabilityStore(tmp_path / "capabilities.json"))
    release = threading.Event()

    def detect() -> ReasoningCapability:
        release.wait(1)
        return ReasoningCapability.supported("reasoning_effort")

    started = time.monotonic()
    result = manager.resolve(_key(), detect, wait_seconds=0.01)
    elapsed = time.monotonic() - started
    release.set()
    completed = manager.resolve(_key(), detect, wait_seconds=1)

    assert result.status is ReasoningCapabilityStatus.INDETERMINATE
    assert completed.status is ReasoningCapabilityStatus.SUPPORTED
    assert elapsed < 0.2


class _ControlledDelegate:
    def __init__(self, *, reject_control: bool = False) -> None:
        self.calls: list[tuple[str, object]] = []
        self.reject_control = reject_control

    def chat(self, _messages, _max_tokens=0) -> str:
        self.calls.append(("inherit", None))
        return "inherited"

    def chat_with_reasoning(self, _messages, _max_tokens, patch) -> str:
        self.calls.append(("controlled", patch))
        if self.reject_control:
            raise _ReasoningRejected()
        return "controlled"

    def chat_stream(self, _messages, _max_tokens, callback) -> str:
        self.calls.append(("inherit_stream", None))
        callback("inherited")
        return "inherited"

    def chat_stream_with_reasoning(self, _messages, _max_tokens, callback, patch) -> str:
        self.calls.append(("controlled_stream", patch))
        if self.reject_control:
            raise _ReasoningRejected()
        callback("controlled")
        return "controlled"

    def build_reasoning_patch(self, capability, intent):
        return (capability.direct_mechanism, intent.value)

    @staticmethod
    def is_reasoning_control_rejection(exc, _capability) -> bool:
        return isinstance(exc, _ReasoningRejected)


class _ReasoningRejected(Exception):
    pass


def test_scoped_client_uses_cached_control_without_probe(tmp_path: Path) -> None:
    store = ReasoningCapabilityStore(tmp_path / "capabilities.json")
    store.put(_key(), ReasoningCapability.supported("reasoning_effort"))
    delegate = _ControlledDelegate()
    client = ReasoningScopedLLMClient(
        delegate,
        _key(),
        ReasoningIntent.PREFER_DIRECT,
        manager=ReasoningCapabilityManager(store),
    )

    assert client.chat([], 10) == "controlled"
    assert delegate.calls == [("controlled", ("reasoning_effort", "prefer_direct"))]


def test_scoped_client_invalidates_rejected_control_and_retries_inherit(tmp_path: Path) -> None:
    path = tmp_path / "capabilities.json"
    store = ReasoningCapabilityStore(path)
    store.put(_key(), ReasoningCapability.supported("reasoning_effort"))
    delegate = _ControlledDelegate(reject_control=True)
    client = ReasoningScopedLLMClient(
        delegate,
        _key(),
        ReasoningIntent.PREFER_DIRECT,
        manager=ReasoningCapabilityManager(store),
    )

    assert client.chat([], 10) == "inherited"
    assert [name for name, _patch in delegate.calls] == ["controlled", "inherit"]
    assert store.get(_key()).status is ReasoningCapabilityStatus.INDETERMINATE


def test_scoped_stream_forwards_callback_and_retries_rejected_control(tmp_path: Path) -> None:
    store = ReasoningCapabilityStore(tmp_path / "capabilities.json")
    store.put(_key(), ReasoningCapability.supported("reasoning_effort"))
    delegate = _ControlledDelegate(reject_control=True)
    client = ReasoningScopedLLMClient(
        delegate,
        _key(),
        ReasoningIntent.PREFER_LOW,
        manager=ReasoningCapabilityManager(store),
    )
    chunks: list[str] = []

    assert client.chat_stream([], 10, chunks.append) == "inherited"
    assert chunks == ["inherited"]
    assert [name for name, _patch in delegate.calls] == ["controlled_stream", "inherit_stream"]
