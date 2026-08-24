"""Presentation-density projection that preserves business capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from transbridge.config.ui_preferences import GuidanceMode

from .models import GuidanceIntent, GuidanceIntentId, GuidanceState


class GuidanceVisibility(StrEnum):
    EXPANDED = "expanded"
    COLLAPSED = "collapsed"
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class GuidancePresentation:
    state: GuidanceState
    configured_mode: GuidanceMode
    effective_mode: GuidanceMode
    visibility: GuidanceVisibility
    explanation_lines: tuple[str, ...]

    @property
    def primary_intent(self) -> GuidanceIntent:
        return self.state.primary_intent

    @property
    def recovery_intents(self) -> tuple[GuidanceIntent, ...]:
        return self.state.recovery_intents

    @property
    def command_signature(self) -> tuple[tuple[GuidanceIntentId, bool, str | None], ...]:
        return self.state.command_signature


def present_guidance(
    state: GuidanceState,
    mode: GuidanceMode,
    visibility: GuidanceVisibility = GuidanceVisibility.EXPANDED,
) -> GuidancePresentation:
    """Reduce explanatory density only; never alter actions or state identity."""

    effective = GuidanceMode.GUIDED if mode is GuidanceMode.AUTO else mode
    if visibility is not GuidanceVisibility.EXPANDED:
        explanations: tuple[str, ...] = ()
    elif effective is GuidanceMode.COMPACT:
        explanations = (state.reason,)
    else:
        explanations = (state.reason, *state.detail_lines)
    return GuidancePresentation(state, mode, effective, visibility, explanations)


__all__ = ["GuidancePresentation", "GuidanceVisibility", "present_guidance"]
