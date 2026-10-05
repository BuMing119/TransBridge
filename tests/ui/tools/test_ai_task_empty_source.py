from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import ActionRule
from transbridge.ui.tools.ai_translator.scope_presenter import TranslationScope
from transbridge.ui.tools.ai_translator.task_scope import SourceWorkbenchScope, TaskScope, estimate_tasks


@pytest.mark.parametrize("mode", ["translate", "mixed", "polish"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_task_scope_and_estimate_exclude_blank_sources(mode, overwrite):
    entries = [
        TranslationEntry("empty", "empty", "", "", 0, "ARMO:DESC"),
        TranslationEntry("spaces", "spaces", " \t\n", "preserve", 1, "ARMO:DESC"),
        TranslationEntry("normal", "normal", "Armor", "护甲" if mode == "polish" else "", 0, "ARMO:FULL"),
    ]
    slot = SimpleNamespace(label="Demo", esp_path="demo.esp", collection=TranslationEntryCollection(entries))
    ctx = SimpleNamespace(slots={"demo": slot}, active_slot=slot, entry_labels={})
    config = LLMConfig(action_rules=[ActionRule(action="translate")])
    scope = TaskScope(ctx, SourceWorkbenchScope(entries), lambda _: "items")

    tasks = scope.build([slot], TranslationScope(), mode=mode, config=config, overwrite=overwrite)

    assert [entry.key for entry in tasks[0].entries] == ["normal"]
    estimate = estimate_tasks(tasks, config)
    assert "总条目 3" in estimate
    assert "可处理 1" in estimate
    assert "本次任务 1 条" in estimate
