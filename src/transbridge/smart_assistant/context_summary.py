"""Isolated semantic summary adapter using the configured client and reported usage."""

from copy import deepcopy
from threading import Event, Lock

from transbridge.application.assistant_context.budget_policy import summary_call_budget, summary_messages
from transbridge.application.assistant_context.compaction import validate_summary
from transbridge.application.assistant_context.models import PreparationWait
from transbridge.smart_assistant.context_budget import ContextBudgetExceeded


class IsolatedSummaryClient:
    """Lazily own a summary-only provider; cancellation never closes the business client."""

    def __init__(self, config, fallback_client):
        self._config = config
        self._fallback = fallback_client
        self._owned_client = None
        self._lock = Lock()
        self._cancelled = Event()

    def _check_cancelled(self):
        if self._cancelled.is_set():
            raise PreparationWait("COMPACTION_CANCELLED", "摘要客户端已取消。")

    def chat_stream_with_tools(
        self, messages, max_tokens, tools, chunk_callback, *, usage_callback=None, purpose="summary"
    ):
        self._check_cancelled()
        if tools or purpose != "summary":
            raise ValueError("The isolated summary client accepts only tool-free summary calls")
        with self._lock:
            self._check_cancelled()
            if (
                self._owned_client is None
                and getattr(self._config, "model", "")
                and getattr(self._config, "api_key", "")
            ):
                from transbridge.infra.llm_client import create_llm_client

                config = deepcopy(self._config)
                config.llm_max_retries = 0
                self._owned_client = create_llm_client(config)
            client = self._owned_client or self._fallback
        self._check_cancelled()

        def on_chunk(text):
            self._check_cancelled()
            chunk_callback(text)

        turn = client.chat_stream_with_tools(
            messages, max_tokens, tools=tools, chunk_callback=on_chunk, usage_callback=usage_callback, purpose=purpose
        )
        self._check_cancelled()
        return turn

    def cancel(self):
        """Caller schedules shutdown off the GUI thread; no client is created for cleanup."""
        self._cancelled.set()
        with self._lock:
            owned, self._owned_client = self._owned_client, None
        if owned is not None:
            owned.cancel()


class SemanticSummaryGenerator:
    """One invocation is one model attempt; orchestration alone owns bounded repair."""

    def __init__(self, client, budget, on_usage=None, cancelled=lambda: False):
        self.client = client
        self.budget = budget
        self.on_usage = on_usage
        self.cancelled = cancelled

    def _check_cancelled(self):
        if self.cancelled():
            raise PreparationWait("COMPACTION_CANCELLED", "摘要已取消，原上下文保持有效。")

    def __call__(self, items, *, max_tokens, background=()):
        return self._generate(items, max_tokens, background)

    def repair(self, items, *, max_tokens, background=(), error):
        return self._generate(items, max_tokens, background, error)

    def _generate(self, items, max_tokens, background, feedback=""):
        self._check_cancelled()
        messages = summary_messages(items, background, feedback)
        try:
            summary_call_budget(self.budget, max_tokens).require(messages)
        except ContextBudgetExceeded as exc:
            raise PreparationWait("CONTEXT_SUMMARY_CAPACITY", "本次摘要原文及输出超出独立调用预算。") from exc
        try:
            turn = self.client.chat_stream_with_tools(
                messages,
                max_tokens,
                tools=[],
                chunk_callback=lambda _text: self._check_cancelled(),
                usage_callback=self.on_usage,
                purpose="summary",
            )
        except PreparationWait:
            raise
        except Exception as exc:
            self._check_cancelled()
            raise PreparationWait("COMPACTION_GENERATION_FAILED", f"摘要模型调用失败：{exc}") from exc
        self._check_cancelled()
        if turn.stop_reason == "cancelled":
            raise PreparationWait("COMPACTION_CANCELLED", "摘要模型调用已取消。")
        if turn.tool_calls or turn.stop_reason in {"length", "max_tokens", "error"}:
            raise PreparationWait("COMPACTION_INVALID_OUTPUT", "摘要返回工具调用或不完整内容。")
        return validate_summary(turn.text, items)
