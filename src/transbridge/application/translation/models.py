"""Immutable translation planning contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any

from transbridge.application.io.identity import EntryKey


class TranslationAction(StrEnum):
    TRANSLATE = "translate"
    POLISH = "polish"
    BOTH = "both"
    SKIP = "skip"


class RetrievalStatus(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class RetrievalSnapshot:
    status: RetrievalStatus
    manifest: tuple[str, ...] = ()
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status is RetrievalStatus.DISABLED and self.manifest:
            raise ValueError("disabled retrieval cannot contain a loaded manifest")
        if self.status is RetrievalStatus.DEGRADED and not self.reason_code:
            raise ValueError("degraded retrieval requires a reason code")


@dataclass(frozen=True, slots=True)
class TranslationRunSpec:
    run_id: str
    config_revision: int
    input_revision: int
    source_locale: str
    target_locale: str
    prompt_profile: str
    provider: str
    base_url: str
    model: str
    parameters: tuple[tuple[str, str], ...]
    retrieval: RetrievalSnapshot
    scope: tuple[EntryKey, ...]

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must not be empty")
        for name, value in (
            ("config_revision", self.config_revision),
            ("input_revision", self.input_revision),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if len(set(self.scope)) != len(self.scope):
            raise ValueError("run scope must not contain duplicate EntryKeys")
        if not all(
            value.strip()
            for value in (
                self.source_locale,
                self.target_locale,
                self.prompt_profile,
                self.provider,
                self.base_url,
                self.model,
            )
        ):
            raise ValueError("run configuration fields must not be empty")

    @property
    def fingerprint(self) -> str:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "config_revision": self.config_revision,
            "input_revision": self.input_revision,
            "source_locale": self.source_locale,
            "target_locale": self.target_locale,
            "prompt_profile": self.prompt_profile,
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "parameters": self.parameters,
            "retrieval": {
                "status": self.retrieval.status.value,
                "manifest": self.retrieval.manifest,
                "reason_code": self.retrieval.reason_code,
            },
            "scope": [key.to_dict() for key in self.scope],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanningEntry:
    key: EntryKey
    stage: int
    original: str
    translation: str = ""
    context: str = ""
    labels: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActionRuleSpec:
    rule_id: str
    priority: int
    action: TranslationAction
    stages: frozenset[int] | None = None
    labels: frozenset[str] | None = None
    contexts: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class ActionAssignment:
    key: EntryKey
    action: TranslationAction
    reason: str
    rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionPlan:
    scope: tuple[EntryKey, ...]
    assignments: tuple[ActionAssignment, ...]

    def __post_init__(self) -> None:
        assigned = tuple(item.key for item in self.assignments)
        if len(set(assigned)) != len(assigned):
            raise ValueError("an ActionPlan must assign each key exactly once")
        if set(assigned) != set(self.scope):
            raise ValueError("ActionPlan assignments must be a complete scope partition")

    def partition(self, action: TranslationAction) -> tuple[EntryKey, ...]:
        return tuple(item.key for item in self.assignments if item.action is action)


@dataclass(frozen=True, slots=True)
class ContextBatch:
    round_number: int
    category: str
    keys: tuple[EntryKey, ...]
    quest_id: str = ""
    quest_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ContextPlan:
    batches: tuple[ContextBatch, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def keys(self) -> tuple[EntryKey, ...]:
        return tuple(key for batch in self.batches for key in batch.keys)
