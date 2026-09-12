"""Assistant policy flags persist independently and old configuration keeps defaults."""

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository


def _repository(tmp_path):
    return ConfigRepository(
        tmp_path / "transbridge.ini",
        legacy_path=tmp_path / "legacy.ini",
        credential_store=UnavailableCredentialStore(),
    )


def test_old_configuration_defaults_to_automatic_summary_and_cache(tmp_path):
    repository = _repository(tmp_path)
    repository.update_sections({"llm": {"assistant_context_window": 16384}})
    config = LLMConfig.load_from_file(repository=repository, environment={})
    assert config.assistant_auto_compaction is True
    assert config.assistant_prompt_cache is True
    assert config.assistant_context_window == 16384


@pytest.mark.parametrize("auto,cache", [(False, False), (False, True), (True, False), (True, True)])
def test_policy_flags_round_trip_independently_and_copy_to_execution(tmp_path, auto, cache):
    repository = _repository(tmp_path)
    config = LLMConfig(assistant_auto_compaction=auto, assistant_prompt_cache=cache)
    config.save_to_file(repository=repository)
    restored = LLMConfig.load_from_file(repository=_repository(tmp_path), environment={})
    assert restored.assistant_auto_compaction is auto
    assert restored.assistant_prompt_cache is cache
    execution = restored.copy_for_execution()
    restored.assistant_auto_compaction = not auto
    restored.assistant_prompt_cache = not cache
    assert execution.assistant_auto_compaction is auto
    assert execution.assistant_prompt_cache is cache


def test_invalid_policy_values_keep_safe_defaults(tmp_path):
    repository = _repository(tmp_path)
    repository.update_sections({"llm": {"assistant_auto_compaction": "invalid", "assistant_prompt_cache": "invalid"}})
    config = LLMConfig.load_from_file(repository=repository, environment={})
    assert config.assistant_auto_compaction is True and config.assistant_prompt_cache is True
