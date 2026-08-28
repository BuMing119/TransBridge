"""Request-scoped LLM reasoning intent, capability cache, and lazy probing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import logging
from pathlib import Path
import threading
from urllib.parse import urlsplit, urlunsplit

from transbridge.config.paths import get_data_dir
from transbridge.persistence._utils import atomic_write_json

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_PROBE_VERSION = 1
_STABLE_TTL_SECONDS = 30 * 24 * 60 * 60
_INDETERMINATE_TTL_SECONDS = 24 * 60 * 60
_FOREGROUND_PROBE_WAIT_SECONDS = 2.0


class ReasoningIntent(StrEnum):
    """Workflow intent; providers decide how (or whether) it can be expressed."""

    INHERIT = "inherit"
    PREFER_DIRECT = "prefer_direct"
    PREFER_LOW = "prefer_low"


class ReasoningCapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class ReasoningCapabilityKey:
    protocol: str
    base_url: str
    model: str
    probe_version: int = _PROBE_VERSION

    @classmethod
    def from_config(cls, config: object) -> ReasoningCapabilityKey:
        protocol = str(getattr(config, "provider", "openai_compatible") or "openai_compatible").strip().lower()
        base_url = _normalize_base_url(str(getattr(config, "base_url", "") or ""))
        if protocol == "anthropic" and not base_url:
            base_url = "https://api.anthropic.com"
        return cls(
            protocol=protocol,
            base_url=base_url,
            model=str(getattr(config, "model", "") or "").strip(),
        )

    @property
    def digest(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ReasoningCapability:
    status: ReasoningCapabilityStatus
    direct_mechanism: str = ""
    low_mechanism: str = ""
    detected_at: str = ""

    @classmethod
    def supported(cls, mechanism: str) -> ReasoningCapability:
        return cls(
            ReasoningCapabilityStatus.SUPPORTED,
            direct_mechanism=mechanism,
            low_mechanism=mechanism if mechanism == "reasoning_effort" else "",
            detected_at=_utc_now(),
        )

    @classmethod
    def supported_controls(cls, *, direct: str = "", low: str = "") -> ReasoningCapability:
        if not direct and not low:
            raise ValueError("at least one reasoning control mechanism is required")
        return cls(
            ReasoningCapabilityStatus.SUPPORTED,
            direct_mechanism=direct,
            low_mechanism=low,
            detected_at=_utc_now(),
        )

    @classmethod
    def unsupported(cls) -> ReasoningCapability:
        return cls(ReasoningCapabilityStatus.UNSUPPORTED, detected_at=_utc_now())

    @classmethod
    def indeterminate(cls) -> ReasoningCapability:
        return cls(ReasoningCapabilityStatus.INDETERMINATE, detected_at=_utc_now())


@dataclass(frozen=True, slots=True)
class ReasoningRequestPatch:
    """Provider-ready request fields selected from a proven capability."""

    mechanism: str
    standard: dict[str, object]
    extra_body: dict[str, object]


class ReasoningCapabilityStore:
    """Small versioned JSON cache containing no credentials or model content."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(get_data_dir()) / "llm_reasoning_capabilities.json"
        self._lock = threading.RLock()
        self._loaded = False
        self._entries: dict[str, dict] = {}

    def get(self, key: ReasoningCapabilityKey) -> ReasoningCapability | None:
        with self._lock:
            self._load()
            record = self._entries.get(key.digest)
            if not isinstance(record, dict) or not self._matches_key(record, key):
                return None
            try:
                capability = ReasoningCapability(
                    ReasoningCapabilityStatus(str(record["status"])),
                    str(record.get("direct_mechanism", "")),
                    str(record.get("low_mechanism", "")),
                    str(record["detected_at"]),
                )
            except (KeyError, TypeError, ValueError):
                return None
            if not _is_fresh(capability):
                self._entries.pop(key.digest, None)
                self._persist()
                return None
            return capability

    def put(self, key: ReasoningCapabilityKey, capability: ReasoningCapability) -> None:
        with self._lock:
            self._load()
            self._entries[key.digest] = {
                "protocol": key.protocol,
                "endpoint_digest": _endpoint_digest(key.base_url),
                "model": key.model,
                "probe_version": key.probe_version,
                "status": capability.status.value,
                "direct_mechanism": capability.direct_mechanism,
                "low_mechanism": capability.low_mechanism,
                "detected_at": capability.detected_at or _utc_now(),
            }
            self._persist()

    def delete(self, key: ReasoningCapabilityKey) -> None:
        with self._lock:
            self._load()
            if self._entries.pop(key.digest, None) is not None:
                self._persist()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("LLM 思考能力缓存不可读，按空缓存处理: %s", exc)
            return
        if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION:
            return
        entries = document.get("entries")
        if isinstance(entries, dict):
            self._entries = {str(key): value for key, value in entries.items() if isinstance(value, dict)}

    def _persist(self) -> None:
        try:
            atomic_write_json(
                self._path,
                {
                    "schema_version": _SCHEMA_VERSION,
                    "entries": self._entries,
                },
            )
        except OSError as exc:
            logger.warning("LLM 思考能力缓存写入失败，本次仅使用内存结果: %s", exc)

    @staticmethod
    def _matches_key(record: dict, key: ReasoningCapabilityKey) -> bool:
        return all(
            record.get(field) == value
            for field, value in (
                ("protocol", key.protocol),
                ("endpoint_digest", _endpoint_digest(key.base_url)),
                ("model", key.model),
                ("probe_version", key.probe_version),
            )
        )


