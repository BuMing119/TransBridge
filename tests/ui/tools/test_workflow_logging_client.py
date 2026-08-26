from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_client import AnthropicClient
from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient


def _content(store: WorkflowLogStore, channel: str = "llm_call_001") -> str:
    store.close()
    return (Path(store.log_dir) / f"{channel}.log").read_text(encoding="utf-8")


def test_log_store_can_share_an_existing_translation_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "existing-run"
    store = WorkflowLogStore("plugin.esp", workflow="translation", log_dir=run_dir)

    store.write_line("diagnostic", "visible")
    store.close()

    assert store.log_dir == str(run_dir)
    assert (run_dir / "diagnostic.log").read_text(encoding="utf-8") == "visible\n"


def test_prepared_logging_reserves_id_and_logs_after_budget_admission(tmp_path: Path) -> None:
    class _Client:
        def chat(self, messages, max_tokens=0):
            return f"response:{messages[0]['content']}:{max_tokens}"

        def cancel(self) -> None:
            pass

    budget = AiRequestBudget(1)
    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(
        LimitedLLMClient(_Client(), budget),
        store,
    )
    result = client.chat_prepared(lambda: [{"role": "user", "content": "prepared prompt"}], 30)

    assert result == "response:prepared prompt:30"
    content = _content(store)
    assert content.index("[CALL 001]") < content.index("[REQUEST TO LLM]")
    assert "prepared prompt" in content
    assert "response:prepared prompt:30" in content
    assert "[REQUEST BUDGET]" in content
    assert content.count("[END CALL]") == 1
    assert budget.snapshot().in_flight == 0


def test_preparation_failure_keeps_call_id_logs_metrics_and_releases_budget(tmp_path: Path) -> None:
    class _Client:
        def chat(self, _messages, max_tokens=0):
            raise AssertionError("provider must not be called")

        def cancel(self) -> None:
            pass

    budget = AiRequestBudget(1)
    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(LimitedLLMClient(_Client(), budget), store)

    def fail() -> list[dict]:
        raise ValueError("cannot build prompt")

    with pytest.raises(ValueError, match="cannot build prompt"):
        client.chat_prepared(fail)

    content = _content(store)
    assert "[CALL 001]" in content
    assert "[REQUEST TO LLM]" not in content
    assert '"exception_type": "ValueError"' in content
    assert "cannot build prompt" in content
    assert "[REQUEST BUDGET]" in content
    assert content.count("[END CALL]") == 1
    assert budget.snapshot().in_flight == 0


def test_provider_error_records_diagnostics_and_redacts_credentials(tmp_path: Path) -> None:
    class _Response:
        status_code = 429
        text = '{"error":"rate limited","api_key":"body-secret","token":"body-token"}'
        headers = {"x-request-id": "req-response", "authorization": "Bearer header-secret"}

    class _ProviderError(RuntimeError):
        status_code = 429
        code = "rate_limit"
        body = {"message": "retry", "api_key": "exception-secret"}
        request_id = "req-exception"
        response = _Response()

    class _Client:
        def chat(self, _messages, max_tokens=0):
            raise _ProviderError("api_key=message-secret Bearer bearer-secret sk-1234567890")

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(_Client(), store)
    messages = [
        {
            "role": "user",
            "content": "Authorization: Bearer prompt-secret sk-abcdefghijk",
            "api_key": "request-secret",
        }
    ]

    with pytest.raises(_ProviderError):
        client.chat(messages, 100)

    content = _content(store)
    assert '"exception_type": "_ProviderError"' in content
    assert '"status_code": 429' in content
    assert '"code": "rate_limit"' in content
    assert '"request_id": "req-exception"' in content
    assert '"x-request-id": "req-response"' in content
    assert "rate limited" in content
    assert _ProviderError.__name__ in content
    assert "***REDACTED***" in content
    for secret in (
        "body-secret",
        "body-token",
        "exception-secret",
        "message-secret",
        "bearer-secret",
        "sk-1234567890",
        "prompt-secret",
        "sk-abcdefghijk",
        "request-secret",
        "header-secret",
    ):
        assert secret not in content
    assert content.count("[END CALL]") == 1


def test_anthropic_missing_output_limit_is_captured_in_the_existing_call_log(tmp_path: Path) -> None:
    delegate = AnthropicClient.__new__(AnthropicClient)
    delegate._client = MagicMock()
    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(delegate, store)

    with pytest.raises(ValueError, match="Anthropic API requires a positive max_tokens"):
        client.chat([{"role": "user", "content": "prompt"}], 0)

    content = _content(store)
    assert "[REQUEST TO LLM]" in content
    assert '"exception_type": "ValueError"' in content
    assert "输出 Token" in content
    assert "greater than 0" in content
    assert content.count("[END CALL]") == 1
    delegate._client.messages.create.assert_not_called()


@pytest.mark.parametrize("response", ["", "   "])
def test_empty_successful_response_is_explicitly_classified(tmp_path: Path, response: str) -> None:
    class _Client:
        def chat(self, _messages, max_tokens=0):
            return response

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(_Client(), store)

    assert client.chat([{"role": "user", "content": "prompt"}]) == response

    content = _content(store)
    assert "[EMPTY RESPONSE]" in content
    assert "empty successful response" in content
    assert content.count("[END CALL]") == 1
