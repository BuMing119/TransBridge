from copy import deepcopy
import json

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.paratranz.config_manager import ActionRule
from transbridge.ui.tools.ai_translator.task_config_snapshot import execution_snapshot, restore_execution_snapshot
from transbridge.ui.tools.ai_translator.task_recovery_binding import signature


@pytest.mark.parametrize("custom", [False, True])
def test_rule_filters_roundtrip_through_json_task_snapshot_and_keep_signature(custom):
    config = LLMConfig(api_key="original-secret")
    if custom:
        config.action_rules = [
            ActionRule("custom", 0, {0, 2}, {"second", "first"}, {"INFO:NAM1", "NPC_:FULL"}, "translate")
        ]

    encoded = json.dumps(execution_snapshot(config), ensure_ascii=False)
    assert "original-secret" not in encoded
    restored = restore_execution_snapshot(LLMConfig(api_key="current-secret"), json.loads(encoded))

    assert restored.api_key == "current-secret"
    assert restored.action_rules == config.action_rules
    assert signature(restored) == signature(config)
    restored.action_rules[0].action = "skip"
    assert signature(restored) != signature(config)


def test_unordered_filters_have_stable_snapshots_and_signatures():
    config = LLMConfig(action_rules=[ActionRule("custom", 0, {3, 1, 2}, {"b", "a"}, action="polish")])
    equivalent = deepcopy(config)
    equivalent.action_rules[0].status_filter = frozenset([2, 1, 3])
    equivalent.action_rules[0].label_filter = frozenset(["a", "b"])

    assert execution_snapshot(config) == execution_snapshot(equivalent)
    assert signature(config) == signature(equivalent)


def test_explicit_empty_rules_in_old_task_snapshot_override_new_defaults():
    old_config = LLMConfig(action_rules=[])
    restored = restore_execution_snapshot(LLMConfig(), execution_snapshot(old_config))

    assert restored.action_rules == []
    assert signature(restored) == signature(old_config)
