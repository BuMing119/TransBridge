"""Offline-safe token counting for AI workflow business content."""

from __future__ import annotations

from functools import lru_cache
import math
from typing import Any

from transbridge.application.translation.token_batching import ContentTokenCount

try:
    import tiktoken
except ImportError:  # pragma: no cover - required in production, retained for degraded tooling
    tiktoken = None


class TiktokenContentTokenCounter:
    """Use a model tokenizer when known and a deterministic conservative estimate otherwise."""

    def __init__(self, model: str, *, estimate_safety_factor: float = 1.25) -> None:
        if estimate_safety_factor < 1:
            raise ValueError("estimate_safety_factor must be at least 1")
        self._model = (model or "").strip()
        self._safety_factor = float(estimate_safety_factor)
        self._encoding = _known_model_encoding(self._model)

    def count(self, text: str) -> ContentTokenCount:
        value = text or ""
        if self._encoding is not None:
            try:
                return ContentTokenCount(
                    len(self._encoding.encode(value)),
                    False,
                    str(getattr(self._encoding, "name", self._model or "tiktoken")),
                )
            except Exception:
                pass
        # One token per UTF-8 byte is a conservative byte-level-BPE upper bound;
        # the additional factor protects compatible providers with unknown tokenizers.
        estimated = math.ceil(len(value.encode("utf-8")) * self._safety_factor)
        return ContentTokenCount(estimated, True, "utf8-bytes-v1")


@lru_cache(maxsize=32)
def _known_model_encoding(model: str) -> Any | None:
    if not model or tiktoken is None:
        return None
    try:
        # Unknown/compatible provider model names stop at KeyError. We do not
        # call get_encoding() as a fallback, which could fetch encoding assets.
        return tiktoken.encoding_for_model(model)
    except Exception:
        return None
