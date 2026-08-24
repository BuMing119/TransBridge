"""Lifecycle-safe controller for guidance projections and user intents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from transbridge.config.ui_preferences import (
    GuidanceMode,
    UiPreferenceSaveResult,
    UiPreferenceSnapshot,
)

from .models import (
    GuidanceContextIdentity,
    GuidanceIntentId,
    GuidanceProjection,
    GuidanceState,
)
from .presentation import (
    GuidancePresentation,
    GuidanceVisibility,
    present_guidance,
)
from .state_machine import build_guidance_state


class GuidancePreferencePort(Protocol):
    def load(self) -> UiPreferenceSnapshot: ...

    def save_guidance_mode(self, mode: GuidanceMode) -> UiPreferenceSaveResult: ...


@dataclass(frozen=True, slots=True)
class GuidanceReduction:
    accepted: bool
    reason: str
    presentation: GuidancePresentation | None = None


@dataclass(frozen=True, slots=True)
class GuidanceSubmission:
    accepted: bool
    intent_id: GuidanceIntentId | None
    reason: str = ""
    result: object = None


class GuidanceController:
    """One context-generation owner; all methods are safe after ``close``."""

    def __init__(
        self,
        on_change: Callable[[GuidancePresentation], None],
        dispatch: Callable[[GuidanceIntentId], object],
        *,
        preferences: GuidancePreferencePort | None = None,
        initial_mode: GuidanceMode = GuidanceMode.AUTO,
    ) -> None:
        self._on_change = on_change
        self._dispatch = dispatch
        self._preferences = preferences
        self._mode = preferences.load().guidance_mode if preferences is not None else initial_mode
        self._visibility = GuidanceVisibility.EXPANDED
        self._context_identity: GuidanceContextIdentity | None = None
        self._generation = -1
        self._revision = -1
        self._state: GuidanceState | None = None
        self._submitted: set[tuple[int, int, GuidanceIntentId]] = set()
        self._closed = False
        self._lock = RLock()

    @property
    def mode(self) -> GuidanceMode:
        with self._lock:
            return self._mode

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def project(self, projection: GuidanceProjection) -> GuidanceReduction:
        with self._lock:
            rejected = self._projection_rejection(projection)
            if rejected:
                return GuidanceReduction(False, rejected)
            if projection.generation > self._generation:
                self._context_identity = projection.context_identity
                self._generation = projection.generation
                self._revision = -1
                self._state = None
                self._visibility = GuidanceVisibility.EXPANDED
                self._submitted.clear()
            state = build_guidance_state(projection)
            self._revision = projection.revision
            self._state = state
            presentation = present_guidance(state, self._mode, self._visibility)
        self._on_change(presentation)
        return GuidanceReduction(True, "", presentation)

    def collapse(self) -> bool:
        return self._set_visibility(GuidanceVisibility.COLLAPSED)

    def hide(self) -> bool:
        return self._set_visibility(GuidanceVisibility.HIDDEN)

    def restore(self) -> bool:
        return self._set_visibility(GuidanceVisibility.EXPANDED)

    def set_mode(self, mode: GuidanceMode) -> UiPreferenceSaveResult:
        if not isinstance(mode, GuidanceMode):
            raise TypeError("mode must be a GuidanceMode")
        with self._lock:
            if self._closed:
                return UiPreferenceSaveResult(
                    requested_mode=mode,
                    saved=False,
                    diagnostic_code="guidance_closed",
                    message="guidance controller is closed",
                )
            preferences = self._preferences
        if preferences is None:
            return UiPreferenceSaveResult(
                requested_mode=mode,
                saved=False,
                diagnostic_code="ui_preference_repository_missing",
                message="UI preference repository is not configured",
            )
        result = preferences.save_guidance_mode(mode)
        if not result.saved:
            return result
        with self._lock:
            if self._closed:
                return result
            self._mode = mode
            presentation = self._current_presentation_locked()
        if presentation is not None:
            self._on_change(presentation)
        return result

    def submit_primary(self, *, expected_revision: int | None = None) -> GuidanceSubmission:
        with self._lock:
            state = self._state
            if self._closed or state is None:
                return GuidanceSubmission(False, None, "guidance_unavailable")
            intent = state.primary_intent
            if expected_revision is not None and expected_revision != state.revision:
                return GuidanceSubmission(False, intent.intent_id, "stale_revision")
            if not intent.enabled:
                return GuidanceSubmission(False, intent.intent_id, intent.enabled_reason or "intent_disabled")
            token = (state.generation, state.revision, intent.intent_id)
            if token in self._submitted:
                return GuidanceSubmission(False, intent.intent_id, "duplicate")
            self._submitted.add(token)
        result = self._dispatch(intent.intent_id)
        return GuidanceSubmission(True, intent.intent_id, result=result)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._state = None
            self._submitted.clear()

    def _projection_rejection(self, projection: GuidanceProjection) -> str:
        if self._closed:
            return "closed"
        if self._generation < 0:
            return ""
        if projection.generation < self._generation:
            return "stale_generation"
        if projection.generation == self._generation:
            if projection.context_identity != self._context_identity:
                return "context_identity_mismatch"
            if projection.revision == self._revision:
                return "duplicate_revision"
            if projection.revision < self._revision:
                return "out_of_order_revision"
        return ""

    def _set_visibility(self, visibility: GuidanceVisibility) -> bool:
        with self._lock:
            if self._closed or self._state is None or visibility is self._visibility:
                return False
            self._visibility = visibility
            presentation = self._current_presentation_locked()
        if presentation is not None:
            self._on_change(presentation)
        return True

    def _current_presentation_locked(self) -> GuidancePresentation | None:
        if self._state is None:
            return None
        return present_guidance(self._state, self._mode, self._visibility)


__all__ = [
    "GuidanceController",
    "GuidancePreferencePort",
    "GuidanceReduction",
    "GuidanceSubmission",
]
