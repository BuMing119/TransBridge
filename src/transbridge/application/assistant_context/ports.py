"""Small model and storage boundaries, independent of Qt and provider SDKs."""

from typing import Protocol

from .models import CompactionSummary, FrozenContextItem


class SummaryGenerator(Protocol):
    def __call__(
        self, items: tuple[FrozenContextItem, ...], *, max_tokens: int, background: tuple[CompactionSummary, ...] = ()
    ) -> str:
        """Return semantic text for exactly these new sources, without executing tools."""
        ...
