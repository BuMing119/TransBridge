from dataclasses import replace
import json

import pytest

from transbridge.application.assistant_context.compaction import compact, validate_summary
from transbridge.application.assistant_context.models import (
    CompactionSummary,
    ContextEpoch,
    FrozenContextItem,
    PreparationWait,
    encode,
    state_item,
)
from transbridge.smart_assistant.context_budget import ContextBudget


def item(name, content="x" * 700, role="assistant", **extra):
    return FrozenContextItem(name, name + "-digest", encode({"role": role, "content": content, **extra}), name)


def epoch(items=(), summaries=()):
    return ContextEpoch(
        "s",
        "r",
        (),
        "initial",
        "config",
        "[]",
        summaries,
        (state_item({}),) + tuple(items),
        tuple((i.item_id, i.source_digest) for i in items),
    )


def semantic(items):
    return encode({
        "discussion_context": "Historical constraints remain relevant.",
        "decisions_with_sources": [{"statement": "Keep negation", "source_ids": [items[0].item_id]}],
        "unresolved_questions": [],
        "suggested_next_steps": [],
    })


class Summarizer:
    def __init__(self):
        self.calls = []

    def __call__(self, items, **kwargs):
        self.calls.append(items)
        return semantic(items)


@pytest.fixture
def budget():
    return ContextBudget(7000, 300, 50, estimator=len, estimator_label="test-characters")


def test_below_high_does_not_call(budget):
    original = epoch([item("user", "short", "user")])
    fake = Summarizer()
    assert compact(original, budget, [], {}, fake) is original
    assert fake.calls == []


def test_three_cycles_preserve_every_old_segment_and_uncovered_item(budget):
    current = epoch()
    fake = Summarizer()
    for cycle in range(3):
        old = current
        incoming = tuple(item(f"{cycle}-{n}") for n in range(8)) + (item(f"user-{cycle}", "now", "user"),)
        current = replace(
            current,
            items=current.items + incoming,
            source_digests=current.source_digests + tuple((i.item_id, i.source_digest) for i in incoming),
        )
        prepared = compact(current, budget, [], {}, fake)
        assert prepared.epoch_id != current.epoch_id
        assert prepared.summaries[: len(old.summaries)] == old.summaries
        assert prepared.source_digests == current.source_digests
        covered = {s for summary in prepared.summaries for s in summary.covered_sources}
        assert prepared.items == tuple(i for i in current.items if i.item_id not in covered)
        assert prepared.items[0] == old.items[0]
        assert prepared.items[-1] == incoming[-1]
        current = ContextEpoch.from_dict(prepared.to_dict())
    assert len(current.summaries) >= 3
    assert len({s for segment in current.summaries for s in segment.covered_sources}) == sum(
        len(segment.covered_sources) for segment in current.summaries
    )


def test_full_summary_chain_capacity_is_detected_without_call(budget):
    original = epoch([item("user", "now", "user")], (CompactionSummary("old", "x" * 5400, ()),))
    fake = Summarizer()
    with pytest.raises(PreparationWait, match="CONTEXT_SUMMARY_CAPACITY"):
        compact(original, budget, [], {}, fake)
    assert not fake.calls


def test_huge_current_input_cannot_be_summarized(budget):
    fake = Summarizer()
    with pytest.raises(PreparationWait, match="CONTEXT_REQUIRED_TOO_LARGE"):
        compact(epoch([item("user", "x" * 10000, "user")]), budget, [], {}, fake)
    assert not fake.calls


def test_capacity_wait_explains_local_budget_and_preserves_input(budget):
    original = epoch([item("user", "x" * 10000, "user")])
    tools = [{"name": "read", "description": "read records"}]
    fake = Summarizer()
    with pytest.raises(PreparationWait) as error:
        compact(original, budget, tools, {}, fake)
    message = str(error.value)
    assert "配置窗口 7,000" in message
    assert "消息" in message and "工具定义" in message and "输出预留 300" in message
    assert "协议余量 50" in message and "不是服务端实际用量" in message
    assert not fake.calls
    assert original.items[-1].message["content"] == "x" * 10000


def test_incomplete_protocol_group_is_never_selected(budget):
    pending = item("pending", "", tool_calls=[{"id": "call", "name": "read", "arguments": {}}])
    original = epoch([item(str(n)) for n in range(7)] + [pending, item("user", "now", "user")])
    fake = Summarizer()
    result = compact(original, budget, [], {}, fake)
    assert pending in result.items
    assert all(pending not in group for group in fake.calls)


def test_malformed_protocol_blocks_before_model(budget):
    fake = Summarizer()
    with pytest.raises(PreparationWait, match="CONTEXT_PROTOCOL_INCOMPLETE"):
        compact(epoch([item("bad", "result", "tool", tool_call_id="missing")]), budget, [], {}, fake)
    assert not fake.calls


def test_unavailable_summarizer_is_not_retried(budget):
    calls = []

    def fail(items, **kwargs):
        calls.append(items)
        raise TimeoutError("provider timeout")

    with pytest.raises(PreparationWait, match="COMPACTION_GENERATION_FAILED") as failure:
        compact(epoch([item(str(n)) for n in range(8)]), budget, [], {}, fail)
    assert isinstance(failure.value.__cause__, TimeoutError)
    assert len(calls) == 1


