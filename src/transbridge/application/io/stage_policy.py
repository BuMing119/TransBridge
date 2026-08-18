"""Discrete translation-stage policy shared by application adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Protocol, runtime_checkable

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity


class Stage(Enum):
    """The seven legacy wire values without ordinal comparison semantics."""

    HIDDEN = -1
    UNTRANSLATED = 0
    TRANSLATED = 1
    QUESTIONABLE = 2
    CHECKED = 3
    REVIEWED = 5
    LOCKED = 9

    @classmethod
    def from_value(cls, value: int | Stage) -> Stage | None:
        if isinstance(value, cls):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        try:
            return cls(value)
        except ValueError:
            return None


class StageOperation(StrEnum):
    AI = "ai"
    PREVIEW = "preview"
    PUBLISH = "publish"
    TM_READ = "tm_read"
    TM_WRITE = "tm_write"


@dataclass(frozen=True, slots=True)
class StageDecision:
    stage: Stage | None
    operation: StageOperation
    include_ai: bool
    include_tm: bool
    preview_text: str
    publish_text: str | None
    diagnostic: Diagnostic | None = None
    blocks_publish: bool = False
    policy_version: str = "1"

    @property
    def code(self) -> str | None:
        return self.diagnostic.code if self.diagnostic else None

    @property
    def severity(self) -> DiagnosticSeverity | None:
        return self.diagnostic.severity if self.diagnostic else None


@runtime_checkable
class StagePolicyPort(Protocol):
    version: str

    def evaluate(
        self,
        stage: int | Stage,
        translation: str,
        operation: StageOperation,
        *,
        original: str = "",
    ) -> StageDecision: ...


class StagePolicy:
    """Evaluate each stage through explicit membership, never numeric order."""

    version = "1"
    _AI_EDITABLE = frozenset({
        Stage.UNTRANSLATED,
        Stage.TRANSLATED,
        Stage.QUESTIONABLE,
        Stage.CHECKED,
        Stage.REVIEWED,
    })
    _TM_READABLE = _AI_EDITABLE
    _TM_WRITABLE = frozenset({
        Stage.TRANSLATED,
        Stage.QUESTIONABLE,
        Stage.CHECKED,
        Stage.REVIEWED,
    })
    _PUBLISH_TRANSLATION = _TM_WRITABLE

    def evaluate(
        self,
        stage: int | Stage,
        translation: str,
        operation: StageOperation,
        *,
        original: str = "",
    ) -> StageDecision:
        if not isinstance(translation, str) or not isinstance(original, str):
            raise TypeError("stage policy text values must be strings")
        if not isinstance(operation, StageOperation):
            operation = StageOperation(operation)

        resolved = Stage.from_value(stage)
        if resolved is None:
            return StageDecision(
                None,
                operation,
                False,
                False,
                original,
                None,
                Diagnostic(
                    "STAGE_INVALID",
                    "The entry stage is not one of the seven supported values.",
                    details=(("stage", stage),),
                ),
                True,
                self.version,
            )

        if resolved is Stage.HIDDEN:
            return StageDecision(
                resolved,
                operation,
                False,
                False,
                original,
                original,
                policy_version=self.version,
            )

        if resolved is Stage.LOCKED:
            if translation:
                return StageDecision(
                    resolved,
                    operation,
                    False,
                    False,
                    translation,
                    translation,
                    policy_version=self.version,
                )
            return StageDecision(
                resolved,
                operation,
                False,
                False,
                original,
                None,
                Diagnostic(
                    "STAGE_LOCKED_TRANSLATION_REQUIRED",
                    "A locked entry has no translation and blocks formal publication.",
                    DiagnosticSeverity.ERROR,
                ),
                True,
                self.version,
            )

        selected_text = translation or original
        projected_text = selected_text if resolved in self._PUBLISH_TRANSLATION else original
        include_tm = operation is StageOperation.TM_READ and resolved in self._TM_READABLE
        if operation is StageOperation.TM_WRITE:
            include_tm = resolved in self._TM_WRITABLE and bool(translation)
        return StageDecision(
            resolved,
            operation,
            resolved in self._AI_EDITABLE,
            include_tm,
            projected_text,
            projected_text,
            policy_version=self.version,
        )

    def allows_ai(self, stage: int | Stage, translation: str = "", *, original: str = "") -> bool:
        return self.evaluate(stage, translation, StageOperation.AI, original=original).include_ai

    def allows_tm_read(self, stage: int | Stage, translation: str = "", *, original: str = "") -> bool:
        return self.evaluate(stage, translation, StageOperation.TM_READ, original=original).include_tm

    def allows_tm_write(self, stage: int | Stage, translation: str, *, original: str = "") -> bool:
        return self.evaluate(stage, translation, StageOperation.TM_WRITE, original=original).include_tm


DEFAULT_STAGE_POLICY = StagePolicy()
