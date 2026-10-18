"""Explicit batch decisions without choosing unresolved translations for the user."""

from __future__ import annotations

from enum import StrEnum

from .decisions import DecisionCommand, DecisionOperation
from .drafts import DraftWriteExpectation
from .models import ConflictGroup, DecisionStatus, TerminologyDraft
from .review_guards import unresolved_originals


class TermBatchOperation(StrEnum):
    CONFIRM = "confirm"
    SUPPRESS = "suppress"
    REENABLE = "reenable"


def batch_commands(
    draft: TerminologyDraft,
    expectation: DraftWriteExpectation,
    identities: tuple[str, ...],
    operation: TermBatchOperation | str,
    conflicts: tuple[ConflictGroup, ...] = (),
) -> tuple[DecisionCommand, ...]:
    operation = TermBatchOperation(operation)
    if not identities or len(set(identities)) != len(identities):
        raise ValueError("请至少选择一条术语，且不能重复选择")
    decisions = {item.term_id: item for item in draft.decisions}
    if any(identity not in decisions for identity in identities):
        raise ValueError("所选术语已变化或不存在，请刷新后重试")
    unresolved = unresolved_originals(draft, conflicts)
    commands = []
    for identity in identities:
        decision = decisions[identity]
        if operation is not TermBatchOperation.SUPPRESS:
            if decision.status is DecisionStatus.UNRESOLVED or decision.normalized_original in unresolved:
                raise ValueError(f"“{decision.original}”存在未决定的异译，请先查看证据并审核")
            if not decision.translation.strip():
                raise ValueError(f"“{decision.original}”没有译名，请先编辑译名")
        if operation is TermBatchOperation.CONFIRM:
            if decision.suppressed:
                raise ValueError(f"“{decision.original}”已停用，请使用启用操作")
            command = DecisionCommand(
                operation=DecisionOperation.CHANGE_TRANSLATION,
                expectation=expectation,
                term_id=identity,
                translation=decision.translation,
            )
        else:
            command = DecisionCommand(
                operation=DecisionOperation(operation.value), expectation=expectation, term_id=identity
            )
        commands.append(command)
    return tuple(commands)
