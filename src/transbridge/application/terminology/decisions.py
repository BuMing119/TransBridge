"""Audited human decision commands for terminology drafts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from transbridge.application.contracts import RequestContext
from transbridge.application.ports import ClockPort, IdGeneratorPort

from .drafts import DraftService, DraftWriteExpectation, revised_draft
from .identity import canonical_digest, normalize_original, term_id
from .models import (
    ConflictGroup,
    DecisionStatus,
    ManualAction,
    ManualActionType,
    ScopeKind,
    TermDecision,
    TerminologyDraft,
    TermScope,
)


class DecisionOperation(StrEnum):
    ADD = "add"
    CHANGE_TRANSLATION = "change_translation"
    REPLACE_ORIGINAL = "replace_original"
    CHANGE_SCOPE = "change_scope"
    CHANGE_VARIANTS = "change_variants"
    CHANGE_NOTES = "change_notes"
    UNIFY_TRANSLATION = "unify_translation"
    PLUGIN_EXCEPTION = "plugin_exception"
    IGNORE_CONFLICT = "ignore_conflict"
    SUPPRESS = "suppress"
    REENABLE = "reenable"


@dataclass(frozen=True, slots=True)
class ManualActor:
    actor_id: str
    trusted: bool

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("manual actor identity must not be empty")
        if self.trusted is not True:
            raise ValueError("manual actor identity must come from a trusted identity port")


class ManualActorPort(Protocol):
    """Resolve an auditable human identity; owner_id alone is not sufficient."""

    def resolve(self, context: RequestContext) -> ManualActor: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionCommand:
    operation: DecisionOperation
    expectation: DraftWriteExpectation
    term_id: str | None = None
    original: str | None = None
    translation: str | None = None
    scope: TermScope | None = None
    variants: tuple[str, ...] | None = None
    notes: str | None = None
    reason: str | None = None
    conflict_resolution: ConflictGroup | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", DecisionOperation(self.operation))
        for name in ("term_id", "original", "translation"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name.replace('_', ' ')} must be absent or non-empty")
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason must be absent or non-empty")
        if self.variants is not None and any(not item.strip() for item in self.variants):
            raise ValueError("variants must not contain empty values")


class DecisionService:
    def __init__(
        self,
        drafts: DraftService,
        actors: ManualActorPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
    ) -> None:
        self._drafts = drafts
        self._actors = actors
        self._clock = clock
        self._ids = ids

    def apply(self, command: DecisionCommand, context: RequestContext) -> TerminologyDraft:
        self._validate_context(command.expectation, context)
        actor = self._actors.resolve(context)
        if not isinstance(actor, ManualActor) or actor.trusted is not True:
            raise ValueError("manual actor identity is not trusted")
        current = self._drafts.active(
            command.expectation.line.project_id,
            command.expectation.line.variant_id,
        )
        self._drafts.require_expected(current, command.expectation)
        decisions, action_type, action_term_id, replacement_term_id = self._apply_operation(current, command)
        before = _decision_digest(current.decisions)
        after = _decision_digest(decisions)
        occurred_at = self._clock.now()
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("manual action clock must return a timezone-aware datetime")
        action = ManualAction(
            action_id=self._new_action_id(),
            term_id=action_term_id,
            action_type=action_type,
            actor=actor.actor_id,
            occurred_at=occurred_at.isoformat(),
            base_version_id=current.ref.base_version_id,
            before_digest=before,
            after_digest=after,
            reason=command.reason,
            replacement_term_id=replacement_term_id,
        )
        updated = revised_draft(
            current,
            decisions=decisions,
            actions=(*current.actions, action),
            conflict_resolutions=_upsert_conflict_resolution(
                current.conflict_resolutions,
                command.conflict_resolution,
            ),
            digest_context={"manual_action": action.action_id},
        )
        return self._drafts.save(updated, expectation=command.expectation)

    def _apply_operation(
        self,
        draft: TerminologyDraft,
        command: DecisionCommand,
    ) -> tuple[tuple[TermDecision, ...], ManualActionType, str, str | None]:
        operation = command.operation
        if operation is DecisionOperation.ADD:
            added = self._new_decision(draft, command)
            return _insert(draft.decisions, added), ManualActionType.ADD, added.term_id, None
        if operation is DecisionOperation.PLUGIN_EXCEPTION:
            if command.scope is None or command.scope.kind is not ScopeKind.PLUGIN:
                raise ValueError("plugin exception requires an explicit plugin scope")
            added = self._new_decision(draft, command)
            return _insert(draft.decisions, added), ManualActionType.RESOLVE_CONFLICT, added.term_id, None
        if operation is DecisionOperation.UNIFY_TRANSLATION:
            return self._unify(draft, command)
        if operation is DecisionOperation.IGNORE_CONFLICT:
            return self._ignore(draft, command)

        current = _required_decision(draft, command.term_id)
        replacement_id = None
        if operation is DecisionOperation.CHANGE_TRANSLATION:
            updated = replace(
                current,
                translation=_required(command.translation, "translation"),
                status=DecisionStatus.MANUAL_CONFIRMED,
            )
            action_type = ManualActionType.CHANGE_TRANSLATION
        elif operation is DecisionOperation.REPLACE_ORIGINAL:
            original = _required(command.original, "replacement original")
            scope = current.scope if command.scope is None else command.scope
            replacement_id = term_id(
                project_id=current.project_id,
                variant_id=current.variant_id,
                scope=scope,
                original=original,
            )
            replacement = TermDecision(
                term_id=replacement_id,
                project_id=current.project_id,
                variant_id=current.variant_id,
                original=original,
                normalized_original=normalize_original(original),
                translation=current.translation if command.translation is None else command.translation,
                scope=scope,
                status=DecisionStatus.MANUAL_CONFIRMED,
                variants=current.variants,
                notes=current.notes if command.notes is None else command.notes,
                replacement_of=current.term_id,
                evidence_ids=(),
            )
            decisions = _replace(draft.decisions, replace(current, suppressed=True), replacement)
            return decisions, ManualActionType.REPLACE_ORIGINAL, current.term_id, replacement_id
        elif operation is DecisionOperation.CHANGE_SCOPE:
            if command.scope is None:
                raise ValueError("scope change requires a scope")
            replacement_id = term_id(
                project_id=current.project_id,
                variant_id=current.variant_id,
                scope=command.scope,
                original=current.original,
            )
            replacement = replace(
                current,
                term_id=replacement_id,
                scope=command.scope,
                status=DecisionStatus.MANUAL_CONFIRMED,
                replacement_of=current.term_id,
            )
            decisions = _replace(draft.decisions, replace(current, suppressed=True), replacement)
            return decisions, ManualActionType.CHANGE_SCOPE, current.term_id, replacement_id
        elif operation is DecisionOperation.CHANGE_VARIANTS:
            if command.variants is None:
                raise ValueError("variant change requires variants")
            updated = replace(current, variants=command.variants, status=DecisionStatus.MANUAL_CONFIRMED)
            action_type = ManualActionType.CHANGE_ATTRIBUTES
        elif operation is DecisionOperation.CHANGE_NOTES:
            if command.notes is None:
                raise ValueError("notes change requires a notes value")
            updated = replace(current, notes=command.notes, status=DecisionStatus.MANUAL_CONFIRMED)
            action_type = ManualActionType.CHANGE_ATTRIBUTES
        elif operation is DecisionOperation.SUPPRESS:
            updated = replace(current, suppressed=True, status=DecisionStatus.MANUAL_CONFIRMED)
            action_type = ManualActionType.SUPPRESS
        elif operation is DecisionOperation.REENABLE:
            translation = current.translation if current.translation else _required(command.translation, "translation")
            updated = replace(
                current,
                translation=translation,
                suppressed=False,
                status=DecisionStatus.MANUAL_CONFIRMED,
            )
            action_type = ManualActionType.REENABLE
        else:  # pragma: no cover - exhaustive enum guard
            raise ValueError(f"unsupported terminology decision operation: {operation.value}")
        return _replace(draft.decisions, updated), action_type, current.term_id, replacement_id

    def _new_decision(self, draft: TerminologyDraft, command: DecisionCommand) -> TermDecision:
        original = _required(command.original, "original")
        translation = _required(command.translation, "translation")
        scope = command.scope or TermScope.project()
        identity = term_id(
            project_id=draft.ref.project_id,
            variant_id=draft.ref.variant_id,
            scope=scope,
            original=original,
        )
        return TermDecision(
            identity,
            draft.ref.project_id,
            draft.ref.variant_id,
            original,
            normalize_original(original),
            translation,
            scope=scope,
            status=DecisionStatus.MANUAL_CONFIRMED,
            variants=command.variants or (),
            notes=command.notes or "",
        )

    def _unify(
        self,
        draft: TerminologyDraft,
        command: DecisionCommand,
    ) -> tuple[tuple[TermDecision, ...], ManualActionType, str, None]:
        if command.term_id is None:
            decision = self._new_decision(draft, command)
            decisions = _insert(draft.decisions, decision)
        else:
            current = _required_decision(draft, command.term_id)
            decision = replace(
                current,
                translation=_required(command.translation, "unified translation"),
                suppressed=False,
                status=DecisionStatus.MANUAL_CONFIRMED,
            )
            decisions = _replace(draft.decisions, decision)
        return decisions, ManualActionType.RESOLVE_CONFLICT, decision.term_id, None

    def _ignore(
        self,
        draft: TerminologyDraft,
        command: DecisionCommand,
    ) -> tuple[tuple[TermDecision, ...], ManualActionType, str, None]:
        if command.term_id is None:
            original = _required(command.original, "conflict original")
            scope = command.scope or TermScope.project()
            identity = term_id(
                project_id=draft.ref.project_id,
                variant_id=draft.ref.variant_id,
                scope=scope,
                original=original,
            )
            decision = TermDecision(
                identity,
                draft.ref.project_id,
                draft.ref.variant_id,
                original,
                normalize_original(original),
                "",
                scope=scope,
                status=DecisionStatus.MANUAL_CONFIRMED,
                suppressed=True,
                notes=command.notes or "",
            )
            decisions = _insert(draft.decisions, decision)
        else:
            current = _required_decision(draft, command.term_id)
            decision = replace(current, suppressed=True, status=DecisionStatus.MANUAL_CONFIRMED)
            decisions = _replace(draft.decisions, decision)
        return decisions, ManualActionType.IGNORE_CONFLICT, decision.term_id, None

    def _new_action_id(self) -> str:
        identity = self._ids.new_id()
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("ID generator returned an empty manual action identity")
        return identity

    @staticmethod
    def _validate_context(expectation: DraftWriteExpectation, context: RequestContext) -> None:
        if (context.project_id, context.variant_id) != (
            expectation.line.project_id,
            expectation.line.variant_id,
        ):
            raise PermissionError("manual decision context does not match the draft Project/Variant")


def _required(value: str | None, label: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _required_decision(draft: TerminologyDraft, identity: str | None) -> TermDecision:
    if identity is None:
        raise ValueError("decision operation requires a term ID")
    try:
        return next(item for item in draft.decisions if item.term_id == identity)
    except StopIteration as exc:
        raise KeyError(f"term decision was not found: {identity}") from exc


def _insert(decisions: tuple[TermDecision, ...], added: TermDecision) -> tuple[TermDecision, ...]:
    if any(item.term_id == added.term_id for item in decisions):
        raise ValueError("a decision already exists for this term identity")
    return (*decisions, added)


def _replace(
    decisions: tuple[TermDecision, ...],
    updated: TermDecision,
    added: TermDecision | None = None,
) -> tuple[TermDecision, ...]:
    replaced = tuple(updated if item.term_id == updated.term_id else item for item in decisions)
    return replaced if added is None else _insert(replaced, added)


def _decision_digest(decisions: tuple[TermDecision, ...]) -> str:
    return canonical_digest(decisions, namespace="terminology.manual-decision-state.v1")


def _upsert_conflict_resolution(
    current: tuple[ConflictGroup, ...],
    resolution: ConflictGroup | None,
) -> tuple[ConflictGroup, ...]:
    if resolution is None:
        return current
    return (*tuple(item for item in current if item.conflict_group_id != resolution.conflict_group_id), resolution)


__all__ = [
    "DecisionCommand",
    "DecisionOperation",
    "DecisionService",
    "ManualActor",
    "ManualActorPort",
]
