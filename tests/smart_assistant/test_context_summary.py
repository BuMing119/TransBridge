from types import SimpleNamespace

import pytest

from transbridge.application.assistant_context.models import FrozenContextItem, PreparationWait, encode
from transbridge.infra.llm_tool_calling import LlmTurn
from transbridge.smart_assistant.context_budget import ContextBudget
from transbridge.smart_assistant.context_summary import IsolatedSummaryClient, SemanticSummaryGenerator

SOURCE = FrozenContextItem("source", "digest", encode({"role": "user", "content": "never export"}), "source")
VALID = encode({
    "discussion_context": "Export remains prohibited.",
    "decisions_with_sources": [],
    "unresolved_questions": [],
    "suggested_next_steps": [],
})


class Client:
    def __init__(self, text=VALID):
        self.calls = []
        self.text = text

    def chat_stream_with_tools(self, messages, max_tokens, **kwargs):
        self.calls.append((messages, max_tokens, kwargs))
        if kwargs["usage_callback"]:
            kwargs["usage_callback"]("reported-summary-usage")
        return LlmTurn(text=self.text, stop_reason="stop")


def test_summary_has_no_business_tools_and_reports_usage():
    client = Client()
    usages = []
    generator = SemanticSummaryGenerator(client, ContextBudget(), on_usage=usages.append)
    assert generator((SOURCE,), max_tokens=1000) == VALID
    assert client.calls[0][2]["tools"] == []
    assert client.calls[0][2]["purpose"] == "summary"
    assert usages == ["reported-summary-usage"]


def test_independent_call_budget_blocks_before_network():
    client = Client()
    generator = SemanticSummaryGenerator(client, ContextBudget(200, 50, 20))
    with pytest.raises(PreparationWait, match="CONTEXT_SUMMARY_CAPACITY"):
        generator((SOURCE,), max_tokens=100)
    assert not client.calls


def test_cancelled_summary_never_calls_model():
    client = Client()
    generator = SemanticSummaryGenerator(client, ContextBudget(), cancelled=lambda: True)
    with pytest.raises(PreparationWait, match="COMPACTION_CANCELLED"):
        generator((SOURCE,), max_tokens=1000)
    assert not client.calls


def test_cancelled_after_response_keeps_usage():
    client = Client()
    usages = []
    generator = SemanticSummaryGenerator(
        client, ContextBudget(), on_usage=usages.append, cancelled=lambda: bool(client.calls)
    )
    with pytest.raises(PreparationWait, match="COMPACTION_CANCELLED"):
        generator((SOURCE,), max_tokens=1000)
    assert usages == ["reported-summary-usage"]


def test_invalid_model_output_is_not_retried_by_adapter():
    client = Client('{"approval":true}')
    generator = SemanticSummaryGenerator(client, ContextBudget())
    with pytest.raises(PreparationWait, match="COMPACTION_INVALID_OUTPUT"):
        generator((SOURCE,), max_tokens=1000)
    assert len(client.calls) == 1


def test_repair_uses_original_sources_and_error_feedback():
    client = Client()
    generator = SemanticSummaryGenerator(client, ContextBudget())
    generator.repair((SOURCE,), max_tokens=1000, error="bad schema")
    messages = client.calls[0][0]
    assert "bad schema" in messages[0]["content"]
    assert "source" in messages[1]["content"]


def test_provider_failure_is_preserved_without_retry():
    def fail(*args, **kwargs):
        raise TimeoutError("timeout")

    generator = SemanticSummaryGenerator(SimpleNamespace(chat_stream_with_tools=fail), ContextBudget())
    with pytest.raises(PreparationWait, match="COMPACTION_GENERATION_FAILED") as error:
        generator((SOURCE,), max_tokens=1000)
    assert isinstance(error.value.__cause__, TimeoutError)


def test_isolated_client_is_lazy_disables_transport_retries_and_never_cancels_business(monkeypatch):
    made, closed = [], []

    def factory(config):
        made.append(config)
        return SimpleNamespace(
            chat_stream_with_tools=lambda *a, **kw: LlmTurn(text=VALID), cancel=lambda: closed.append("summary")
        )

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", factory)
    config = SimpleNamespace(model="configured", api_key="test", llm_max_retries=2)
    fallback = SimpleNamespace(cancel=lambda: closed.append("business"))
    client = IsolatedSummaryClient(config, fallback)
    assert not made
    assert client.chat_stream_with_tools([], 100, [], lambda _: None).text == VALID
    assert made[0].llm_max_retries == 0
    assert config.llm_max_retries == 2
    client.cancel()
    client.cancel()
    assert closed == ["summary"]


def test_isolated_fake_fallback_is_not_owned_or_cancelled():
    client = Client()
    isolated = IsolatedSummaryClient(SimpleNamespace(), client)
    generator = SemanticSummaryGenerator(isolated, ContextBudget())
    assert generator((SOURCE,), max_tokens=1000) == VALID
    isolated.cancel()  # Fake client intentionally has no cancel method.
    with pytest.raises(PreparationWait, match="COMPACTION_CANCELLED"):
        generator((SOURCE,), max_tokens=1000)
    assert len(client.calls) == 1


def test_cancel_before_first_use_does_not_construct_summary_client(monkeypatch):
    made = []
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda config: made.append(config))
    client = IsolatedSummaryClient(SimpleNamespace(model="model", api_key="test"), None)
    client.cancel()
    with pytest.raises(PreparationWait, match="COMPACTION_CANCELLED"):
        client.chat_stream_with_tools([], 100, [], lambda _: None)
    assert not made
