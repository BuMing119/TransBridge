"""Immutable, Qt-free contracts for state-driven guidance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from transbridge.ui.shell.action_catalog import IntentId as GuidanceIntentId


class GuidanceKind(StrEnum):
    NO_PROJECT = "no_project"
    EMPTY_PROJECT = "empty_project"
    UNTRANSLATED = "untranslated"
    REVIEW_PENDING = "review_pending"
    PUBLISH_PENDING = "publish_pending"
    MISSING_CONFIGURATION = "missing_configuration"
    FAILED = "failed"
    PARTIAL_FAILURE = "partial_failure"


@dataclass(frozen=True, slots=True)
class GuidanceContextIdentity:
    project_id: str | None = None
    version_id: str | None = None
    content_id: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        for value in (self.project_id, self.version_id, self.content_id, self.run_id):
            if value is not None and not value.strip():
                raise ValueError("context identity values must not be blank")


@dataclass(frozen=True, slots=True)
class GuidanceIntent:
    intent_id: GuidanceIntentId
    label: str
    enabled: bool = True
    enabled_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("intent label must not be blank")
        if self.enabled and self.enabled_reason is not None:
            raise ValueError("enabled intent cannot carry a disabled reason")
        if not self.enabled and not (self.enabled_reason and self.enabled_reason.strip()):
            raise ValueError("disabled intent requires a user-facing reason")


@dataclass(frozen=True, slots=True)
class GuidanceProjection:
    """One upstream-owned business-state projection.

    ``kind`` is explicit: this layer must not infer domain state from widget
    contents or private fields.  Revisions are local to one context generation.
    """

    context_identity: GuidanceContextIdentity
    generation: int
    revision: int
    kind: GuidanceKind
    reason: str = ""
    missing_configuration: tuple[str, ...] = ()
    retry_available: bool = False

    def __post_init__(self) -> None:
        if self.generation < 0 or self.revision < 0:
            raise ValueError("generation and revision must be non-negative")
        if any(not item.strip() for item in self.missing_configuration):
            raise ValueError("configuration names must not be blank")
        if self.kind is GuidanceKind.MISSING_CONFIGURATION and not self.missing_configuration:
            raise ValueError("missing-configuration projection requires at least one missing item")


@dataclass(frozen=True, slots=True)
class GuidanceState:
    context_identity: GuidanceContextIdentity
    generation: int
    revision: int
    kind: GuidanceKind
    headline: str
    reason: str
    primary_intent: GuidanceIntent
    recovery_intents: tuple[GuidanceIntent, ...]
    detail_lines: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.headline.strip() or not self.reason.strip():
            raise ValueError("guidance state requires a headline and reason")
        if not self.recovery_intents:
            raise ValueError("guidance state requires at least one recovery entry")
        all_ids = (self.primary_intent.intent_id, *(item.intent_id for item in self.recovery_intents))
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("primary and recovery intents must be unique")

    @property
    def command_signature(self) -> tuple[tuple[GuidanceIntentId, bool, str | None], ...]:
        intents = (self.primary_intent, *self.recovery_intents)
        return tuple((item.intent_id, item.enabled, item.enabled_reason) for item in intents)


__all__ = [
    "GuidanceContextIdentity",
    "GuidanceIntent",
    "GuidanceIntentId",
    "GuidanceKind",
    "GuidanceProjection",
    "GuidanceState",
]
