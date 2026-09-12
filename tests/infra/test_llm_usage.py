from dataclasses import replace
import json
from pathlib import Path

import pytest

from transbridge.infra.llm_tool_calling import LlmTurn
from transbridge.infra.llm_usage import LlmUsage, UsageAttempt, single_attempt_client
from transbridge.smart_assistant.observability.models import TokenStats


def test_openai_cache_is_subset_and_raw_fields_are_allowlisted():
    attempt = UsageAttempt("openai", "test", "routing")
    attempt.observe(
        {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "prompt_tokens_details": {"cached_tokens": 80},
            "secret": "must not persist",
        },
        final=True,
    )
    usage = attempt.finish()
    assert (usage.input_tokens, usage.uncached_input_tokens, usage.cache_read_tokens) == (100, 20, 80)
    assert usage.cache_write_tokens is None
    assert usage.completeness == "complete"
    assert "secret" not in usage.to_dict()["raw_usage"]


def test_anthropic_counters_replace_cumulative_snapshots():
    attempt = UsageAttempt("anthropic", "test", "summary")
    attempt.observe({
        "input_tokens": 5,
        "output_tokens": 1,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 20,
    })
    attempt.observe({"output_tokens": 8})
    attempt.observe({"output_tokens": 12}, final=True)
    usage = attempt.finish()
    assert (usage.input_tokens, usage.output_tokens) == (55, 12)
    assert usage.uncached_input_tokens == 5
    assert usage.source == "reported"


@pytest.mark.parametrize("value", [None, -1, True, "3", 1.5])
def test_invalid_counter_is_unknown(value):
    attempt = UsageAttempt("openai", "test", "execution")
    attempt.observe({"prompt_tokens": value}, final=True)
    assert attempt.finish().input_tokens is None


def test_zero_is_reported_and_distinct_from_unknown():
    attempt = UsageAttempt("openai", "test", "execution")
    attempt.observe({"prompt_tokens": 0, "completion_tokens": 0}, final=True)
    assert attempt.finish().source == "reported"
    assert attempt.finish().completeness == "complete"
    assert UsageAttempt("openai", "test", "execution").finish().source == "unknown"


def test_partial_usage_survives_cancellation_and_missing_cache_is_not_zero():
    attempt = UsageAttempt("anthropic", "test", "execution")
    attempt.observe({"input_tokens": 8, "output_tokens": 1})
    usage = attempt.finish(outcome="cancelled")
    assert usage.completeness == "partial"
    assert usage.input_tokens is None
    assert usage.uncached_input_tokens == 8
    assert usage.cache_read_tokens is None
    assert usage.output_tokens == 1


def test_finish_and_ledger_redelivery_are_idempotent_but_retries_are_distinct():
    delivered = []
    attempt = UsageAttempt("openai", "test", "summary", delivered.append)
    attempt.observe({"prompt_tokens": 10, "completion_tokens": 5}, final=True)
    first = attempt.finish()
    assert attempt.finish() is first
    assert delivered == [first]
    retry = replace(first, attempt_id="retry", retry_of=first.attempt_id)
    stats = TokenStats()
    assert stats.add_usage(first)
    assert not stats.add_usage(first)
    assert stats.add_usage(retry)
    assert stats.usage_totals()["known_input_tokens"] == 20
    assert stats.input_tokens == 0  # The legacy estimate is an independent column.


def test_usage_never_becomes_assistant_content():
    turn = LlmTurn(text="hello", usage=LlmUsage(input_tokens=123))
    assert turn.to_assistant_message() == {"role": "assistant", "content": "hello"}


def test_synthetic_old_provider_payload_baseline_remains_reproducible():
    from transbridge.infra.anthropic_tool_calling import _convert_messages as anthropic_messages
    from transbridge.infra.openai_tool_calling import _convert_messages as openai_messages
    from transbridge.infra.prompt_cache import build_anthropic_system_blocks

    path = Path(__file__).parents[1] / "fixtures/assistant_context_compaction/legacy_usage_baseline.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    assert openai_messages(fixture["messages"]) == fixture["normalized_openai_messages"]
    system, messages = build_anthropic_system_blocks(fixture["messages"], enable_cache=False)
    assert system == fixture["normalized_anthropic_system"]
    assert anthropic_messages(messages) == fixture["normalized_anthropic_messages"]


def test_sdk_retry_copy_retains_original_client_and_sets_zero_retries():
    class Client:
        def __init__(self, http, retries=4):
            self.http = http
            self.retries = retries

        def with_options(self, *, max_retries):
            return Client(self.http, max_retries)

    original = Client(object())
    copy = single_attempt_client(original)
    assert copy.retries == 0
    assert original.retries == 4
    assert copy.http is original.http
