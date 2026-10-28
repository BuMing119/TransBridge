"""Pure three-way merge of asynchronous results into current entry content."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import Stage


class ResultConflictPolicy(StrEnum):
    KEEP_CURRENT = "keep_current"
    PREFER_INCOMING = "prefer_incoming"
    DEFER = "defer"


@dataclass(frozen=True, slots=True)
class ResultEntryState:
    entry_key: EntryKey
    original: str
    translation: str
    stage: int
    context: str | None = None
    deleted: bool = False
    source_locator: str | None = None

    def __post_init__(self):
        if not all(isinstance(value, str) for value in (self.original, self.translation)) or (
            self.context is not None and not isinstance(self.context, str)
        ):
            raise TypeError("result entry content must be text")
        stage = Stage.from_value(self.stage)
        if stage is None:
            raise ValueError("result entry stage is unsupported")
        object.__setattr__(self, "stage", stage.value)


@dataclass(frozen=True, slots=True)
class ResultEntryChange:
    base: ResultEntryState
    incoming: ResultEntryState

    def __post_init__(self):
        if self.base.entry_key != self.incoming.entry_key:
            raise ValueError("result change must retain entry identity")


@dataclass(frozen=True, slots=True)
class ResultMergeConflict:
    entry_key: EntryKey
    reason: str
    base: ResultEntryState
    current: ResultEntryState | None
    incoming: ResultEntryState
    decision: str


@dataclass(frozen=True, slots=True)
class ResultMergePlan:
    applied: tuple[ResultEntryState, ...] = ()
    unchanged_keys: tuple[EntryKey, ...] = ()
    kept_current_keys: tuple[EntryKey, ...] = ()
    conflicts: tuple[ResultMergeConflict, ...] = ()
    pending: tuple[ResultMergeConflict, ...] = ()


def merge_result_entries(
    base: Mapping[EntryKey, ResultEntryState],
    current: Mapping[EntryKey, ResultEntryState],
    incoming: Mapping[EntryKey, ResultEntryState],
    *,
    policy: ResultConflictPolicy = ResultConflictPolicy.KEEP_CURRENT,
    protect_context: bool = True,
    policies: Mapping[EntryKey, ResultConflictPolicy] | None = None,
    decision_states: Mapping[EntryKey, ResultEntryState] | None = None,
) -> ResultMergePlan:
    """Merge only result targets; revisions and unrelated entry metadata are irrelevant."""
    policy = ResultConflictPolicy(policy)
    applied, unchanged, kept, conflicts, pending = [], [], [], [], []
    for key, proposed in incoming.items():
        selected_policy = ResultConflictPolicy(policies.get(key, policy)) if policies is not None else policy
        original = base[key]
        latest = current.get(key)
        stale_decision = False
        if decision_states is not None and (policies is None or key in policies):
            reviewed = decision_states.get(key)
            comparable = (
                replace(latest, source_locator=None)
                if (latest is not None and reviewed is not None and reviewed.source_locator is None)
                else latest
            )
            if reviewed is None or comparable != reviewed:
                selected_policy = ResultConflictPolicy.DEFER
                stale_decision = True
        if any(state.entry_key != key for state in (original, proposed)) or (
            latest is not None and latest.entry_key != key
        ):
            raise ValueError("result mapping key differs from entry identity")
        structural = (
            latest is None
            or latest.deleted
            or original.deleted
            or proposed.deleted
            or original.source_locator is not None
            and (latest.source_locator != original.source_locator or proposed.source_locator != original.source_locator)
            or latest.original != original.original
            or proposed.original != original.original
            or protect_context
            and original.context is not None
            and latest.context != original.context
            or protect_context
            and original.context is not None
            and proposed.context != original.context
        )
        if structural:
            conflict = ResultMergeConflict(key, "source_changed_or_missing", original, latest, proposed, "defer")
            conflicts.append(conflict)
            pending.append(conflict)
            continue
        before = original.translation, original.stage
        now = latest.translation, latest.stage
        after = proposed.translation, proposed.stage
        if stale_decision and now != after and after != before:
            evidence = ResultMergeConflict(key, "decision_stale", original, latest, proposed, "defer")
            conflicts.append(evidence)
            pending.append(evidence)
            continue
        conflict = before != now and after != before and after != now
        if conflict:
            evidence = ResultMergeConflict(
                key, "translation_stage_conflict", original, latest, proposed, selected_policy.value
            )
            conflicts.append(evidence)
            if selected_policy is ResultConflictPolicy.DEFER:
                pending.append(evidence)
                continue
        if after == before or now == after or conflict and selected_policy is ResultConflictPolicy.KEEP_CURRENT:
            if now != before and now != after:
                kept.append(key)
            else:
                unchanged.append(key)
        else:
            applied.append(
                ResultEntryState(
                    key,
                    latest.original,
                    proposed.translation,
                    proposed.stage,
                    latest.context,
                    source_locator=proposed.source_locator,
                )
            )
    return ResultMergePlan(tuple(applied), tuple(unchanged), tuple(kept), tuple(conflicts), tuple(pending))


def source_locator_signature(metadata, string_id, fingerprint):
    """Bind source-owned location and metadata independently of editable state."""
    payload = {"metadata": dict(metadata), "string_id": string_id, "fingerprint": fingerprint}
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