@dataclass(slots=True)
class _ProbeFlight:
    event: threading.Event
    result: ReasoningCapability | None = None


class ReasoningCapabilityManager:
    """Coordinates persistent cache access and one in-flight probe per key."""

    def __init__(self, store: ReasoningCapabilityStore | None = None) -> None:
        self._store = store or ReasoningCapabilityStore()
        self._lock = threading.Lock()
        self._flights: dict[str, _ProbeFlight] = {}

    def schedule(self, key: ReasoningCapabilityKey, detector: Callable[[], ReasoningCapability] | None) -> None:
        if detector is None or not key.model or self._store.get(key) is not None:
            return
        flight, owner = self._flight(key)
        if not owner:
            return
        cached = self._store.get(key)
        if cached is not None:
            self._finish_flight(key, flight, cached)
            return
        threading.Thread(
            target=self._run_probe,
            args=(key, detector, flight),
            name="llm-reasoning-probe",
            daemon=True,
        ).start()

    def resolve(
        self,
        key: ReasoningCapabilityKey,
        detector: Callable[[], ReasoningCapability] | None,
        *,
        wait_seconds: float = _FOREGROUND_PROBE_WAIT_SECONDS,
    ) -> ReasoningCapability:
        cached = self._store.get(key)
        if cached is not None:
            return cached
        if detector is None or not key.model:
            return ReasoningCapability.indeterminate()
        flight, owner = self._flight(key)
        if owner:
            cached = self._store.get(key)
            if cached is not None:
                self._finish_flight(key, flight, cached)
            else:
                threading.Thread(
                    target=self._run_probe,
                    args=(key, detector, flight),
                    name="llm-reasoning-probe",
                    daemon=True,
                ).start()
        if not flight.event.wait(max(0.0, wait_seconds)):
            return ReasoningCapability.indeterminate()
        return flight.result or ReasoningCapability.indeterminate()

    def invalidate(self, key: ReasoningCapabilityKey) -> None:
        self._store.delete(key)

    def mark_indeterminate(self, key: ReasoningCapabilityKey) -> None:
        self._store.put(key, ReasoningCapability.indeterminate())

    def _flight(self, key: ReasoningCapabilityKey) -> tuple[_ProbeFlight, bool]:
        with self._lock:
            flight = self._flights.get(key.digest)
            if flight is not None:
                return flight, False
            flight = _ProbeFlight(threading.Event())
            self._flights[key.digest] = flight
            return flight, True

    def _run_probe(
        self,
        key: ReasoningCapabilityKey,
        detector: Callable[[], ReasoningCapability],
        flight: _ProbeFlight,
    ) -> None:
        try:
            try:
                result = detector()
            except Exception:
                logger.exception("LLM 思考能力后台探测失败: protocol=%s, model=%s", key.protocol, key.model)
                result = ReasoningCapability.indeterminate()
            flight.result = result
            self._store.put(key, result)
        finally:
            self._finish_flight(key, flight, flight.result or ReasoningCapability.indeterminate())

    def _finish_flight(
        self,
        key: ReasoningCapabilityKey,
        flight: _ProbeFlight,
        result: ReasoningCapability,
    ) -> None:
        flight.result = result
        flight.event.set()
        with self._lock:
            if self._flights.get(key.digest) is flight:
                self._flights.pop(key.digest, None)


