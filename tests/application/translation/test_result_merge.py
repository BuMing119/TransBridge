from dataclasses import replace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    ResultEntryState,
    merge_result_entries,
)


def state(key="one", translation="base", stage=1, context="context"):
    return ResultEntryState(EntryKey(SourceNamespace("source:merge"), key), "original", translation, stage, context)


def merge(base, current, incoming, **options):
    return merge_result_entries(
        {base.entry_key: base},
        {} if current is None else {current.entry_key: current},
        {incoming.entry_key: incoming},
        **options,
    )


def test_manual_global_choice_requires_unchanged_reviewed_content():
    base, reviewed, latest, incoming = (
        state(),
        state(translation="reviewed"),
        state(translation="later"),
        state(translation="AI"),
    )
    plan = merge(
        base,
        latest,
        incoming,
        policy=ResultConflictPolicy.PREFER_INCOMING,
        decision_states={base.entry_key: reviewed},
    )
    assert not plan.applied
    assert plan.pending[0].reason == "decision_stale"


def test_repeated_manual_choice_is_noop_when_result_already_present():
    base, reviewed, incoming = state(), state(translation="reviewed"), state(translation="AI")
    plan = merge(
        base,
        incoming,
        incoming,
        policy=ResultConflictPolicy.PREFER_INCOMING,
        decision_states={base.entry_key: reviewed},
    )
    assert not plan.applied and not plan.pending
    assert plan.unchanged_keys == (base.entry_key,)


@pytest.mark.parametrize("policy", list(ResultConflictPolicy))
def test_incoming_unchanged_preserves_concurrent_edit_for_every_policy(policy):
    base = state()
    current = replace(base, translation="manual", stage=3)
    result = merge(base, current, base, policy=policy)
    assert not result.applied and not result.conflicts
    assert result.kept_current_keys == (base.entry_key,)


@pytest.mark.parametrize("policy", list(ResultConflictPolicy))
def test_true_conflict_keeps_complete_evidence_with_policy_decision(policy):
    base, current, incoming = state(), state(translation="manual", stage=3), state(translation="AI", stage=5)
    result = merge(base, current, incoming, policy=policy)
    assert len(result.conflicts) == 1
    evidence = result.conflicts[0]
    assert (evidence.base, evidence.current, evidence.incoming, evidence.decision) == (base, current, incoming, policy)
    assert result.pending == ((evidence,) if policy is ResultConflictPolicy.DEFER else ())
    assert result.applied == ((incoming,) if policy is ResultConflictPolicy.PREFER_INCOMING else ())


@pytest.mark.parametrize("change", ["missing", "deleted", "original", "context"])
@pytest.mark.parametrize("policy", list(ResultConflictPolicy))
def test_structural_changes_are_pending_even_when_preferring_incoming(change, policy):
    base = state()
    current = (
        None
        if change == "missing"
        else replace(
            base,
            **{
                "deleted": {"deleted": True},
                "original": {"original": "changed"},
                "context": {"context": "changed"},
            }[change],
        )
    )
    result = merge(base, current, replace(base, translation="result"), policy=policy)
    assert not result.applied and len(result.pending) == 1


def test_translation_and_stage_are_one_group_and_download_can_omit_context_dependency():
    base = state(context=None)
    current = replace(base, translation="manual", context="changed")
    incoming = replace(base, stage=5)
    result = merge(base, current, incoming)
    assert not result.applied and result.kept_current_keys == (base.entry_key,)
    assert result.conflicts[0].reason == "translation_stage_conflict"
    result = merge(base, replace(base, context="changed"), replace(base, translation="download"))
    assert result.applied[0].translation == "download"
    assert result.applied[0].context == "changed"


def test_independent_target_applies_while_other_target_remains_pending():
    first, second = state(), state("two")
    result = merge_result_entries(
        {entry.entry_key: entry for entry in (first, second)},
        {first.entry_key: replace(first, original="changed"), second.entry_key: second},
        {entry.entry_key: replace(entry, translation="result") for entry in (first, second)},
    )
    assert tuple(entry.entry_key for entry in result.applied) == (second.entry_key,)
    assert result.pending[0].entry_key == first.entry_key


def test_per_entry_policy_only_overrides_selected_targets_and_never_structural_changes():
    first, second, third = state(), state("two"), state("three")
    result = merge_result_entries(
        {entry.entry_key: entry for entry in (first, second, third)},
        {
            entry.entry_key: replace(entry, translation="manual", original="changed" if entry is third else "original")
            for entry in (first, second, third)
        },
        {entry.entry_key: replace(entry, translation="incoming") for entry in (first, second, third)},
        policies={
            first.entry_key: ResultConflictPolicy.PREFER_INCOMING,
            third.entry_key: ResultConflictPolicy.PREFER_INCOMING,
        },
    )
    assert tuple(entry.entry_key for entry in result.applied) == (first.entry_key,)
    assert result.kept_current_keys == (second.entry_key,)
    assert result.pending[0].entry_key == third.entry_key


def test_explicit_decision_is_not_reused_after_current_content_changes():
    base = state()
    reviewed = replace(base, translation="reviewed local")
    current = replace(reviewed, translation="new local edit")
    incoming = replace(base, translation="incoming")
    options = {
        "policies": {base.entry_key: ResultConflictPolicy.PREFER_INCOMING},
        "decision_states": {base.entry_key: reviewed},
    }
    accepted = merge(base, reviewed, incoming, **options)
    stale = merge(base, current, incoming, **options)
    assert accepted.applied == (incoming,)
    assert stale.applied == () and stale.pending[0].current == current
    assert stale.pending[0].reason == "decision_stale"


@pytest.mark.parametrize("captured_locator", [None, "captured-source"])
def test_merge_and_reverse_preserve_the_captured_optional_source_contract(captured_locator):
    base = replace(state(), source_locator=captured_locator)
    incoming = replace(base, translation="downloaded")
    current = replace(base, source_locator="captured-source")
    accepted = merge(base, current, incoming)
    assert accepted.applied == (incoming,)
    restored = merge(accepted.applied[0], replace(current, translation="downloaded"), base)
    assert restored.applied == (base,)
    if captured_locator:
        changed_source = replace(current, source_locator="different-source")
        rejected = merge(base, changed_source, incoming, policy=ResultConflictPolicy.PREFER_INCOMING)
        assert not rejected.applied
        assert rejected.pending[0].reason == "source_changed_or_missing"