def test_huge_history_stops_at_three_calls_with_unpublished_progress(budget):
    original = epoch([item(str(n)) for n in range(40)] + [item("user", "now", "user")])
    fake = Summarizer()
    with pytest.raises(PreparationWait, match="COMPACTION_CALL_LIMIT") as failure:
        compact(original, budget, [], {}, fake)
    assert len(fake.calls) == 3
    candidate = failure.value.candidate_epoch
    assert len(candidate.summaries) == 3
    assert candidate.source_digests == original.source_digests
    assert len(candidate.items) < len(original.items)
    assert not original.summaries


def test_schema_rejects_forged_sources_and_authority():
    sources = (item("source"),)
    data = json.loads(semantic(sources))
    data["approval"] = True
    with pytest.raises(PreparationWait, match="COMPACTION_INVALID_OUTPUT"):
        validate_summary(encode(data), sources)
    del data["approval"]
    data["decisions_with_sources"][0]["source_ids"] = ["forged"]
    with pytest.raises(PreparationWait, match="COMPACTION_INVALID_OUTPUT"):
        validate_summary(encode(data), sources)


def test_stale_state_never_reaches_model(budget):
    fake = Summarizer()
    with pytest.raises(PreparationWait, match="CONTEXT_STATE_CHANGED"):
        compact(epoch(), budget, [], {"approval": True}, fake)
    assert not fake.calls


def test_required_state_accepts_json_equivalent_request_item_tuples(budget):
    from transbridge.application.assistant_requests.models import RequestItem

    required = {"items": [RequestItem("answer", "Explain").to_dict()]}
    original = replace(epoch(), items=(state_item(required),))
    assert compact(original, budget, [], required, Summarizer()) is original


def test_one_bounded_repair_receives_feedback_and_original_sources(budget):
    class Repairable(Summarizer):
        def __call__(self, items, **kwargs):
            self.calls.append(items)
            return "bad-json"

        def repair(self, items, *, error, **kwargs):
            assert "JSON" in error
            self.calls.append(items)
            return semantic(items)

    fake = Repairable()
    result = compact(epoch([item(str(n)) for n in range(8)]), budget, [], {}, fake)
    assert result.summaries
    assert len(fake.calls) == 2
    assert fake.calls[0] == fake.calls[1]


def test_second_invalid_response_cannot_trigger_third_charge(budget):
    class Invalid(Summarizer):
        def __call__(self, items, **kwargs):
            self.calls.append(items)
            return "bad-json"

        def repair(self, items, **kwargs):
            return self(items)

    fake = Invalid()
    with pytest.raises(PreparationWait, match="COMPACTION_INVALID_OUTPUT"):
        compact(epoch([item(str(n)) for n in range(8)]), budget, [], {}, fake)
    assert len(fake.calls) == 2


def test_failed_later_chunk_exposes_verified_progress(budget):
    class LaterFailure(Summarizer):
        def __call__(self, items, **kwargs):
            if self.calls:
                raise PreparationWait("COMPACTION_CANCELLED", "cancelled")
            return super().__call__(items, **kwargs)

    fake = LaterFailure()
    original = epoch([item(str(n)) for n in range(40)])
    with pytest.raises(PreparationWait, match="COMPACTION_CANCELLED") as error:
        compact(original, budget, [], {}, fake)
    assert len(error.value.candidate_epoch.summaries) == 1
    assert not original.summaries


def test_no_gain_candidate_is_rejected_without_extra_call(budget):
    # A short old group precedes the latest user input, which must remain pinned.
    original = epoch([item("old", "x" * 700), item("user", "x" * 3800, "user"), item("tail", "x" * 700)])

    class Verbose(Summarizer):
        def __call__(self, items, **kwargs):
            data = json.loads(super().__call__(items, **kwargs))
            data["discussion_context"] = "x" * 350
            return encode(data)

    fake = Verbose()
    with pytest.raises(PreparationWait, match="COMPACTION_NO_GAIN"):
        compact(original, budget, [], {}, fake)
    assert len(fake.calls) == 1


def test_disabled_compaction_can_keep_a_hard_budget_safe_epoch(budget):
    original = epoch([item(str(n)) for n in range(8)])
    fake = Summarizer()
    assert compact(original, budget, [], {}, fake, enabled=False) is original
    assert not fake.calls


def test_disabled_compaction_keeps_hard_safe_full_summary_chain(budget):
    original = epoch([item("user", "now", "user")], (CompactionSummary("old", "x" * 5400, ()),))
    fake = Summarizer()
    assert compact(original, budget, [], {}, fake, enabled=False) is original
    assert not fake.calls


def test_new_epoch_places_current_state_before_retained_history(budget):
    original = epoch([item(str(n)) for n in range(8)] + [item("user", "now", "user")])
    latest = state_item({"goal": "preserved exactly"})
    original = replace(original, items=original.items + (latest,))
    result = compact(original, budget, [], {"goal": "preserved exactly"}, Summarizer())
    assert result.items[0] == latest
    assert all(
        i in result.items or any(i.item_id in s.covered_sources for s in result.summaries) for i in original.items
    )
    assert result.messages[len(result.summaries)] == latest.message


def test_retained_old_state_does_not_replace_current_snapshot_after_reordering(budget):
    original = epoch([item(str(n)) for n in range(8)] + [item("user", "now", "user")])
    historical = state_item({"goal": "superseded"})
    latest = state_item({"goal": "current"})
    original = replace(original, items=original.items + (historical, latest), state_digest=latest.source_digest)
    result = compact(original, budget, [], {"goal": "current"}, Summarizer())
    assert result.items[0] == latest
    assert historical in result.items
    assert compact(result, budget, [], {"goal": "current"}, Summarizer()) is result
