from types import SimpleNamespace

import pytest

from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository


def repository(tmp_path):
    return ConfigRepository(
        tmp_path / "transbridge.ini",
        legacy_path=tmp_path / "legacy.ini",
        credential_store=UnavailableCredentialStore(),
    )


def test_missing_concurrency_uses_100_in_config_and_custom_profile(tmp_path):
    repo = repository(tmp_path)
    repo.update_sections({"llm": {"temperature": 0.1}})
    assert LLMConfig().max_concurrent == 100
    assert LLMConfig.load_from_file(repository=repo, environment={}).max_concurrent == 100
    assert CustomWorkflowProfile.from_config("defaults", "translate", SimpleNamespace()).limits["max_concurrent"] == 100


@pytest.mark.parametrize("value", [3, 20, 50, 100, 128])
def test_explicit_concurrency_and_execution_copy_are_preserved(tmp_path, value):
    repo = repository(tmp_path)
    LLMConfig(max_concurrent=value).save_to_file(repository=repo)
    config = LLMConfig.load_from_file(repository=repo, environment={})
    assert config.max_concurrent == value
    execution = config.copy_for_execution()
    config.max_concurrent = 1
    assert execution.max_concurrent == value

    from transbridge.ui.tools.ai_translator.task_config_snapshot import execution_snapshot, restore_execution_snapshot

    restored = restore_execution_snapshot(LLMConfig(), execution_snapshot(execution))
    assert restored.max_concurrent == value