class ReasoningScopedLLMClient:
    """Decorate one workflow's calls with a reasoning intent when proven safe."""

    def __init__(
        self,
        delegate: object,
        key: ReasoningCapabilityKey,
        intent: ReasoningIntent,
        *,
        manager: ReasoningCapabilityManager | None = None,
    ) -> None:
        self._delegate = delegate
        self._key = key
        self._intent = intent
        self._manager = manager or get_default_reasoning_manager()
        self._detector = getattr(delegate, "detect_reasoning_capability", None)
        self._probe_wait_lock = threading.Lock()
        self._probe_wait_consumed = False
        if intent is not ReasoningIntent.INHERIT:
            self._manager.schedule(key, self._detector if callable(self._detector) else None)

    @property
    def delegate(self) -> object:
        return self._delegate

    @property
    def reasoning_intent(self) -> ReasoningIntent:
        return self._intent

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        return self._call("chat", messages, max_tokens)

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        return self._call("chat_stream", messages, max_tokens, chunk_callback)

    def cancel(self) -> None:
        cancel = getattr(self._delegate, "cancel", None)
        if callable(cancel):
            cancel()

    def _call(self, method: str, *args) -> str:
        inherited = getattr(self._delegate, method)
        if self._intent is ReasoningIntent.INHERIT:
            return inherited(*args)
        detector = self._detector if callable(self._detector) else None
        with self._probe_wait_lock:
            wait_seconds = _FOREGROUND_PROBE_WAIT_SECONDS if not self._probe_wait_consumed else 0.0
            self._probe_wait_consumed = True
        capability = self._manager.resolve(self._key, detector, wait_seconds=wait_seconds)
        controlled = getattr(self._delegate, f"{method}_with_reasoning", None)
        patch_builder = getattr(self._delegate, "build_reasoning_patch", None)
        if capability.status is not ReasoningCapabilityStatus.SUPPORTED or not callable(controlled):
            return inherited(*args)
        patch = patch_builder(capability, self._intent) if callable(patch_builder) else None
        if patch is None:
            return inherited(*args)
        try:
            return controlled(*args, patch)
        except Exception as exc:
            rejection = getattr(self._delegate, "is_reasoning_control_rejection", None)
            if not callable(rejection) or not rejection(exc, patch):
                raise
            self._manager.mark_indeterminate(self._key)
            logger.warning(
                "已缓存的 LLM 思考控制被 Provider 拒绝，暂缓重探测并继承重试: protocol=%s, model=%s",
                self._key.protocol,
                self._key.model,
            )
            return inherited(*args)


_default_manager: ReasoningCapabilityManager | None = None
_default_manager_lock = threading.Lock()


def get_default_reasoning_manager() -> ReasoningCapabilityManager:
    global _default_manager
    with _default_manager_lock:
        if _default_manager is None:
            _default_manager = ReasoningCapabilityManager()
        return _default_manager


def with_reasoning_intent(
    delegate: object,
    config: object,
    intent: ReasoningIntent,
    *,
    manager: ReasoningCapabilityManager | None = None,
) -> object:
    if intent is ReasoningIntent.INHERIT:
        return delegate
    return ReasoningScopedLLMClient(
        delegate,
        ReasoningCapabilityKey.from_config(config),
        intent,
        manager=manager,
    )


def _normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value.lower()
    if not parsed.scheme or not parsed.netloc:
        return value.lower()
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def _endpoint_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_fresh(capability: ReasoningCapability) -> bool:
    try:
        detected_at = datetime.fromisoformat(capability.detected_at)
        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return False
    ttl = (
        _INDETERMINATE_TTL_SECONDS
        if capability.status is ReasoningCapabilityStatus.INDETERMINATE
        else _STABLE_TTL_SECONDS
    )
    return 0 <= (datetime.now(UTC) - detected_at).total_seconds() <= ttl


__all__ = [
    "ReasoningCapability",
    "ReasoningCapabilityKey",
    "ReasoningCapabilityManager",
    "ReasoningCapabilityStatus",
    "ReasoningCapabilityStore",
    "ReasoningIntent",
    "ReasoningRequestPatch",
    "ReasoningScopedLLMClient",
    "with_reasoning_intent",
]
