from dataclasses import replace

from transbridge.application.assistant_context.budget_policy import BudgetPolicy, summary_call_budget, summary_messages
from transbridge.application.assistant_context.models import ContextEpoch, FrozenContextItem, encode, state_item
from transbridge.smart_assistant.context_budget import ContextBudget


def test_high_boundary_triggers_and_uses_same_estimator():
    state = state_item({})
    history = tuple(
        FrozenContextItem(str(n), str(n), encode({"role": "assistant", "content": "x" * 500}), str(n))
        for n in range(12)
    )
    epoch = ContextEpoch("s", "r", (), "e", "c", "[]", items=(state,) + history)
    base = ContextBudget(10000, 100, 20, estimator=len, estimator_label="test-characters")
    dynamic = base.count(epoch.messages) - base.count([])
    # Choose exact integer arithmetic: dynamic * 5 / 4 is the 80% boundary.
    available = (dynamic * 5 + 3) // 4
    under = replace(base, context_window=base.count([]) + 120 + available + 1)
    over = replace(base, context_window=under.context_window - 2)
    policy = BudgetPolicy()
    assert policy.decide(epoch, under).action == "append"
    result = policy.decide(epoch, over)
    assert result.action == "compact"
    assert result.estimator_label == "test-characters"
    assert result.segment_tokens == min(2048, int(result.dynamic_budget * 0.15))
    assert summary_call_budget(over, result.segment_tokens).measure(summary_messages(result.selected)).fits


def test_complete_tool_group_is_selected_atomically():
    budget = ContextBudget(10000, 200, 20, estimator=len, estimator_label="characters")
    items = [state_item({})]
    for n in range(5):
        call = {"role": "assistant", "content": "", "tool_calls": [{"id": str(n), "name": "read"}]}
        result = {"role": "tool", "content": "x" * 1800, "tool_call_id": str(n)}
        items.extend([
            FrozenContextItem(f"c{n}", f"c{n}", encode(call), str(n)),
            FrozenContextItem(f"r{n}", f"r{n}", encode(result), str(n)),
        ])
    epoch = ContextEpoch("s", "r", (), "e", "c", "[]", items=tuple(items))
    decision = BudgetPolicy().decide(epoch, budget)
    assert decision.action == "compact"
    selected = {i.item_id for i in decision.selected}
    for n in range(5):
        assert (f"c{n}" in selected) == (f"r{n}" in selected)
    assert "c4" not in selected


def test_unknown_model_uses_labeled_offline_estimate():
    budget = ContextBudget(100)
    epoch = ContextEpoch("s", "r", (), "e", "c", "[]", items=(state_item({}),))
    result = BudgetPolicy().decide(epoch, budget)
    assert result.action == "capacity_wait"
    assert result.code == "CONTEXT_REQUIRED_TOO_LARGE"
    assert result.estimator_label == "text-v2-estimated-25pct"
