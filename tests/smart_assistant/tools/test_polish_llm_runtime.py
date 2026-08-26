"""Smart Assistant polish calls share request admission and workflow logging."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.conftest import make_entry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


class _ProviderClient:
    def __init__(self, *, response="ok", error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if callable(self.response):
            return self.response(messages)
        return self.response

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        return self.chat(messages, max_tokens)

    def cancel(self) -> None:
        return None


def _config(**overrides):
    values = {
        "max_concurrent": 3,
        "max_tokens_per_batch": 10_000,
        "max_output_tokens": 0,
        "model": "fixture-model",
        "game_profile": "general",
        "target_lang": "zh_CN",
        "workflow_profiles": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _create_runtime(tmp_path: Path, provider: _ProviderClient, config=None):
    from transbridge.smart_assistant.tools._polish_llm_runtime import create_polish_llm_runtime

    config = config or _config()
    with (
        patch("transbridge.infra.llm_client.create_llm_client", return_value=provider),
        patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=str(tmp_path)),
    ):
        return create_polish_llm_runtime(config, esp_path=str(tmp_path / "Demo.esp"), stop_event=None)


def test_runtime_applies_one_shared_request_budget_and_writes_call_log(tmp_path: Path) -> None:
    runtime = _create_runtime(tmp_path, _ProviderClient(response='{"translation":"已润色"}'))

    assert runtime.request_budget.max_in_flight == 3
    assert runtime.client.chat([{"role": "user", "content": "润色"}], 0)
    assert runtime.request_budget.snapshot().peak_in_flight == 1
    runtime.close()

    logs = list(Path(runtime.log_store.log_dir).glob("llm_call_*.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "[REQUEST TO LLM]" in content
    assert "[RESPONSE FROM LLM]" in content
    assert "[REQUEST BUDGET]" in content


def test_runtime_records_provider_errors_in_the_corresponding_call_log(tmp_path: Path) -> None:
    runtime = _create_runtime(tmp_path, _ProviderClient(error=RuntimeError("provider unavailable")))

    with pytest.raises(RuntimeError, match="provider unavailable"):
        runtime.client.chat([{"role": "user", "content": "润色"}], 0)
    runtime.close()

    logs = list(Path(runtime.log_store.log_dir).glob("llm_call_*.log"))
    assert len(logs) == 1
    content = logs[0].read_text(encoding="utf-8")
    assert "[ERROR]" in content
    assert "provider unavailable" in content
    assert "[REQUEST BUDGET]" in content


def _respond_with_updated_translations(messages: list[dict]) -> str:
    entries = json.loads(messages[1]["content"])["entries"]
    return json.dumps(
        {
            "results": [
                {
                    "entry_key": entry["entry_key"],
                    "final_translation": f"校对:{entry['current_translation']}",
                }
                for entry in entries
            ]
        },
        ensure_ascii=False,
    )


def test_combined_strategy_batches_multiple_entries_into_one_logged_call(tmp_path: Path) -> None:
    from transbridge.smart_assistant.tools._polish_execution import execute_polish

    entries = [
        make_entry("first", original="First", translation="旧译一", stage=1),
        make_entry("second", original="Second", translation="旧译二", stage=1),
    ]
    collection = TranslationEntryCollection(entries)
    config = _config()
    provider = _ProviderClient(response=_respond_with_updated_translations)
    runtime = _create_runtime(tmp_path, provider, config)

    summary = execute_polish(
        strategy="combined",
        intensity="medium",
        llm_config=config,
        llm_client=runtime.client,
        term_manager=SimpleNamespace(match_terms=lambda _values: {}),
        targets=entries,
        collection=collection,
        stop_event=None,
    )
    runtime.close()

    assert provider.calls == 1
    assert summary.polished_count == 2
    assert summary.failed_count == 0
    assert collection.get("first").translation == "校对:旧译一"
    assert collection.get("second").translation == "校对:旧译二"
    assert len(list(Path(runtime.log_store.log_dir).glob("llm_call_*.log"))) == 1


def test_combined_strategy_retains_originals_and_logs_provider_error(tmp_path: Path) -> None:
    from transbridge.smart_assistant.tools._polish_execution import execute_polish

    entries = [
        make_entry("first", original="First", translation="旧译一", stage=1),
        make_entry("second", original="Second", translation="旧译二", stage=1),
    ]
    collection = TranslationEntryCollection(entries)
    config = _config()
    provider = _ProviderClient(error=TimeoutError("provider timeout"))
    runtime = _create_runtime(tmp_path, provider, config)

    summary = execute_polish(
        strategy="combined",
        intensity="medium",
        llm_config=config,
        llm_client=runtime.client,
        term_manager=SimpleNamespace(match_terms=lambda _values: {}),
        targets=entries,
        collection=collection,
        stop_event=None,
    )
    runtime.close()

    assert provider.calls == 1
    assert summary.polished_count == 0
    assert summary.failed_count == 2
    assert collection.get("first").translation == "旧译一"
    assert collection.get("second").translation == "旧译二"
    logs = list(Path(runtime.log_store.log_dir).glob("llm_call_*.log"))
    assert len(logs) == 1
    assert "provider timeout" in logs[0].read_text(encoding="utf-8")


def test_strict_strategy_is_forwarded_to_the_shared_pipeline_profile() -> None:
    from transbridge.smart_assistant.tools._polish_execution import execute_polish

    entry = make_entry("first", original="First", translation="旧译", stage=1)
    collection = TranslationEntryCollection([entry])
    captured = {}

    class Pipeline:
        @staticmethod
        def process(_targets, **_kwargs):
            return {}

    def create_pipeline(*, profile, **_kwargs):
        captured["strategy"] = profile.postprocess_strategy
        return Pipeline()

    with patch(
        "transbridge.ai_translator.post_processor.proofread_pipeline.ProofreadPipeline.create",
        side_effect=create_pipeline,
    ):
        summary = execute_polish(
            strategy="strict",
            intensity="heavy",
            llm_config=_config(),
            llm_client=_ProviderClient(),
            term_manager=SimpleNamespace(match_terms=lambda _values: {}),
            targets=[entry],
            collection=collection,
            stop_event=None,
        )

    assert captured["strategy"] == "strict"
    assert summary.failed_count == 1
