# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Quest dependency planning and compatible access to the shared executor."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from transbridge.application.translation.batch_execution import BatchExecutionSnapshot, BatchExecutor

if TYPE_CHECKING:
    from transbridge.ai_translator.batch_planner import Batch


def quest_batch_chains(batches: Iterable[Batch]) -> list[list[Batch]]:
    """Preserve explicit Quest order; unknown Quest batches are independent."""

    chains: list[list[Batch]] = []
    known: dict[str, list[Batch]] = {}
    for batch in batches:
        quest = (batch.quest_formid or "").strip()
        if not quest:
            chains.append([batch])
        elif quest in known:
            known[quest].append(batch)
        else:
            known[quest] = [batch]
            chains.append(known[quest])
    return chains


__all__ = ["BatchExecutionSnapshot", "BatchExecutor", "quest_batch_chains"]
