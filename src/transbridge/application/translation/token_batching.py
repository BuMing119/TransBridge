"""Provider-independent business-content token batching contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from transbridge.application.io.identity import EntryKey


@dataclass(frozen=True, slots=True)
class ContentTokenCount:
    """Token count for business content, excluding prompt/request scaffolding."""

    tokens: int
    is_estimate: bool
    encoding: str

    def __post_init__(self) -> None:
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int) or self.tokens < 0:
            raise ValueError("tokens must be a non-negative integer")
        if not self.encoding.strip():
            raise ValueError("encoding must not be empty")


class ContentTokenCounter(Protocol):
    """Count one business-content text without adding prompt overhead."""

    def count(self, text: str) -> ContentTokenCount: ...


@dataclass(frozen=True, slots=True)
class ContentBatch[T]:
    index: int
    items: tuple[T, ...]
    content_tokens: int
    is_estimate: bool
    fingerprint: str


@dataclass(frozen=True, slots=True)
class OversizedContentItem:
    entry_key: EntryKey
    content_tokens: int
    max_tokens: int
    is_estimate: bool

    @property
    def message(self) -> str:
        qualifier = "估算" if self.is_estimate else "实际"
        return (
            f"条目 {self.entry_key.serialize()} 的业务内容 Token（{qualifier} {self.content_tokens}）"
            f"超过单请求上限 {self.max_tokens}"
        )


@dataclass(frozen=True, slots=True)
class ContentBatchPlan[T]:
    batches: tuple[ContentBatch[T], ...]
    oversized: tuple[OversizedContentItem, ...] = ()

    @property
    def items(self) -> tuple[T, ...]:
        return tuple(item for batch in self.batches for item in batch.items)


class StableContentBatcher[T]:
    """Greedily batch an ordered stream using only its business-content budget."""

    VERSION = 1

    def __init__(self, counter: ContentTokenCounter, max_tokens: int, *, max_items: int | None = None) -> None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        if max_items is not None and (isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0):
            raise ValueError("max_items must be a positive integer when provided")
        self._counter = counter
        self._max_tokens = max_tokens
        self._max_items = max_items

    def plan(
        self,
        items: Iterable[T],
        *,
        key: Callable[[T], EntryKey],
        content: Callable[[T], str | Sequence[str]],
    ) -> ContentBatchPlan[T]:
        batches: list[ContentBatch[T]] = []
        oversized: list[OversizedContentItem] = []
        current: list[T] = []
        current_keys: list[EntryKey] = []
        current_content: list[tuple[str, ...]] = []
        current_tokens = 0
        current_estimate = False

        def flush() -> None:
            nonlocal current, current_keys, current_content, current_tokens, current_estimate
            if not current:
                return
            batches.append(
                ContentBatch(
                    index=len(batches),
                    items=tuple(current),
                    content_tokens=current_tokens,
                    is_estimate=current_estimate,
                    fingerprint=_batch_fingerprint(
                        current_keys,
                        current_content,
                        current_tokens,
                        current_estimate,
                    ),
                )
            )
            current = []
            current_keys = []
            current_content = []
            current_tokens = 0
            current_estimate = False

        for item in items:
            entry_key = key(item)
            projected = content(item)
            fields = (projected,) if isinstance(projected, str) else tuple(projected)
            measurement = self._count_fields(fields)
            if measurement.tokens > self._max_tokens:
                flush()
                oversized.append(
                    OversizedContentItem(
                        entry_key=entry_key,
                        content_tokens=measurement.tokens,
                        max_tokens=self._max_tokens,
                        is_estimate=measurement.is_estimate,
                    )
                )
                continue
            token_full = bool(current) and current_tokens + measurement.tokens > self._max_tokens
            item_full = self._max_items is not None and len(current) >= self._max_items
            if token_full or item_full:
                flush()
            current.append(item)
            current_keys.append(entry_key)
            current_content.append(fields)
            current_tokens += measurement.tokens
            current_estimate = current_estimate or measurement.is_estimate
        flush()
        return ContentBatchPlan(tuple(batches), tuple(oversized))

    def _count_fields(self, value: str | Sequence[str]) -> ContentTokenCount:
        fields = (value,) if isinstance(value, str) else value
        tokens = 0
        is_estimate = False
        encodings: list[str] = []
        for field in fields:
            measurement = self._counter.count(field or "")
            tokens += measurement.tokens
            is_estimate = is_estimate or measurement.is_estimate
            if measurement.encoding not in encodings:
                encodings.append(measurement.encoding)
        return ContentTokenCount(tokens, is_estimate, "+".join(encodings) or "empty")


def _batch_fingerprint(
    keys: Sequence[EntryKey],
    content: Sequence[tuple[str, ...]],
    content_tokens: int,
    is_estimate: bool,
) -> str:
    payload = {
        "version": StableContentBatcher.VERSION,
        "keys": [key.serialize() for key in keys],
        "content": content,
        "content_tokens": content_tokens,
        "is_estimate": is_estimate,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
