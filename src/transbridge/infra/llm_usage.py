"""Provider usage snapshots, independent of model output and business admission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import inspect
import logging
import time
from typing import Any, Literal
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmUsage:
    """Final accounting for one wire attempt; None is never a reported zero."""

    attempt_id: str = field(default_factory=lambda: uuid4().hex)
    provider: str = "unknown"
    model: str = "unknown"
    purpose: str = "execution"
    retry_of: str | None = None
    source: Literal["reported", "estimated", "unknown"] = "unknown"
    completeness: Literal["complete", "partial", "unknown"] = "unknown"
    input_tokens: int | None = None
    output_tokens: int | None = None
    uncached_input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    raw_usage: tuple[tuple[str, int], ...] = ()
    duration_ms: int = 0
    outcome: str = "completed"
    degradation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["raw_usage"] = dict(self.raw_usage)
        return data


UsageCallback = Callable[[LlmUsage], None]


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def supports_usage_callback(method: Callable) -> bool:
    """Inspect before dispatch: TypeError from an actual call must never cause replay."""
    try:
        params = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    return "usage_callback" in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def single_attempt_client(client: Any) -> Any:
    """Prevent SDK-internal retries from escaping attempt accounting.

    SDK copies share the original HTTP client, retaining owner cancellation.
    Legacy client-shaped implementations without options remain supported.
    """
    with_options = getattr(client, "with_options", None)
    return with_options(max_retries=0) if callable(with_options) else client


class UsageAttempt:
    """Accumulate only provider counters (snapshots), publishing exactly once."""

    def __init__(self, provider: str, model: str, purpose: str, callback=None, *, retry_of=None):
        self.attempt_id = uuid4().hex
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self.retry_of = retry_of
        self.callback = callback
        self.started = time.monotonic()
        self.raw: dict[str, int] = {}
        self.final_received = False
        self.result: LlmUsage | None = None

    def observe(self, usage: Any, *, final: bool = False) -> None:
        if usage is None:
            return
        names = (
            ("prompt_tokens", "completion_tokens", "total_tokens")
            if self.provider == "openai"
            else ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        values = {name: _field(usage, name) for name in names}
        if self.provider == "openai":
            values["cached_tokens"] = _field(_field(usage, "prompt_tokens_details"), "cached_tokens")
        for key, value in values.items():
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.raw[key] = value
        self.final_received |= final and bool(self.raw)

    def finish(self, *, outcome="completed", degradation=None) -> LlmUsage:
        if self.result is not None:
            return self.result
        raw = self.raw
        if self.provider == "openai":
            total = raw.get("prompt_tokens")
            output = raw.get("completion_tokens")
            read = raw.get("cached_tokens")
            write = None  # Chat Completions does not report a cache-write counter.
            uncached = total - read if total is not None and read is not None and read <= total else None
        else:
            uncached = raw.get("input_tokens")
            output = raw.get("output_tokens")
            read = raw.get("cache_read_input_tokens")
            write = raw.get("cache_creation_input_tokens")
            parts = (uncached, read, write)
            total = sum(parts) if all(part is not None for part in parts) else None
        complete = self.final_received and total is not None and output is not None
        self.result = LlmUsage(
            attempt_id=self.attempt_id,
            provider=self.provider,
            model=self.model,
            purpose=self.purpose,
            retry_of=self.retry_of,
            source="reported" if raw else "unknown",
            completeness="complete" if complete else "partial" if raw else "unknown",
            input_tokens=total,
            output_tokens=output,
            uncached_input_tokens=uncached,
            cache_read_tokens=read,
            cache_write_tokens=write,
            raw_usage=tuple(sorted(raw.items())),
            duration_ms=max(0, int((time.monotonic() - self.started) * 1000)),
            outcome=outcome,
            degradation=degradation,
        )
        if self.callback:
            try:
                self.callback(self.result)
            except Exception:
                logger.warning("Usage callback failed for attempt %s", self.attempt_id, exc_info=True)
        return self.result
