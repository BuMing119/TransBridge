# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Transient extraction execution ports; never persisted as terminology facts."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .conflict_judgment import ConflictJudgePort
    from .extraction import CancellationPort, LlmEvidenceInput, LlmTermProposal, TerminologyExtractionService
    from .incremental import CacheEntryPort
    from .workloads import TerminologyProgress


@dataclass(frozen=True, slots=True)
class ExtractionBatchResult:
    inputs: tuple[LlmEvidenceInput, ...]
    proposals: tuple[LlmTermProposal, ...] = ()
    error: str | None = None


class ExtractionBatchRunner(Protocol):
    def run(
        self, inputs: tuple[LlmEvidenceInput, ...], cancellation: CancellationPort | None
    ) -> Iterator[ExtractionBatchResult]: ...


class ExtractionResponseCachePort(Protocol):
    def get(self, kind: str, key: str) -> CacheEntryPort | None: ...

    def put(self, kind: str, key: str, payload: bytes) -> CacheEntryPort: ...

    def discard(self, kind: str, key: str) -> None: ...


class PreparedExtractionPort(Protocol):
    @property
    def display_label(self) -> str: ...

    def conflict_judge(self, *, progress: Callable[[TerminologyProgress], None]) -> ConflictJudgePort: ...

    def open(
        self,
        *,
        cancellation: CancellationPort | None,
        progress: Callable[[TerminologyProgress], None],
        cache: ExtractionResponseCachePort,
    ) -> AbstractContextManager[TerminologyExtractionService]: ...
