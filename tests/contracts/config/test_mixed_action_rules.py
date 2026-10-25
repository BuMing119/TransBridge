from pathlib import Path

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.config_manager import ActionRule, apply_rules


def _repository(tmp_path: Path) -> ConfigRepository:
    path = tmp_path / "config.ini"
    return ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())


def _assert_default_actions(config: LLMConfig) -> None:
    entries = [
        TranslationEntry(str(stage), str(stage), "Source", "Translation" if stage else "", stage, "NPC_:FULL")
        for stage in (0, 1, 2, 3, 5, 9, -1)
    ]
    assert apply_rules(config.action_rules, entries) == {
        "0": "translate",
        "1": "polish",
        "2": "polish",
        "3": "polish",
        "5": "polish",
        "9": "skip",
        "-1": "skip",
    }


def test_new_config_has_independent_stable_mixed_defaults() -> None:
    first, second = LLMConfig(), LLMConfig()
    _assert_default_actions(first)
    assert first.action_rules == second.action_rules

    first.action_rules[0].status_filter.add(1)
    first.action_rules[0].action = "skip"
    first.action_rules.pop()

    _assert_default_actions(second)
    assert second.action_rules == LLMConfig().action_rules


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_missing_or_legacy_blank_rules_load_defaults_without_rewriting_file(tmp_path: Path, raw) -> None:
    repository = _repository(tmp_path)
    repository.update_sections({"llm": {"action_rules": raw}})
    before = repository.path.read_bytes()

    config = LLMConfig.load_from_file(repository=repository, environment={})
    _assert_default_actions(config)
    assert repository.path.read_bytes() == before

    config.save_to_file()
    reloaded = LLMConfig.load_from_file(repository=repository, environment={})
    assert reloaded.action_rules == config.action_rules
    _assert_default_actions(reloaded)


@pytest.mark.parametrize("rules", [[], [ActionRule("custom-skip", 0, {0}, action="skip")]])
def test_explicit_rules_survive_save_and_reload(tmp_path: Path, rules) -> None:
    repository = _repository(tmp_path)
    config = LLMConfig(action_rules=rules)
    config.save_to_file(repository=repository)

    reloaded = LLMConfig.load_from_file(repository=repository, environment={})

    assert reloaded.action_rules == rules
    if not rules:
        assert repository.load().value("llm", "action_rules") == "[]"
