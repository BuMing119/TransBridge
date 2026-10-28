from __future__ import annotations

from pathlib import Path

import pytest

from transbridge.application.translation.ai_request_budget import AiRequestBudget
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.ui.tools.ai_translator.workflow_log_store import WorkflowLogStore
from transbridge.ui.tools.ai_translator.workflow_logging_client import WorkflowLoggingLLMClient


def test_workflow_logging_client_persists_prompt_and_response(tmp_path: Path) -> None:
    class Client:
        def chat(self, messages, max_tokens=0):
            assert messages[0]["content"] == "校对这段文本"
            assert max_tokens == 500
            return "校对后的回复"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="polish", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store)

    assert client.chat([{"role": "user", "content": "校对这段文本"}], 500) == "校对后的回复"
    store.close()

    content = (Path(store.log_dir) / "llm_call_001.log").read_text(encoding="utf-8")
    assert "[REQUEST TO LLM]" in content
    assert "校对这段文本" in content
    assert "[RESPONSE FROM LLM]" in content
    assert "校对后的回复" in content


def test_workflow_logging_client_records_shared_request_budget_metrics(tmp_path: Path) -> None:
    class Client:
        def chat(self, _messages, max_tokens=0):
            return "ok"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    limited = LimitedLLMClient(Client(), AiRequestBudget(2))
    client = WorkflowLoggingLLMClient(limited, store)

    assert client.chat([{"role": "user", "content": "request"}], 20) == "ok"
    store.close()

    content = (Path(store.log_dir) / "llm_call_001.log").read_text(encoding="utf-8")
    assert "[REQUEST BUDGET]" in content
    assert "in_flight=1" in content
    assert "peak_in_flight=1" in content


def test_grouped_term_logging_keeps_complete_conversations_in_one_tab(tmp_path: Path) -> None:
    class Client:
        def chat(self, messages, max_tokens=0):
            return f"response:{messages[0]['content']}:{max_tokens}"

        def cancel(self) -> None:
            pass

    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    client = WorkflowLoggingLLMClient(Client(), store, channel_prefix="term_llm", grouped=True)

    client.chat([{"role": "user", "content": "first prompt"}], 1000)
    client.chat([{"role": "user", "content": "second prompt"}], 1000)
    store.close()

    content = (Path(store.log_dir) / "term_llm.log").read_text(encoding="utf-8")
    assert content.count("[REQUEST TO LLM]") == 2
    assert "[CALL 001]" in content and "[CALL 002]" in content
    assert "first prompt" in content and "response:first prompt:1000" in content
    assert "second prompt" in content and "response:second prompt:1000" in content
    assert content.count("[END CALL]") == 2


def test_workflow_logging_failures_do_not_change_llm_result_or_error() -> None:
    class BrokenStore:
        def write_chunk(self, _channel: str, _content: str) -> None:
            raise OSError("disk full")

        def write_line(self, _channel: str, _content: str) -> None:
            raise OSError("disk full")

    class SuccessfulClient:
        def chat(self, _messages, _max_tokens=0):
            return "model result"

        def cancel(self) -> None:
            pass

    class FailingClient(SuccessfulClient):
        def chat(self, _messages, _max_tokens=0):
            raise RuntimeError("provider failure")

    messages = [{"role": "user", "content": "text"}]
    assert WorkflowLoggingLLMClient(SuccessfulClient(), BrokenStore()).chat(messages) == "model result"
    with pytest.raises(RuntimeError, match="provider failure"):
        WorkflowLoggingLLMClient(FailingClient(), BrokenStore()).chat(messages)


def test_workflow_log_store_ignores_late_writes_after_close(tmp_path: Path) -> None:
    store = WorkflowLogStore("plugin.esp", workflow="mixed", log_base=tmp_path)
    store.write_chunk("batch_001", "complete response")
    store.close()

    store.write_chunk("batch_001", "late chunk")

    assert (Path(store.log_dir) / "batch_001.log").read_text(encoding="utf-8") == "complete response"


def test_workflow_log_directory_failure_does_not_block_worker(monkeypatch) -> None:
    def fail_data_dir() -> str:
        raise OSError("read-only data directory")

    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.workflow_log_store.ParatranzConfig.get_data_dir",
        fail_data_dir,
    )

    store = WorkflowLogStore("plugin.esp", workflow="polish")
    store.write_line("workflow", "still running")
    store.close()

    assert not store.is_available
    assert store.log_dir == ""
    assert store.last_error == "read-only data directory"
