"""Explicit migration gate for the legacy GUI Project/Variant facade."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from transbridge.application.contracts import DomainError, ErrorCategory, RequestContext

from .models import ActiveProject, TransitionTarget

BaselineProvider = Callable[[TransitionTarget, RequestContext], Any]
CandidateBuilder = Callable[[TransitionTarget, Any, RequestContext], ActiveProject]


class LegacyProjectLifecycleAdapter:
    """Adapter seam retained until S05 injects authoritative source baselines.

    It deliberately fails closed without a baseline provider.  This prevents
    the old ``VariantStore(list[TranslationEntry])`` overlay from being
    presented as replace materialization.
    """

    def __init__(
        self,
        candidate_builder: CandidateBuilder,
        *,
        baseline_provider: BaselineProvider | None = None,
    ) -> None:
        self._candidate_builder = candidate_builder
        self._baseline_provider = baseline_provider

    @property
    def authoritative(self) -> bool:
        return self._baseline_provider is not None

    def prepare_candidate(self, target: TransitionTarget, context: RequestContext) -> ActiveProject:
        if self._baseline_provider is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "LEGACY_SOURCE_BASELINE_REQUIRED",
                "The legacy GUI lifecycle requires an authoritative source baseline before switching.",
            )
        baseline = self._baseline_provider(target, context)
        if baseline is None:
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "LEGACY_SOURCE_BASELINE_UNAVAILABLE",
                "The authoritative source baseline is unavailable.",
            )
        return self._candidate_builder(target, baseline, context)


__all__ = ["BaselineProvider", "CandidateBuilder", "LegacyProjectLifecycleAdapter"]
