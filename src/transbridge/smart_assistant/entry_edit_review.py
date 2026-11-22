# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Keep the exact edits shown on a confirmation card with its executable steps."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from graphlib import TopologicalSorter

from .tools._editor_edits import PreparedEntryEdits, prepare_entry_edits

EDIT_TOOLS = frozenset({"edit_translation", "set_stage"})


def _source(context):
    return (
        getattr(context, "_target_version_identity", None),
        getattr(context, "_target_source_key", None),
        getattr(context, "_target_workbench_source", None),
    )


@dataclass(frozen=True, slots=True)
class EntryEditReview:
    tool: str
    args: dict
    source: tuple
    prepared: PreparedEntryEdits

    def resolve(self, tool, args, context) -> PreparedEntryEdits:
        if self.tool != tool or self.args != args:
            raise ValueError("操作参数与确认卡中的修改不一致，请重新确认。")
        if self.source != _source(context):
            raise ValueError("确认时的工程、版本或工作台来源已变化，请重新确认。")
        return self.prepared


def prepare_reviews(steps, context, collection):
    """Project each edit over its predecessors, keeping independent DAG branches separate."""
    reviews = {}
    if collection is None or not any(step.get("tool") in EDIT_TOOLS for step in steps):
        return reviews
    graph = any(step.get("depends_on") for step in steps)
    indices = {step.get("id"): i for i, step in enumerate(steps)}
    deps = (
        {i: tuple(indices[key] for key in step.get("depends_on", ())) for i, step in enumerate(steps)} if graph else {}
    )
    order = TopologicalSorter(deps).static_order() if graph else range(len(steps))
    ancestors = {}
    changes = {}
    projected = {}
    for index in order:
        if graph:
            parents = set(deps[index])
            for predecessor in deps[index]:
                parents.update(ancestors[predecessor])
            ancestors[index] = parents
            projected = {key: value for i, edits in changes.items() if i in parents for key, value in edits.items()}
        tool = steps[index].get("tool")
        if tool not in EDIT_TOOLS:
            continue
        args = steps[index].get("args", {})
        try:
            prepared = prepare_entry_edits(tool, args, context, collection, projected=projected)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            reviews[index] = str(error)
            continue
        reviews[index] = EntryEditReview(tool, deepcopy(args), deepcopy(_source(context)), prepared)
        updated = {edit.before.entry_key: edit.after for edit in prepared.edits}
        if graph:
            changes[index] = updated
        else:
            projected.update(updated)
    return reviews
