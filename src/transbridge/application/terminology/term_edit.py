"""Complete term edits with stable identity and auditable replacements."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .identity import normalize_original, term_id
from .models import DecisionStatus, ManualActionType, TermDecision, TerminologyDraft

if TYPE_CHECKING:
    from .decisions import DecisionCommand


def edit_decision(
    draft: TerminologyDraft, current: TermDecision, command: DecisionCommand
) -> tuple[tuple[TermDecision, ...], ManualActionType, str, str | None]:
    if command.original is None or command.translation is None or command.scope is None:
        raise ValueError("请填写原名、译名和使用范围")
    if command.variants is None or command.notes is None:
        raise ValueError("完整编辑需要包含别名和备注")
    original = command.original.strip()
    translation = command.translation.strip()
    normalized = normalize_original(original)
    identity = term_id(
        project_id=current.project_id, variant_id=current.variant_id, scope=command.scope, original=original
    )
    replacement = identity != current.term_id
    updated = replace(
        current,
        term_id=identity,
        original=original,
        normalized_original=normalized,
        translation=translation,
        scope=command.scope,
        variants=command.variants,
        notes=command.notes,
        status=DecisionStatus.MANUAL_CONFIRMED,
        replacement_of=current.term_id if replacement else current.replacement_of,
        evidence_ids=() if normalized != current.normalized_original else current.evidence_ids,
    )
    action = ManualActionType.CHANGE_ATTRIBUTES
    if original != current.original:
        action = ManualActionType.REPLACE_ORIGINAL
    elif command.scope != current.scope:
        action = ManualActionType.CHANGE_SCOPE
    elif translation != current.translation:
        action = ManualActionType.CHANGE_TRANSLATION
    return (
        replace_identity(draft.decisions, current, updated),
        action,
        current.term_id,
        identity if replacement else None,
    )


def replace_identity(
    decisions: tuple[TermDecision, ...], current: TermDecision, updated: TermDecision
) -> tuple[TermDecision, ...]:
    """Move an edited identity to the end of its own replacement chain."""
    if updated.term_id == current.term_id:
        updated = replace(updated, replacement_of=current.replacement_of)
        return tuple(updated if item.term_id == current.term_id else item for item in decisions)
    lookup = {item.term_id: item for item in decisions}
    historical = lookup.get(updated.term_id)
    if historical is None:
        retained = tuple(
            replace(item, suppressed=True) if item.term_id == current.term_id else item for item in decisions
        )
        return (*retained, updated)
    successor = current
    visited = {current.term_id}
    while successor.replacement_of != historical.term_id:
        predecessor = lookup.get(successor.replacement_of)
        if predecessor is None or predecessor.term_id in visited:
            raise ValueError("此原名和使用范围的术语已存在，请编辑已有术语")
        visited.add(predecessor.term_id)
        successor = predecessor
    if not historical.suppressed or any(
        item.replacement_of == historical.term_id and item.term_id != successor.term_id for item in decisions
    ):
        raise ValueError("此原名和使用范围的术语已存在，请编辑已有术语")
    # Splice the restored node out of the current chain before appending it.
    # Immutable versions and ManualAction rows retain the original transitions;
    # the live chain remains acyclic so every former identity resolves to this edit.
    result = []
    for item in decisions:
        if item.term_id == historical.term_id:
            result.append(updated)
            continue
        if item.term_id == current.term_id:
            item = replace(item, suppressed=True)
        if item.term_id == successor.term_id:
            item = replace(item, replacement_of=historical.replacement_of)
        result.append(item)
    return tuple(result)
