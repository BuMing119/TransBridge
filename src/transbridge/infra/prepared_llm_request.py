"""Two-phase request preparation shared by LLM decorators and callers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreparedLlmRequest[T]:
    """Prepare costly immutable data, then finalize against current state.

    ``prepare`` runs before request admission, within a separate bounded pool.
    ``finalize`` runs after admission and must be lightweight: merge current
    confirmed terms, for example, without embedding work or nested LLM calls.

    Callable compatibility lets clients without two-phase support execute the
    same request synchronously. Decorators should preserve the two phases when
    forwarding to a client that supports ``chat_prepared``.
    """

    prepare: Callable[[], T]
    finalize: Callable[[T], list[dict]]

    def __call__(self) -> list[dict]:
        return self.finalize(self.prepare())


__all__ = ["PreparedLlmRequest"]
