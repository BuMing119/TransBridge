"""Shared conflict-review constraints for manual edits and version membership."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .identity import normalize_original
from .models import ConflictGroup, ConflictStatus, DecisionStatus, TermDecision, TerminologyDraft

if TYPE_CHECKING:
    from .decisions import DecisionCommand


def unresolved_originals(draft: TerminologyDraft, conflicts: tuple[ConflictGroup, ...]) -> set[str]:
    resolutions = {item.conflict_group_id: item for item in draft.conflict_resolutions}
    return {
        item.normalized_original
        for original in conflicts
        if (item := resolutions.get(original.conflict_group_id, original)).status is ConflictStatus.UNRESOLVED
    }


def require_reviewed_edit(
    draft: TerminologyDraft, command: DecisionCommand, conflicts: tuple[ConflictGroup, ...]
) -> None:
    current = next((item for item in draft.decisions if item.term_id == command.term_id), None)
    originals = set()
    if current is not None:
        originals.add(current.normalized_original)
    if command.original is not None:
        originals.add(normalize_original(command.original))
    if command.conflict_resolution is not None:
        _require_explicit_resolution(command, conflicts, originals)
        return
    if command.operation == "suppress":
        return
    if (current is not None and current.status is DecisionStatus.UNRESOLVED) or originals.intersection(
        unresolved_originals(draft, conflicts)
    ):
        raise ValueError("此术语存在未决定的异译，请先查看证据并审核")


def _require_explicit_resolution(
    command: DecisionCommand, conflicts: tuple[ConflictGroup, ...], originals: set[str]
) -> None:
    resolution = command.conflict_resolution
    assert resolution is not None
    expected = {
        "unify_translation": ConflictStatus.UNIFIED,
        "plugin_exception": ConflictStatus.PLUGIN_EXCEPTION,
        "ignore_conflict": ConflictStatus.IGNORED,
    }.get(command.operation)
    if expected is None or resolution.status is not expected or originals != {resolution.normalized_original}:
        raise ValueError("异译审核结果与本次操作不一致，请重新审核")
    pinned = next((item for item in conflicts if item.conflict_group_id == resolution.conflict_group_id), None)
    if conflicts and (pinned is None or pinned.variants != resolution.variants):
        raise ValueError("异译证据已变化，请重新审核")
    if (resolution.project_id, resolution.variant_id) != (
        command.expectation.line.project_id,
        command.expectation.line.variant_id,
    ):
        raise ValueError("异译审核不属于当前工程和翻译版本")


def require_reviewed_effective_terms(decisions: tuple[TermDecision, ...], conflicts: tuple[ConflictGroup, ...]) -> None:
    unresolved = {item.normalized_original for item in conflicts if item.status is ConflictStatus.UNRESOLVED}
    for decision in decisions:
        if decision.is_effective and decision.normalized_original in unresolved:
            raise ValueError(f"“{decision.original}”存在未决定的异译，请先查看证据并审核后发布")
